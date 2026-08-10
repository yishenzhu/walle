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
        # 流式：Delta 入队，None 为回合结束哨兵；任务 start 时创建，stop 时取消
        self._stream_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._stream_task: asyncio.Task | None = None
        # 审批：工具调用 id → 等待中的 Future（工具并发执行，按 id 路由）
        self._pending_approvals: dict[str, asyncio.Future[ApprovalRsp]] = {}

    # ── 生命周期 ──────────────────────────────────────
    async def start(self) -> None:
        """注册事件监听并启动长连接（官方 SDK 后台运行，就绪后返回）。

        事件 handler 在 SDK 后台 loop 执行；用 call_soon_threadsafe 把
        消息/审批结果调度回主 loop（闭包捕获 loop，无需实例成员）。
        """
        loop = asyncio.get_running_loop()

        def on_message(msg: InboundMessage) -> None:
            chat_id = msg.conversation.chat_id
            text = (msg.body_text or msg.content_text or "").strip()
            logger.info(f"feishu receive: chat={chat_id} content={text!r}")
            self._chat_id = chat_id
            loop.call_soon_threadsafe(
                self._queue.put_nowait, UserInput(content=text)
            )

        def on_card_action(evt: CardActionEvent) -> None:
            value = evt.action.value if isinstance(evt.action.value, dict) else {}
            tc_id = value.get("tool_call_id") or ""
            decision = value.get("decision")
            if tc_id in self._pending_approvals and decision in ("approve", "reject"):
                fut = self._pending_approvals.pop(tc_id)
                loop.call_soon_threadsafe(
                    fut.set_result,
                    ApprovalRsp(
                        approved=decision == "approve",
                        reason=None if decision == "approve" else "用户拒绝",
                    ),
                )

        self._on_message, self._on_card_action = on_message, on_card_action  # 测试可触发
        self._channel.on(Events.MESSAGE, on_message)
        self._channel.on(Events.CARD_ACTION, on_card_action)
        await self._channel.connect_until_ready()
        # 常驻流任务：producer 阻塞消费队列，有 Delta 才建卡，start 即可安全创建
        self._stream_task = asyncio.create_task(self._run_stream())
        logger.info("feishu channel ready")

    async def stop(self) -> None:
        """关闭通道：取消常驻流任务并断开长连接。幂等，可再次 start()。"""
        if self._stream_task is not None:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
            self._stream_task = None
            # 清空残留增量，避免重启后消费到上一轮的脏数据
            while not self._stream_queue.empty():
                self._stream_queue.get_nowait()
        try:
            await self._channel.disconnect()
        except Exception as err:
            logger.warning(f"feishu disconnect failed: {err}")

    # ── notify：流式 + 工具事件 ───────────────────────
    async def notify(self, n: NotificationUnion) -> None:
        """流式：Delta 写入官方 CardKit 卡片；工具调用/错误独立卡片发送。"""
        try:
            match n:
                case Delta(delta=delta):
                    await self._stream_queue.put(delta)
                case DeltaEnd():
                    await self._stream_queue.put(None)  # None 结束当前卡片流
                case ToolStart(tool_name=name, arguments=args):
                    await self._send_tool_card(name, args)
                case _:
                    text = self._render(n)
                    if text:
                        await self._channel.send(self._chat_id, {"markdown": text})
        except Exception as err:
            logger.warning(f"feishu notify failed: {err}")

    async def _send_tool_card(self, tool_name: str, arguments: dict) -> None:
        """工具调用卡片：🔧 工具名 + 参数，美观展示。"""
        card = (
            new_card()
            .header(title=f"🔧 {tool_name}", template="orange")
            .markdown(self._tool_markdown(tool_name, arguments))
            .build()
        )
        await self._channel.send(self._chat_id, {"card": card.data})

    @staticmethod
    def _tool_markdown(tool_name: str, arguments: dict) -> str:
        """工具名 + 参数 JSON，工具卡 / 审批卡 / 反馈卡共用。"""
        params = truncate(json.dumps(arguments, ensure_ascii=False, indent=2), 1024)
        return f"**{tool_name}**\n```json\n{params}\n```"

    async def _run_stream(self) -> None:
        """常驻流任务：每回合一个官方 stream() 卡片，读到 None 结束，循环新开。
        官方 stream() 阻塞到 producer 返回（读完 None）后自动 finish_streaming_card。
        """
        while True:
            try:
                async def producer(stream) -> None:
                    while (chunk := await self._stream_queue.get()) is not None:
                        await stream.append(chunk)

                await self._channel.stream(self._chat_id, {"markdown": producer})
            except Exception as err:
                logger.warning(f"feishu stream failed: {err}")
                await asyncio.sleep(1)

    @staticmethod
    def _render(n: NotificationUnion) -> str:
        """错误类通知 → 飞书文本。Delta / ToolStart 由各自分支处理。"""
        match n:
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
            case Approval(tool_name=n, arguments=a, tool_call_id=tc_id):
                return await self._approval_card(n, a, tc_id)
            # 其他服务不处理

    async def _approval_card(
        self, tool_name: str, arguments: dict, tool_call_id: str
    ) -> ApprovalRsp:
        """审批：卡片 + ✅/❌ 按钮，等待点击回调。

        工具可并发执行，按钮 value 携带工具调用 id，回调按 id 路由到
        对应 Future。点击后 update_card 替换为已处理状态（保留工具信息）。
        """
        card = (
            new_card()
            .header(title="🔐 工具审批", template="blue")
            .markdown(self._tool_markdown(tool_name, arguments))
            .buttons(
                [
                    {"label": "✅ 通过", "action": {"tool_call_id": tool_call_id, "decision": "approve"}, "style": "primary"},
                    {"label": "❌ 拒绝", "action": {"tool_call_id": tool_call_id, "decision": "reject"}, "style": "danger"},
                ]
            )
            .build()
        )
        result = await self._channel.send(self._chat_id, {"card": card.data})  # SDK 期望 dict，非 CardPayload 对象
        message_id = getattr(result, "message_id", None) or ""
        fut: asyncio.Future[ApprovalRsp] = asyncio.get_running_loop().create_future()
        self._pending_approvals[tool_call_id] = fut
        try:
            rsp = await fut
            if message_id:
                await self._update_card_feedback(message_id, tool_name, arguments, rsp.approved)
            return rsp
        finally:
            self._pending_approvals.pop(tool_call_id, None)

    async def _update_card_feedback(
        self, message_id: str, tool_name: str, arguments: dict, approved: bool
    ) -> None:
        """审批后更新卡片为已处理状态，保留工具调用信息。失败仅告警。"""
        try:
            template, text = ("green", "✅ 已通过") if approved else ("red", "❌ 已拒绝")
            card = (
                new_card()
                .header(title=text, template=template)
                .markdown(self._tool_markdown(tool_name, arguments))
                .build()
            )
            await self._channel.update_card(message_id, card.data)
        except Exception as err:
            logger.warning(f"feishu update card failed: {err}")
