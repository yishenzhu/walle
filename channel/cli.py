"""CLI 通道：服务端（CLIChannel）+ 客户端（CLIClient），JSON-line 协议。

服务端：每连接即一个会话。CLIConn 是该连接的 Channel 端点（实现
notify/call，收发 JSON-line 帧）；CLIChannel 只负责监听 / accept，
握手后经 session_factory 创建会话（持本连接作为传输），读循环的输入
直接喂给该会话，连接断开即会话结束。
客户端：python -m walle.channel.cli 连接服务端交互（独立进程）。
"""
import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from ..schemas import NotificationUnion, ServiceUnion, UserInput

logger = logging.getLogger(__name__)

# CLI 终端渲染颜色（客户端复用）
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"

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
    ) -> None:
        self.chat_id = chat_id
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
        await self.send({"type": "notify", "data": n.model_dump(mode="json")})

    async def call(self, s: ServiceUnion) -> Any:
        req_id = f"req-{uuid.uuid4().hex}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self.send(
            {"type": "call", "id": req_id, "data": s.model_dump(mode="json")}
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
        """读帧：input → on_input（本连接消息）；reply → resolve 对应 request。"""
        while line := await self.read_line():
            if not line.strip():
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = msg.get("type")
            if t == "input":
                await on_input(msg.get("content") or "")
            elif t == "reply":
                self.resolve(msg.get("id", ""), msg.get("data"))


class CLIChannel:
    """CLI 服务端：监听端口，每连接一个会话（连接即会话）。

    握手后经 session_factory 创建会话（持本连接作为传输）；读循环输入
    直接喂该会话；连接断开即会话结束（close）。
    """

    def __init__(
        self,
        session_factory: Callable[[CLIConn], Any],
        host: str = HOST,
        port: int = PORT,
    ) -> None:
        self._session_factory = session_factory
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
            self._server.close()   # 3.12+：停止监听并等待挂起连接完成
            self._server = None

    # ── 连接处理：握手 → 建会话 → 读循环 ──────────────
    async def _handle_conn(self, reader, writer) -> None:
        """每连接一个任务：握手（hello 帧带 chat_id）→ 创建会话 → 读循环。"""
        chat_id = ""
        session = None
        try:
            line = await reader.readline()
            if not line:
                return
            hello = json.loads(line)
            chat_id = hello.get("chat_id") or f"cli-{uuid.uuid4().hex[:12]}"

            conn = CLIConn(chat_id, reader, writer)
            session = self._session_factory(conn)

            async def on_input(content: str) -> None:
                """本连接输入 → 本会话处理（chat_id 已绑定）。"""
                await session.handle(UserInput(content=content, chat_id=chat_id))

            await conn.send({"type": "welcome", "chat_id": chat_id})
            logger.info(f"cli client connected: {chat_id}")
            await conn.run(on_input)
        except (json.JSONDecodeError, ConnectionError, asyncio.IncompleteReadError) as exc:
            logger.debug(f"cli conn {chat_id} closed: {exc}")
        finally:
            if session is not None:
                try:
                    await session.close()
                except Exception as exc:
                    logger.warning(f"session {chat_id} close failed: {exc}")
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass   # 对端已断开：wait_closed 可能抛 BrokenPipeError，属预期


# ── 客户端（独立进程）─────────────────────────────────

class CLIClient:
    """CLI 客户端：连接服务端，stdin 发送 + socket 渲染回复（独立进程）。

    用法：python -m walle.channel.cli（连接参数固定为 HOST / PORT）
    双循环：stdin → input 帧；socket notify → 渲染，call → 交互后回 reply。
    """

    def __init__(self, host: str = HOST, port: int = PORT):
        self._host = host
        self._port = port
        self._chat_id = f"cli-{uuid.uuid4().hex[:12]}"
        self._reply_done = asyncio.Event()   # 回复完成（delta_end）信号

    @staticmethod
    def render_notification(data: dict) -> None:
        """渲染服务端 notify 帧（Delta 流式 / 工具事件 / 错误）。"""
        t = data.get("type")
        if t == "delta":
            print(data.get("delta", ""), end="", flush=True)
        elif t == "delta_end":
            print()
        elif t == "tool_start":
            print(f"  {CYAN}🔧 {data.get('tool_name')}{RESET} {data.get('arguments')}", flush=True)
        elif t == "tool_result":
            tc, err = data.get("tool_call_id"), data.get("error")
            if err:
                print(f"  {RED}❌ {tc}{RESET} {err}", flush=True)
            else:
                print(f"  {GREEN}✅ {tc}{RESET} {data.get('result')}", flush=True)
        elif t == "error":
            print(f"  {RED}⚠️ {data.get('message')}{RESET}", flush=True)

    async def run(self) -> None:
        """连接服务端，握手后双循环收发。"""
        reader, writer = await asyncio.open_connection(self._host, self._port)
        await self._send(writer, {"type": "hello", "chat_id": self._chat_id})
        print(f"已连接 {self._host}:{self._port}（会话 {self._chat_id}，Ctrl+C 退出）")
        try:
            await asyncio.gather(
                self._read_stdin(writer),
                self._read_socket(reader, writer),
            )
        finally:
            writer.close()
            await writer.wait_closed()

    # ── 双循环 ────────────────────────────────────────
    async def _read_stdin(self, writer) -> None:
        """stdin → input 帧；发送后等回复完成再提示下一个（避免提示符与回复混排）。"""
        while True:
            try:
                content = (await asyncio.to_thread(input, "You> ")).strip()
            except (EOFError, KeyboardInterrupt, asyncio.CancelledError):
                return   # EOF / Ctrl+C / 取消：优雅退出，不打 traceback
            if content:
                await self._send(
                    writer,
                    {"type": "input", "content": content, "chat_id": self._chat_id},
                )
                self._reply_done.clear()
                await self._reply_done.wait()

    async def _read_socket(self, reader, writer) -> None:
        """socket 帧：notify → 渲染；delta_end 置回复完成；call → 交互并回 reply。"""
        while line := await reader.readline():
            if not line:
                self._reply_done.set()   # 断开：解除等待，避免挂起
                return
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "notify":
                data = msg["data"]
                self.render_notification(data)
                if data.get("type") == "delta_end":
                    self._reply_done.set()
            elif msg.get("type") == "call":
                reply = await self._handle_call(msg["data"])
                await self._send(
                    writer, {"type": "reply", "id": msg.get("id"), "data": reply}
                )

    async def _handle_call(self, data: dict) -> dict:
        """处理 call 载荷（Inquiry / Approval），返回 reply 数据。"""
        if data.get("type") == "inquiry":
            print(f"  [提问] {data.get('question')}")
            for i, opt in enumerate(data.get("options") or [], 1):
                print(f"    {i}. {opt}")
            return {"content": (await asyncio.to_thread(input, "  回答: ")).strip()}
        if data.get("type") == "approval":
            print(f"  [审批请求] 允许执行: {data.get('tool_name')}({data.get('arguments')})?")
            while True:
                answer = (await asyncio.to_thread(input, "  允许? (y/n): ")).strip().lower()
                if answer in ("y", "yes"):
                    return {"approved": True}
                if answer in ("n", "no"):
                    reason = (await asyncio.to_thread(input, "  拒绝原因(可选): ")).strip()
                    return {"approved": False, "reason": reason or None}
                print("  请输入 y/n")
        return {}

    @staticmethod
    async def _send(writer, msg: dict) -> None:
        writer.write(json.dumps(msg).encode() + b"\n")
        await writer.drain()


if __name__ == "__main__":
    try:
        asyncio.run(CLIClient().run())
    except KeyboardInterrupt:
        print()   # Ctrl+C 优雅退出，不打印栈
