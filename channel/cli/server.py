"""CLI 服务端：CLIConn(连接端点) + CLIChannel(监听/会话管理), JSON-line 协议。

会话由注入的 sessions 管理。CLIConn 是该连接的 Channel 端点
（实现 notify/call，收发 JSON-line 帧）；CLIChannel 监听 / accept，握手后
经 sessions 新建或 attach 会话，读循环的输入直接喂给该会话；
连接断开 detach 保留（状态跨连接存活）。真正销毁走 sessions 显式 remove。
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from ...spec import Error, ModelConfig, NotificationUnion, ServiceUnion, Sessions, UserInput

logger = logging.getLogger(__name__)

# 通道默认连接参数（服务端监听 / 客户端连接共用同一值，避免漂移）
HOST = "127.0.0.1"
PORT = 8899


class CLIConn:
    """一个 CLI 客户端连接的 Channel 端点。

    notify 发帧给客户端；call 走 request/reply（uuid id 路由 pending
    Future）；读循环把 input 帧交给注入的 on_input（由 CLIChannel 绑定
    本连接所属会话，消息天然属于本会话）。
    """

    def __init__(
        self,
        chat_id: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        cwd: str | None = None,
        model: ModelConfig | None = None,
    ) -> None:
        self.chat_id = chat_id
        self.cwd = cwd  # 客户端工作目录（会话 bash 执行位置 / 沙箱可写区）
        self.model = model  # 客户端模型配置（会话级 provider；None = 进程默认）
        self._reader = reader
        self._writer = writer
        self._pending: dict[str, asyncio.Future] = {}

    # ── 传输 ──────────────────────────────────────────
    async def send(self, msg: dict) -> None:
        self._writer.write(json.dumps(msg).encode() + b"\n")
        await self._writer.drain()

    async def read_line(self) -> str:
        """读一行原始帧（空串 = 连接关闭）。"""
        line = await self._reader.readline()
        return line.decode(errors="replace")

    # ── Channel 协议 ──────────────────────────────────
    async def notify(self, n: NotificationUnion) -> None:
        # 本连接归属的会话身份：补全 chat_id（上层构造事件时不带，由本连接注入）
        await self.send(
            {
                "type": "notify",
                "data": n.model_copy(update={"chat_id": self.chat_id}).model_dump(
                    mode="json"
                ),
            }
        )

    async def call(self, s: ServiceUnion) -> Any:
        req_id = f"req-{uuid.uuid4().hex}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self.send(
            {
                "type": "call",
                "id": req_id,
                "data": s.model_copy(update={"chat_id": self.chat_id}).model_dump(
                    mode="json"
                ),
            }
        )
        try:
            return await fut
        finally:
            self._pending.pop(req_id, None)

    def resolve(self, req_id: str, data) -> None:
        fut = self._pending.get(req_id)
        if fut is not None and not fut.done():
            fut.set_result(data)

    def close(self) -> None:
        """连接断开：fail 所有挂起的 call，避免服务端永久挂起。"""
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("cli client disconnected"))
        self._pending.clear()

    # ── 读循环 ────────────────────────────────────────
    async def run(self, on_input: Callable[[str], Awaitable[None]]) -> None:
        """读帧：input → on_input（本连接消息）；reply → resolve 对应 request。

        input 不直接 await on_input，而是入队由独立 worker 串行消费：读循环
        保持活跃，agent 运行期间 reply 帧（审批 / 提问的回复）仍能被读取。
        否则 on_input（含完整 agent run）会占住读循环，reply 无人处理，
        双向交互工具（ask_user / 审批）会死锁——agent 等 reply，读循环等 agent。
        """
        queue: asyncio.Queue[str] = asyncio.Queue()
        worker = asyncio.create_task(self._consume(on_input, queue))
        try:
            while line := await self.read_line():
                if not line.strip():
                    break
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = msg.get("type")
                if t == "input":
                    queue.put_nowait(msg.get("content") or "")
                elif t == "reply":
                    self.resolve(msg.get("id", ""), msg.get("data"))
        finally:
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
                    await self.notify(Error(message=f"处理失败: {exc}"))
                except Exception:
                    pass  # 对端已断开：无法通知，忽略


class CLIChannel:
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
            self._handle_conn, self._host, self._port
        )
        logger.info(f"cli channel listening on {self._host}:{self._port}")

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()  # 3.12+：停止监听并等待挂起连接完成
            self._server = None

    # ── 连接处理：握手 → 建/取会话 → 读循环 ────────────
    async def _handle_conn(self, reader, writer) -> None:
        """每连接一个任务：握手（hello 帧）→ 新建或 attach 会话 → 读循环。

        特殊帧 list：浏览会话列表后立即断开（不进入会话循环）。
        断开时 detach 保留会话（状态跨连接存活），不销毁。
        """
        chat_id = ""
        session = None
        try:
            line = await reader.readline()
            if not line:
                return
            msg = json.loads(line)

            # list 帧：返回会话元数据（id/状态/存活，不含消息内容）后关闭
            if msg.get("type") == "list":
                writer.write(
                    json.dumps(
                        {"type": "list", "sessions": self._sessions.list()}
                    ).encode()
                    + b"\n"
                )
                await writer.drain()
                return

            chat_id = msg.get("chat_id") or f"cli-{uuid.uuid4().hex[:12]}"
            attach = bool(msg.get("attach", False))
            ext_names = msg.get("extensions")  # 可选：本会话要激活的扩展名（缺省=全部）
            cwd = msg.get("cwd")  # 客户端工作目录（新会话的 bash/沙箱基准）
            # 客户端模型配置（可选；非法/缺失回退进程默认 provider）
            model = None
            raw_model = msg.get("model")
            if isinstance(raw_model, dict):
                try:
                    model = ModelConfig.model_validate(raw_model)
                except Exception:
                    logger.warning(f"ignoring invalid model config: {raw_model}")

            conn = CLIConn(chat_id, reader, writer, cwd=cwd, model=model)

            if attach:
                # resume：取已存在会话，绑定新 transport
                session = self._sessions.get(chat_id)
                if session is None:
                    await conn.send(
                        {"type": "error", "message": f"会话不存在: {chat_id}"}
                    )
                    return
                session.attach(conn)
                logger.info(f"cli client reattached: {chat_id}")
            else:
                # 新会话：sessions.create（按需激活指定扩展）
                session = self._sessions.create(conn, ext_names)
                logger.info(f"cli client connected: {chat_id}")

            async def on_input(content: str) -> None:
                """本连接输入 → 本会话处理（chat_id 已绑定）。"""
                await session.handle(UserInput(content=content, chat_id=chat_id))

            await conn.send({"type": "welcome", "chat_id": chat_id})
            await conn.run(on_input)
        except (
            json.JSONDecodeError,
            ConnectionError,
            asyncio.IncompleteReadError,
        ) as exc:
            logger.debug(f"cli conn {chat_id} closed: {exc}")
        finally:
            if session is not None:
                try:
                    # 断开只 detach（保留 messages 供重连），不 close
                    session.detach()
                except Exception as exc:
                    logger.warning(f"session {chat_id} detach failed: {exc}")
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass  # 对端已断开：wait_closed 可能抛 BrokenPipeError，属预期
