"""Web 对话页与 WebSocket 桥接回归。"""

import asyncio
import importlib
import queue
import threading
import time

from fastapi.testclient import TestClient

from ..channel.transport import JSONLineConn

web = importlib.import_module("walle.channel.web.app")


class FakeAgent:
    """最小 JSON-line agent 服务端：welcome → 审批 call / 终态 notify。"""

    def __init__(self) -> None:
        self.frames: queue.Queue[dict] = queue.Queue()
        self.hello: dict | None = None
        self.port = 0
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> int:
        self._thread.start()
        assert self._ready.wait(5), "fake agent failed to start"
        return self.port

    def stop(self) -> None:
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(timeout=5)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._stop = asyncio.Event()
        try:
            self._loop.run_until_complete(self._main())
        finally:
            self._loop.close()

    async def _main(self) -> None:
        server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = server.sockets[0].getsockname()[1]
        self._ready.set()
        try:
            async with server:
                await self._stop.wait()
        finally:
            server.close()

    async def _handle(self, reader, writer) -> None:
        conn = JSONLineConn(reader, writer)
        try:
            hello = await conn.recv()
            if hello is None:
                return
            self.hello = hello
            await conn.send({"type": "welcome", "chat_id": hello.get("chat_id")})
            while (msg := await conn.recv()) is not None:
                self.frames.put(msg)
                kind = msg.get("type")
                if kind == "input":
                    await conn.send(
                        {
                            "type": "call",
                            "id": "req-1",
                            "data": {
                                "type": "approval",
                                "tool_name": "bash",
                                "arguments": {"cmd": "echo hi"},
                                "tool_call_id": "tc1",
                            },
                        }
                    )
                elif kind == "reply":
                    await conn.send(
                        {"type": "notify", "data": {"type": "delta_end"}}
                    )
                elif kind == "cancel":
                    await conn.send(
                        {
                            "type": "notify",
                            "data": {"type": "delta_end", "reason": "user cancelled"},
                        }
                    )
        finally:
            conn.close()
            await conn.wait_closed()


def wait_frame(agent: FakeAgent, kind: str, timeout: float = 5) -> dict:
    """等待假 agent 收到指定类型的帧（跳过其它帧）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            msg = agent.frames.get(timeout=0.1)
        except queue.Empty:
            continue
        if msg.get("type") == kind:
            return msg
    raise AssertionError(f"no '{kind}' frame received")


def test_index_serves_chat_page():
    response = TestClient(web.app).get("/")
    assert response.status_code == 200
    assert "<title>Walle</title>" in response.text
    assert "/ws/chat" in response.text
    assert "工具审批" in response.text
    assert "停止" in response.text


def test_bridge_forwards_approval_reply_and_cancel(monkeypatch):
    """浏览器 reply / cancel 帧必须原样转到 agent 连接。"""
    agent = FakeAgent()
    port = agent.start()
    monkeypatch.setattr(web, "PORT", port)
    try:
        with TestClient(web.app).websocket_connect("/ws/chat") as ws:
            assert ws.receive_json()["type"] == "welcome"
            # Web 为人机审批通道：交互不设超时（避免用户离开后审批过期）
            assert agent.hello is not None
            assert agent.hello["call_timeout"] is None

            ws.send_json({"type": "input", "content": "run"})
            assert wait_frame(agent, "input")["content"] == "run"

            call = ws.receive_json()
            assert call["type"] == "call"
            assert call["data"]["type"] == "approval"

            ws.send_json(
                {"type": "reply", "id": call["id"], "data": {"approved": True}}
            )
            reply = wait_frame(agent, "reply")
            assert reply["id"] == call["id"]
            assert reply["data"] == {"approved": True}
            assert ws.receive_json()["data"]["type"] == "delta_end"

            ws.send_json({"type": "cancel"})
            wait_frame(agent, "cancel")
            tail = ws.receive_json()
            assert tail["data"] == {
                "type": "delta_end",
                "reason": "user cancelled",
            }
    finally:
        agent.stop()
