"""飞书通道：应用机器人（长连接 + 真流式）。

模式 B：FeishuChannel 实现 Channel，作为主交互通道。
  - notify：流式回复通过「创建消息 → 更新消息」实现打字机效果
  - call：从长连接事件队列取用户消息（Receive / Inquiry / Approval）
模式 A 的 FeishuObserver 已移除（webhook 无法流式，演进到应用机器人）。
"""
import asyncio
import json
import logging
from typing import Any

import httpx

from ..schemas import (
    ApprovalRsp,
    Delta,
    DeltaEnd,
    Error,
    NotificationUnion,
    Receive,
    Inquiry,
    Approval,
    ServiceUnion,
    ToolResult,
    ToolStart,
    UserInput,
)
from .channel import truncate

logger = logging.getLogger(__name__)

API_BASE = "https://open.feishu.cn/open-apis"
CREATE_MSG = f"{API_BASE}/im/v1/messages"
UPDATE_MSG = f"{API_BASE}/im/v1/messages/{{message_id}}"


class FeishuChannel:
    """飞书交互通道：长连接接收用户消息，流式更新发送 AI 回复。

    - notify：首条 Delta 创建消息（打字机起点），后续 Delta 更新同一消息
    - 工具事件（ToolStart/ToolResult/Error）独立发送
    - call(Receive)：从消息队列取下一条用户消息
    """

    def __init__(self, app_id: str, app_secret: str):
        self._app_id = app_id
        self._app_secret = app_secret
        self._queue: asyncio.Queue[UserInput] = asyncio.Queue()
        self._chat_id: str = ""          # 当前会话 chat_id
        self._msg_id: str | None = None  # 进行中的流式消息（None = 新回合）
        self._msg_text: str = ""      # 流式累积文本
        self._token: str | None = None
        self._http = httpx.AsyncClient(timeout=10.0)
        self._ws_task: asyncio.Task | None = None

    async def _send(self, chat_id: str, text: str) -> str:
        """发送一条独立消息；返回 message_id（流式起点用）。"""
        payload = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        }
        resp = await self._http.post(
            CREATE_MSG,
            params={"receive_id_type": "chat_id"},
            headers=await self._headers(),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()["data"]["message_id"]

    async def _headers(self) -> dict:
        """取 tenant_access_token（缓存）。"""
        if self._token is None:
            resp = await self._http.post(
                f"{API_BASE}/auth/v3/tenant_access_token/internal",
                json={"app_id": self._app_id, "app_secret": self._app_secret},
            )
            resp.raise_for_status()
            self._token = resp.json()["tenant_access_token"]
        return {"Authorization": f"Bearer {self._token}"}

    # ── 发送 ──────────────────────────────────────────
    async def _update(self, message_id: str, text: str) -> None:
        """更新已发送消息（打字机效果）。"""
        payload = {"msg_type": "text", "content": json.dumps({"text": text})}
        resp = await self._http.put(
            UPDATE_MSG.format(message_id=message_id),
            headers=await self._headers(),
            json=payload,
        )
        resp.raise_for_status()

    # ── notify：流式渲染 ──────────────────────────────
    async def notify(self, n: NotificationUnion) -> None:
        """流式：首条 Delta 建消息，后续更新；工具事件独立发。"""
        try:
            match n:
                case Delta(delta=delta):
                    self._msg_text += delta
                    if self._msg_id is None:
                        self._msg_id = await self._send(self._chat_id, self._msg_text)
                    else:
                        await self._update(self._msg_id, self._msg_text)
                case DeltaEnd():
                    self._msg_id = None  # 回合定格，新回合重新创建
                    self._msg_text = ""
                case _:
                    text = self._render(n)
                    if text:
                        await self._send(self._chat_id, text)
        except Exception as err:
            logger.warning(f"feishu notify failed: {err}")

    # ── call：交互 ────────────────────────────────────
    @staticmethod
    def _render(n: NotificationUnion) -> str:
        """工具事件 → 飞书文本（Delta 由 notify 处理，不走这里）。"""
        match n:
            case ToolStart(tool_name=name, arguments=args):
                return f"🔧 调用工具 **{name}**\n```\n{args}\n```"
            case ToolResult(tool_call_id=tc_id, result=result, error=None):
                return f"✅ 工具结果\n{truncate(result, 512)}"
            case ToolResult(tool_call_id=tc_id, error=err):
                return f"❌ 工具错误: {err}"
            case Error(message=msg):
                return f"⚠️ 错误: {msg}"
        return ""

    async def call(self, s: ServiceUnion) -> Any:
        match s:
            case Receive():
                return await self._queue.get()
            case Inquiry(question=q, options=opts):
                await self._send(self._chat_id, f"❓ {q}")
                return (await self._queue.get()).content or ""
            case Approval(tool_name=n, arguments=a):
                await self._send(self._chat_id, f"🔐 审批: {n}({a})? 回复 y/n")
                while True:
                    rsp = (await self._queue.get()).content or ""
                    if rsp.lower() in ("y", "yes"):
                        return ApprovalRsp(approved=True)
                    if rsp.lower() in ("n", "no"):
                        return ApprovalRsp(approved=False, reason=rsp)
            # 其他服务不处理

    # ── 生命周期 ──────────────────────────────────────
    async def start(self) -> None:
        """启动长连接事件订阅（后台线程 + asyncio 队列）。"""
        import lark_oapi as lark

        def on_message(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
            event = data.event
            chat_id = event.message.chat_id
            content = json.loads(event.message.content).get("text", "")
            self._chat_id = chat_id
            self._queue.put_nowait(UserInput(content=content.strip()))

        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message)
            .build()
        )
        ws = lark.ws.Client(self._app_id, self._app_secret, event_handler=event_handler)
        self._ws_task = asyncio.create_task(asyncio.to_thread(ws.start))

    async def close(self) -> None:
        if self._ws_task:
            self._ws_task.cancel()
        await self._http.aclose()
