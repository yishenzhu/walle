"""CLI 服务端：CLIConnection(连接端点) + CLIServer(监听/会话管理), JSON-line 协议。

会话由注入的 sessions 管理。CLIConnection 是该连接的 Channel 端点
（实现 notify/call，收发 JSON-line 帧）；CLIServer 监听 / accept，握手后
经 sessions 新建或 attach 会话，读循环的输入直接喂给该会话；
连接断开 detach 保留（状态跨连接存活）。真正销毁走 sessions 显式 remove。
"""

import asyncio
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ...spec import (
    DeltaEnd,
    Error,
    ModelConfig,
    NotificationUnion,
    RequestUnion,
    Sessions,
    UserInput,
)
from ..transport import HOST, MAX_FRAME, PORT, FrameTooLargeError, JSONLineConn

logger = logging.getLogger(__name__)

# 请求/回复等待上限（协议语义，不属于帧传输）
CALL_TIMEOUT = 300.0


class CLIConnection:
    """一个 CLI 客户端连接的 Channel 端点。

    notify 发帧给客户端；call 走 request/reply（uuid id 路由 pending
    Future）；读循环把 input 帧交给注入的 on_input（由 CLIServer 绑定
    本连接所属会话，消息天然属于本会话）。帧读写委托给 JSONLineConn。
    """

    def __init__(
        self,
        chat_id: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        cwd: str | None = None,
        model: ModelConfig | None = None,
        agent: str | None = None,
        course_id: str | None = None,
        lesson_id: str | None = None,
        call_timeout: float | None = CALL_TIMEOUT,
        link: JSONLineConn | None = None,
    ) -> None:
        self.chat_id = chat_id
        self.cwd = cwd  # 客户端工作目录（会话 bash 执行位置 / 沙箱可写区）
        self.model = model  # 客户端模型配置（会话级 provider；None = 进程默认）
        self.agent = agent  # 客户端指定 agent 名（None = default）
        self.course_id = course_id  # 课程答疑上下文
        self.lesson_id = lesson_id  # 当前课节
        self._link = link or JSONLineConn(reader, writer)
        self._pending: dict[str, asyncio.Future] = {}
        self._call_timeout = call_timeout
        self._closed = False
        self._handover = False  # 接管时保留 worker，让 run 继续

    @property
    def pending_count(self) -> int:
        """尚未答复的服务请求数（审批 / ask_user 等）。"""
        return sum(1 for fut in self._pending.values() if not fut.done())

    def handover(self) -> None:
        """接管标记：连接关闭时不取消正在执行的 input worker。"""
        self._handover = True

    # ── 传输 ──────────────────────────────────────────
    async def send(self, msg: dict) -> None:
        await self._link.send(msg)

    # ── Channel 协议 ──────────────────────────────────
    async def notify(self, notification: NotificationUnion) -> None:
        if self._closed:
            return
        # 本连接归属的会话身份：补全 chat_id（上层构造事件时不带，由本连接注入）
        if not await self._link.try_send(
            {
                "type": "notify",
                "data": notification.model_copy(update={"chat_id": self.chat_id}).model_dump(
                    mode="json"
                ),
            }
        ):
            self.close()

    async def call(self, request: RequestUnion) -> Any:
        req_id = f"req-{uuid.uuid4().hex}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            await self._link.send(
                {
                    "type": "call",
                    "id": req_id,
                    "data": request.model_copy(update={"chat_id": self.chat_id}).model_dump(
                        mode="json"
                    ),
                }
            )
            if self._call_timeout is None:
                return await fut
            return await asyncio.wait_for(fut, self._call_timeout)
        except TimeoutError:
            raise TimeoutError(
                f"no reply for '{request.type}' within {self._call_timeout}s"
            ) from None
        finally:
            self._pending.pop(req_id, None)

    def resolve(self, req_id: str, data) -> None:
        fut = self._pending.get(req_id)
        if fut is not None and not fut.done():
            fut.set_result(data)

    def close(self) -> None:
        """连接断开：fail 所有挂起的 call，并关闭写端。"""
        if self._closed:
            return
        self._closed = True
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("cli client disconnected"))
        self._pending.clear()
        self._link.close()

    async def wait_closed(self) -> None:
        await self._link.wait_closed()

    # ── 读循环 ────────────────────────────────────────
    async def run(
        self,
        on_input: Callable[[str], Awaitable[None]],
        on_cancel: Callable[[], Awaitable[None]] | None = None,
        on_agent: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """读帧：input → on_input；reply → resolve；cancel → on_cancel；
        set_agent → on_agent（会话内切换 agent，不断开连接）。

        input 不直接 await on_input，而是入队由独立 worker 串行消费：读循环
        保持活跃，agent 运行期间 reply 帧（审批 / 提问的回复）仍能被读取。
        否则 on_input（含完整 agent run）会占住读循环，reply 无人处理，
        双向交互工具（ask_user / 审批）会死锁——agent 等 reply，读循环等 agent。
        """
        queue: asyncio.Queue[str] = asyncio.Queue()
        worker = asyncio.create_task(self._consume(on_input, queue))
        try:
            while not self._closed:
                try:
                    msg = await self._link.recv()
                except FrameTooLargeError:
                    # 单行超过 MAX_FRAME：拒绝并断开，避免内存被打爆
                    await self.notify(DeltaEnd(reason="frame_too_large"))
                    break
                if msg is None:
                    break
                t = msg.get("type")
                if t == "input":
                    queue.put_nowait(msg.get("content") or "")
                elif t == "reply":
                    self.resolve(msg.get("id", ""), msg.get("data"))
                elif t == "cancel" and on_cancel is not None:
                    await on_cancel()
                elif t == "set_agent" and on_agent is not None:
                    try:
                        await on_agent(str(msg.get("agent") or "default"))
                    except Exception as exc:
                        logger.warning(f"agent switch failed: {exc}")
                        await self.notify(
                            Error(
                                message=f"切换 agent 失败: {exc}",
                                code="agent_switch_failed",
                            )
                        )
        finally:
            if not self._handover:
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass  # 连接断开：中断仍在处理的 input

    async def _consume(
        self,
        on_input: Callable[[str], Awaitable[None]],
        queue: asyncio.Queue[str],
    ) -> None:
        """串行消费 input 帧；单条消息处理失败仅记录并通知客户端，不影响后续消息。"""
        while True:
            content = await queue.get()
            try:
                await on_input(content)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"input handling failed: {exc}")
                # 客户端在发送 input 后会等回复完成（delta_end）再提示下一行；
                # 处理失败时必须主动通知客户端，否则其会一直等待而"卡住"。
                try:
                    await self.notify(
                        Error(
                            message=f"处理失败: {exc}",
                            code=getattr(exc, "code", "error"),
                        )
                    )
                except Exception:
                    pass  # 对端已断开：无法通知，忽略


class CLIServer:
    """CLI 服务端：监听端口，会话由注入的 sessions 管理。

    握手 hello 帧：
      - 带 attach=true + chat_id → 取已有会话 attach（resume）
      - 否则 → sessions.create(conn) 新建会话（绑定本连接为 transport）
    连接断开 → detach 保留会话（messages 状态跨连接存活），
    会话留在 sessions 供重连 attach。真正销毁走 sessions 显式 remove+close。
    """

    def __init__(
        self,
        sessions: Sessions,
        host: str = HOST,
        port: int = PORT,
    ) -> None:
        self._sessions = sessions
        self._host = host
        self._port = port
        self._server: asyncio.AbstractServer | None = None

    # ── 生命周期 ──────────────────────────────────────
    async def start(self) -> None:
        """开始监听，等待 CLI 客户端连接。"""
        self._server = await asyncio.start_server(
            self._handle_conn, self._host, self._port, limit=MAX_FRAME
        )
        logger.info(f"cli channel listening on {self._host}:{self._port}")

    async def stop(self) -> None:
        if self._server is not None:
            # 停止监听（不等待已建立连接；各连接的 handler 自行在 finally 关闭）
            self._server.close()
            self._server = None

    async def _history_snapshot(self, session, limit: int = 20) -> list[dict]:
        """给 attach 的客户端最近对话快照（system / tool 不展示）。"""
        messages = await session.recent_messages(limit)
        return [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant") and m.content
        ]

    # ── 连接处理：握手 → 建/取会话 → 读循环 ────────────
    async def _handle_conn(self, reader, writer) -> None:
        """每连接一个任务：握手（hello 帧）→ 新建或 attach 会话 → 读循环。

        特殊帧 list：浏览会话列表后立即断开（不进入会话循环）。
        断开时 detach 保留会话（状态跨连接存活），不销毁。
        """
        chat_id = ""
        session = None
        conn: CLIConnection | None = None
        link = JSONLineConn(reader, writer)
        try:
            msg = await link.recv()
            if msg is None:
                return

            # 浏览帧：返回元数据后关闭，不进入会话循环
            if msg.get("type") == "list":
                await link.send(
                    {"type": "list", "sessions": self._sessions.list()}
                )
                return
            if msg.get("type") == "list_workspaces":
                await link.send(
                    {
                        "type": "workspaces",
                        "current": os.getcwd(),
                        "workspaces": self._sessions.list_workspaces(),
                    }
                )
                return
            if msg.get("type") == "list_sessions":
                cwd = msg.get("cwd") or ""
                if not cwd or not Path(cwd).expanduser().is_dir():
                    await link.send(
                        {
                            "type": "error",
                            "code": "invalid_cwd",
                            "message": f"工作目录不存在: {cwd}",
                        }
                    )
                    return
                await link.send(
                    {
                        "type": "sessions",
                        "cwd": cwd,
                        "sessions": self._sessions.list_sessions(cwd),
                    }
                )
                return

            chat_id = msg.get("chat_id") or f"cli-{uuid.uuid4().hex[:12]}"
            attach = bool(msg.get("attach", False))
            extension_names = msg.get("extensions")  # 可选：本会话要激活的扩展名（缺省=全部）
            cwd = msg.get("cwd")  # 客户端工作目录（新会话的 bash/沙箱基准）
            # 交互等待上限：缺省 CALL_TIMEOUT；显式 null = 不设上限
            # （Web 端人机审批用；连接断开会 fail 挂起请求，页面另有停止按钮）
            call_timeout: float | None = CALL_TIMEOUT
            if "call_timeout" in msg:
                raw_timeout = msg.get("call_timeout")
                if raw_timeout is None:
                    call_timeout = None
                else:
                    try:
                        call_timeout = float(raw_timeout)
                    except (TypeError, ValueError):
                        logger.warning(f"ignoring invalid call_timeout: {raw_timeout}")
            # 客户端模型配置（可选；非法/缺失回退进程默认 provider）
            model = None
            raw_model = msg.get("model")
            if isinstance(raw_model, dict):
                try:
                    model = ModelConfig.model_validate(raw_model)
                except Exception:
                    logger.warning(f"ignoring invalid model config: {raw_model}")

            conn = CLIConnection(
                chat_id,
                reader,
                writer,
                cwd=cwd,
                model=model,
                agent=msg.get("agent") or None,
                course_id=msg.get("course_id") or None,
                lesson_id=msg.get("lesson_id") or None,
                call_timeout=call_timeout,
                link=link,
            )

            if attach:
                # resume：取已存在会话，绑定新 transport
                session = self._sessions.get(chat_id)
                if session is None:
                    await conn.send(
                        {
                            "type": "error",
                            "code": "session_not_found",
                            "message": f"会话不存在: {chat_id}",
                        }
                    )
                    return
                # 先回 welcome（带最近对话）：客户端收到后才开始读 notify 帧
                await conn.send(
                    {
                        "type": "welcome",
                        "chat_id": chat_id,
                        "history": await self._history_snapshot(session),
                    }
                )
                old = session.transport
                session.attach(conn)
                # 接管：旧连接断开。学习模式的生成 run 若无挂起交互，
                # 允许继续跑（新连接接收后续事件），避免切页中断课程。
                if isinstance(old, CLIConnection) and old is not conn:
                    resumable = (
                        session.agent_name == "learning"
                        and old.pending_count == 0
                    )
                    if not resumable:
                        await session.cancel(reason="session reattached")
                    else:
                        old.handover()
                    await old.notify(
                        Error(
                            message="session attached elsewhere",
                            code="session_taken_over",
                        )
                    )
                    old.close()
                    if resumable:
                        logger.info(f"learning run continues after reattach: {chat_id}")
                # 显式给了 agent 就切过去：同一个会话可以带着历史换 agent 配置
                if conn.agent:
                    session.set_agent(conn.agent)
                # 课程页切换课节后重连：更新当前课节上下文
                if conn.course_id is not None:
                    session.set_course_context(conn.course_id, conn.lesson_id)
                logger.info(f"cli client reattached: {chat_id}")
            else:
                # 新会话：sessions.create（按需激活指定扩展）
                session = self._sessions.create(conn, extension_names)
                # 新建也可能命中磁盘上同 id 的会话（如 Web 重启后恢复），
                # 带上快照让客户端直接接续。
                await conn.send(
                    {
                        "type": "welcome",
                        "chat_id": chat_id,
                        "history": await self._history_snapshot(session),
                    }
                )
                logger.info(
                    f"cli client connected: {chat_id} agent={conn.agent or 'default'}"
                )

            async def on_input(content: str) -> None:
                """本连接输入 → 本会话处理（chat_id 已绑定）。"""
                await session.handle(UserInput(content=content, chat_id=chat_id))

            async def on_cancel() -> None:
                await session.cancel(reason="user cancelled")

            async def on_agent(name: str) -> None:
                session.set_agent(name)

            await conn.run(on_input, on_cancel, on_agent)
        except (OSError, asyncio.IncompleteReadError, ValueError) as exc:
            logger.debug(f"cli conn {chat_id} closed: {exc}")
        finally:
            if conn is not None:
                conn.close()
            else:
                link.close()
            if session is not None:
                try:
                    # 按连接身份 detach：旧连接不会误伤新连接
                    session.detach(conn)
                except Exception as exc:
                    logger.warning(f"session {chat_id} detach failed: {exc}")
            await link.wait_closed()
