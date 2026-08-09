"""飞书通道：应用机器人（长连接 + 真流式）。

模式 B：FeishuChannel 实现 Channel，作为主交互通道。
  - notify：流式回复通过「创建消息 → 更新消息」实现打字机效果
  - call：从长连接事件队列取用户消息（Receive / Inquiry / Approval）
模式 A 的 FeishuObserver 已移除（webhook 无法流式，演进到应用机器人）。
"""
import asyncio
import json
import logging
import threading
import time
from typing import Any

import lark_oapi as lark  # 模块级 import：主线程无 running loop 时绑定模块级 loop，供独立线程跑 ws.start()

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
        self._last_update: float = 0.0  # 上次更新消息时间（节流用）
        self._update_count: int = 0   # 本回合更新次数（飞书限制 20 次/消息）
        self._client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws_thread: threading.Thread | None = None

    async def _send(self, chat_id: str, text: str) -> str:
        """发送一条独立消息（SDK async）；返回 message_id（流式起点用）。"""
        body = lark.im.v1.CreateMessageRequestBody.builder().receive_id(chat_id).msg_type("text").content(
            json.dumps({"text": text}, ensure_ascii=False)
        ).build()
        req = lark.im.v1.CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
        resp = await self._client.im.v1.message.acreate(req)
        if resp.code != 0:
            raise RuntimeError(f"feishu send failed: {resp.code} {resp.msg}")
        return resp.data.message_id

    async def _update(self, message_id: str, text: str) -> None:
        """更新已发送消息（打字机效果，SDK async）。"""
        body = lark.im.v1.UpdateMessageRequestBody.builder().msg_type("text").content(
            json.dumps({"text": text}, ensure_ascii=False)
        ).build()
        req = lark.im.v1.UpdateMessageRequest.builder().message_id(message_id).request_body(body).build()
        resp = await self._client.im.v1.message.aupdate(req)
        if resp.code != 0:
            raise RuntimeError(f"feishu update failed: {resp.code} {resp.msg}")

    # ── notify：流式渲染 ──────────────────────────────
    async def notify(self, n: NotificationUnion) -> None:
        """流式：首条 Delta 建消息，节流更新；工具事件独立发。

        飞书限制一条消息最多编辑 20 次（230072），因此流式更新需同时
        节流（每秒最多一次）和限次（每回合最多 18 次 + 最终 1 次）。
        """
        try:
            match n:
                case Delta(delta=delta):
                    await self._stream_delta(delta)
                case DeltaEnd():
                    if self._msg_id is not None:
                        await self._update(self._msg_id, self._msg_text)  # 最终内容
                    self._msg_id = None  # 回合定格，新回合重新创建
                    self._msg_text = ""
                    self._last_update = 0.0
                    self._update_count = 0
                case _:
                    text = self._render(n)
                    if text:
                        await self._send(self._chat_id, text)
        except Exception as err:
            logger.warning(f"feishu notify failed: {err}")

    async def _stream_delta(self, delta: str) -> None:
        """流式增量：首条创建，后续节流+限次更新。"""
        self._msg_text += delta
        if self._msg_id is None:
            self._msg_id = await self._send(self._chat_id, self._msg_text)
            self._last_update = time.monotonic()
            return
        now = time.monotonic()
        # 每秒最多一次，且每回合最多 18 次（留 1 次给 DeltaEnd 最终更新）
        if now - self._last_update >= 1.0 and self._update_count < 18:
            await self._update(self._msg_id, self._msg_text)
            self._last_update = now
            self._update_count += 1

    # ── call：交互 ────────────────────────────────────
    @staticmethod
    def _render(n: NotificationUnion) -> str:
        """工具事件 → 飞书文本（Delta 由 notify 处理，不走这里）。

        参数 / 结果用 JSON 格式化 + 代码块，飞书端展示整齐。
        """
        match n:
            case ToolStart(tool_name=name, arguments=args):
                params = json.dumps(args, ensure_ascii=False, indent=2)
                return f"🔧 **{name}**\n```{params}```"
            case ToolResult(tool_call_id=tc_id, result=result, error=None):
                body = _to_text(result)
                return f"✅ 工具结果\n```\n{truncate(body, 512)}\n```"
            case ToolResult(tool_call_id=tc_id, error=err):
                return f"❌ 工具错误\n```{err}```"
            case Error(message=msg):
                return f"⚠️ 错误\n```{msg}```"
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
        """启动长连接事件订阅（独立线程跑官方 start() + 跨线程安全通信）。

        lark 的 ws.Client.start() 是同步阻塞式（模块级 loop + run_until_complete），
        必须在独立线程运行，保留其自动重连/异常处理等完整生命周期。
        事件回调在独立线程执行，用 call_soon_threadsafe 调度回主循环的队列。
        """
        self._loop = asyncio.get_running_loop()

        def on_message(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
            event = data.event
            chat_id = event.message.chat_id
            content = json.loads(event.message.content).get("text", "")
            logger.info(f"feishu receive: chat={chat_id} content={content!r}")
            # 跨线程：把消息调度到主循环的 asyncio.Queue
            self._loop.call_soon_threadsafe(
                self._queue.put_nowait, UserInput(content=content.strip())
            )
            self._chat_id = chat_id

        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message)
            .build()
        )
        ws = lark.ws.Client(
            self._app_id,
            self._app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )
        # daemon 线程：进程退出不阻塞（SDK 无公开 stop，start() 会阻塞在 _select）
        self._ws_thread = threading.Thread(target=ws.start, daemon=True)
        self._ws_thread.start()


def _to_text(value: Any) -> str:
    """工具结果 → 文本：dict/list 用 JSON 格式化，其余转字符串。"""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)
