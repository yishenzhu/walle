"""飞书通道：官方 lark-channel-sdk（应用机器人，CardKit 流式）。

模式 B：FeishuChannel 实现 Channel，作为主交互通道。
  - notify：流式回复用官方 channel.stream()（CardKit 卡片，无 20 次编辑限制）
  - call：从长连接事件队列取用户消息（Receive / Inquiry / Approval）
工具事件精简：只发 ToolStart（🔧）与错误，不发 ToolResult 详情。
审批用卡片按钮（✅/❌），点击回调驱动 ApprovalRsp。
"""
import asyncio
import json
import logging
import uuid
from typing import Any

import lark_channel
from lark_channel import CardActionEvent, Events, InboundMessage, new_card

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
    """飞书交互通道：官方 SDK 长连接接收消息，CardKit 卡片流式回复。

    - notify：Delta 流式写入卡片（官方 stream），工具事件精简独立发送
    - call(Receive)：从消息队列取下一条用户消息
    - call(Approval)：卡片按钮 ✅/❌，等待点击回调
    """

    def __init__(self, app_id: str, app_secret: str):
        self._channel = lark_channel.FeishuChannel(app_id=app_id, app_secret=app_secret)
        self._queue: asyncio.Queue[UserInput] = asyncio.Queue()
        self._chat_id: str = ""          # 当前会话 chat_id
        self._loop: asyncio.AbstractEventLoop | None = None
        # 流式：官方 stream() 的 producer 通过队列消费 Delta（None = 结束信号）
        self._stream_queue: asyncio.Queue[str | None] | None = None
        # 审批：token → 等待中的 Future（工具并发执行，需按 token 区分）
        self._pending_approvals: dict[str, asyncio.Future[ApprovalRsp]] = {}

    # ── 生命周期 ──────────────────────────────────────
    async def start(self) -> None:
        """注册事件监听并启动长连接（官方 SDK 后台运行，就绪后返回）。

        事件 handler 在 SDK 后台 loop 执行；跨线程用 call_soon_threadsafe
        把消息/审批结果调度回主 loop 的队列。
        """
        self._loop = asyncio.get_running_loop()
        self._channel.on(Events.MESSAGE, self._on_message)
        self._channel.on(Events.CARD_ACTION, self._on_card_action)
        await self._channel.connect_until_ready()
        logger.info("feishu channel ready")

    def _on_message(self, msg: InboundMessage) -> None:
        """收到用户消息（SDK 后台 loop）。"""
        chat_id = msg.conversation.chat_id
        text = (msg.body_text or msg.content_text or "").strip()
        logger.info(f"feishu receive: chat={chat_id} content={text!r}")
        self._chat_id = chat_id
        self._loop.call_soon_threadsafe(
            self._queue.put_nowait, UserInput(content=text)
        )

    def _on_card_action(self, evt: CardActionEvent) -> None:
        """审批卡片按钮回调（SDK 后台 loop）。"""
        value = evt.action.value if isinstance(evt.action.value, dict) else {}
        token = value.get("approval") or ""
        decision = value.get("decision")
        if token in self._pending_approvals and decision in ("approve", "reject"):
            fut = self._pending_approvals.pop(token)
            self._loop.call_soon_threadsafe(
                fut.set_result,
                ApprovalRsp(
                    approved=decision == "approve",
                    reason=None if decision == "approve" else "用户拒绝",
                ),
            )

    # ── notify：流式 + 工具事件 ───────────────────────
    async def notify(self, n: NotificationUnion) -> None:
        """流式：Delta 写入官方 CardKit 卡片；工具事件精简独立发送。"""
        try:
            match n:
                case Delta(delta=delta):
                    await self._stream_delta(delta)
                case DeltaEnd():
                    await self._stream_end()
                case _:
                    text = self._render(n)
                    if text:
                        await self._channel.send(self._chat_id, {"markdown": text})
        except Exception as err:
            logger.warning(f"feishu notify failed: {err}")

    async def _stream_delta(self, delta: str) -> None:
        """流式增量：入队给官方 stream() 的 producer（首个 Delta 启动）。"""
        if self._stream_queue is None:
            self._stream_queue = asyncio.Queue()
            asyncio.create_task(self._stream())
        await self._stream_queue.put(delta)

    async def _stream_end(self) -> None:
        """回合结束：发送结束信号，官方 stream 自动 finish_streaming_card。"""
        if self._stream_queue is not None:
            await self._stream_queue.put(None)
            self._stream_queue = None

    async def _stream(self) -> None:
        """驱动官方 stream()：producer 从队列消费 Delta，None 结束信号收尾。

        官方内部处理：CardKit 预分配、发送引用消息、节流更新（100ms/50字符）、
        正常或出错时自动 finish_streaming_card。
        """
        queue = self._stream_queue  # 捕获稳定引用（_stream_end 会置 None）

        async def producer(stream) -> None:
            while (chunk := await queue.get()) is not None:
                await stream.append(chunk)

        try:
            await self._channel.stream(self._chat_id, {"markdown": producer})
        except Exception as err:
            logger.warning(f"feishu stream failed: {err}")
        finally:
            if self._stream_queue is queue:  # 失败时清理，防后续 Delta 入死队列
                self._stream_queue = None

    @staticmethod
    def _render(n: NotificationUnion) -> str:
        """工具事件 → 飞书文本。Delta 由 _stream 处理，不走这里。

        精简：只渲染 ToolStart（🔧 参数截断）与错误；ToolResult 详情静默。
        """
        match n:
            case ToolStart(tool_name=name, arguments=args):
                params = json.dumps(args, ensure_ascii=False)
                return f"🔧 **{name}**\n```{truncate(params, 512)}```"
            case ToolResult(tool_call_id=tc_id, error=err) if err is not None:
                return f"❌ 工具错误\n```{err}```"
            case Error(message=msg):
                return f"⚠️ 错误\n```{msg}```"
        return ""

    # ── call：交互 ────────────────────────────────────
    async def call(self, s: ServiceUnion) -> Any:
        match s:
            case Receive():
                return await self._queue.get()
            case Inquiry(question=q, options=opts):
                await self._channel.send(self._chat_id, {"markdown": f"❓ {q}"})
                return (await self._queue.get()).content or ""
            case Approval(tool_name=n, arguments=a):
                return await self._approval_card(n, a)
            # 其他服务不处理

    async def _approval_card(self, tool_name: str, arguments: dict) -> ApprovalRsp:
        """审批：卡片 + ✅/❌ 按钮，等待点击回调。

        工具可并发执行，每张审批卡带唯一 token，回调按 token 路由到对应 Future。
        """
        token = uuid.uuid4().hex
        card = (
            new_card()
            .header(title="🔐 工具审批", template="blue")
            .markdown(f"**{tool_name}**")
            .markdown(
                f"```json\n{truncate(json.dumps(arguments, ensure_ascii=False, indent=2), 1024)}\n```"
            )
            .buttons(
                [
                    {"label": "✅ 通过", "action": {"approval": token, "decision": "approve"}, "style": "primary"},
                    {"label": "❌ 拒绝", "action": {"approval": token, "decision": "reject"}, "style": "danger"},
                ]
            )
            .build()
        )
        await self._channel.send(self._chat_id, {"card": card})
        fut: asyncio.Future[ApprovalRsp] = self._loop.create_future()
        self._pending_approvals[token] = fut
        try:
            return await fut
        finally:
            self._pending_approvals.pop(token, None)
