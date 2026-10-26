"""会话服务端：连接端点 + 监听/会话管理。"""

import asyncio
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..spec import (
    DeltaEnd,
    Error,
    ModelConfig,
    NotificationUnion,
    RequestUnion,
    Sessions,
    UserInput,
)
from .transport import HOST, MAX_FRAME, PORT, FrameTooLargeError, JSONLineConn

logger = logging.getLogger(__name__)

# 请求/回复等待上限（协议语义，不属于帧传输）
CALL_TIMEOUT = 300.0


class SessionConn:
    """一个客户端连接的 Channel 端点。"""

    def __init__(
        self,
        session_id: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        cwd: str | None = None,
        model: ModelConfig | None = None,
        agent: str | None = None,
        call_timeout: float | None = CALL_TIMEOUT,
        link: JSONLineConn | None = None,
    ) -> None:
        self.session_id = session_id
        self.cwd = cwd  # 客户端工作目录（会话 bash 执行位置 / 沙箱可写区）
        self.model = model  # 客户端模型配置（会话级 provider；None = 进程默认）
        self.agent = agent  # 客户端指定 agent 名（None = default）
        self._link = link or JSONLineConn(reader, writer)
        self._pending: dict[str, asyncio.Future] = {}
        self._call_timeout = call_timeout
        self._closed = False
        self._stop = asyncio.Event()

    # ── 传输 ──────────────────────────────────────────
    async def send(self, msg: dict) -> None:
        await self._link.send(msg)

    # ── Channel 协议 ──────────────────────────────────
    async def notify(self, notification: NotificationUnion) -> None:
        if self._closed:
            return
        # 本连接归属的会话身份：补全 session_id（上层构造事件时不带，由本连接注入）
        if not await self._link.try_send(
            {
                "type": "notify",
                "data": notification.model_copy(update={"session_id": self.session_id}).model_dump(
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
                    "data": request.model_copy(update={"session_id": self.session_id}).model_dump(
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
        self._stop.set()
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("channel client disconnected"))
        self._pending.clear()
        self._link.close()

    # ── 读循环 ────────────────────────────────────────
    async def run(
        self,
        on_inbound: Callable[[dict], Awaitable[None]],
    ) -> None:
        """读帧分派；reply 内部 resolve，其余帧交给 on_inbound。"""
        stop = asyncio.create_task(self._stop.wait())
        try:
            while not self._closed:
                recv = asyncio.create_task(self._link.recv())
                done, _ = await asyncio.wait(
                    {recv, stop},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stop in done:
                    recv.cancel()
                    await asyncio.gather(recv, return_exceptions=True)
                    break
                try:
                    msg = recv.result()
                except FrameTooLargeError:
                    # 单行超过 MAX_FRAME：拒绝并断开，避免内存被打爆
                    await self.notify(DeltaEnd(reason="frame_too_large"))
                    break
                if msg is None:
                    break
                if msg.get("type") == "reply":
                    self.resolve(msg.get("id", ""), msg.get("data"))
                    continue
                try:
                    await on_inbound(msg)
                except Exception as exc:
                    control = str(msg.get("type") or "").startswith(
                        ("session.", "run.")
                    )
                    logger.warning(f"inbound frame failed: {exc}")
                    await self.notify(
                        Error(
                            message=(
                                f"控制帧失败: {exc}"
                                if control
                                else f"处理失败: {exc}"
                            ),
                            code=getattr(
                                exc,
                                "code",
                                "control_failed" if control else "error",
                            ),
                        )
                    )
        finally:
            stop.cancel()
            await asyncio.gather(stop, return_exceptions=True)


class SessionServer:
    """会话服务端：监听端口，按 hello 创建或 attach 会话。"""

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
        """开始监听，等待客户端连接。"""
        self._server = await asyncio.start_server(
            self._handle_conn, self._host, self._port, limit=MAX_FRAME
        )
        logger.info(f"session server listening on {self._host}:{self._port}")

    async def stop(self) -> None:
        if self._server is not None:
            # 停止监听（不等待已建立连接；各连接的 handler 自行在 finally 关闭）
            self._server.close()
            self._server = None

    async def _client_history(self, session) -> list[dict]:
        """给 attach 的客户端完整对话历史（含工具调用与结果）。"""
        return [
            message.model_dump(mode="json")
            for message in await session.conversation_history()
        ]

    # ── 连接处理：握手 → 建/取会话 → 读循环 ────────────
    async def _handle_conn(self, reader, writer) -> None:
        """每连接一个任务：握手 → 创建/attach 会话 → 读循环。"""
        session_id = ""
        session = None
        conn: SessionConn | None = None
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

            session_id = msg.get("session_id") or f"session-{uuid.uuid4().hex[:12]}"
            attach = bool(msg.get("attach", False))
            plugin_names = msg.get("plugins")  # 可选：本会话要激活的插件名（缺省=全部）
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

            conn = SessionConn(
                session_id,
                reader,
                writer,
                cwd=cwd,
                model=model,
                agent=msg.get("agent") or None,
                call_timeout=call_timeout,
                link=link,
            )

            if attach:
                # resume：取已存在会话，绑定新 transport
                session = self._sessions.get(session_id)
                if session is None:
                    await conn.send(
                        {
                            "type": "error",
                            "code": "session_not_found",
                            "message": f"会话不存在: {session_id}",
                        }
                    )
                    return
                # 先回 welcome（带最近对话）：客户端收到后才开始读 notify 帧
                await conn.send(
                    {
                        "type": "welcome",
                        "session_id": session_id,
                        "history": await self._client_history(session),
                    }
                )
                old = session.transport
                session.attach(conn)
                # 先补发旧 turn 的通知，再通知旧连接退出
                await session.replay(conn)
                if isinstance(old, SessionConn) and old is not conn:
                    await old.notify(
                        Error(
                            message="session attached elsewhere",
                            code="session_taken_over",
                        )
                    )
                    old.close()
                # 显式给了 agent 就切过去：同一个会话可以带着历史换 agent 配置
                if conn.agent:
                    session.set_agent(conn.agent)
                logger.info(f"channel client reattached: {session_id}")
            else:
                # 新会话：sessions.create（按需激活指定插件）
                session = self._sessions.create(conn, plugin_names)
                await session.start()
                # 新建也可能命中磁盘上同 id 的会话（如 Web 重启后恢复），
                # 带上快照让客户端直接接续。
                await conn.send(
                    {
                        "type": "welcome",
                        "session_id": session_id,
                        "history": await self._client_history(session),
                    }
                )
                logger.info(
                    f"channel client connected: {session_id} agent={conn.agent or 'default'}"
                )

            async def on_inbound(frame: dict) -> None:
                """解释非 reply 帧；已被接管的旧连接直接忽略。"""
                if session.transport is not conn:
                    return
                kind = frame.get("type")
                if kind == "input.create":
                    session.submit(
                        UserInput(
                            content=frame.get("content") or "",
                            session_id=session_id,
                        )
                    )
                elif kind == "run.cancel":
                    await session.cancel(reason="user cancelled")
                elif kind == "session.set_agent":
                    session.set_agent(str(frame.get("agent") or "default"))
                elif kind == "session.set_metadata":
                    values = frame.get("values")
                    if not isinstance(values, dict):
                        raise ValueError("session.set_metadata requires values object")
                    session.update_metadata(values)
                else:
                    raise ValueError(f"unknown frame: {kind}")

            await conn.run(on_inbound)
        except (OSError, asyncio.IncompleteReadError, ValueError) as exc:
            logger.debug(f"session conn {session_id} closed: {exc}")
        finally:
            if conn is not None:
                conn.close()
            else:
                link.close()
            if session is not None:
                try:
                    # 按连接身份 detach：已被接管时不动新连接
                    session.detach(conn)
                except Exception as exc:
                    logger.warning(f"session {session_id} detach failed: {exc}")
            await link.wait_closed()
