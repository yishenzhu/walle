"""CLI 客户端：独立进程交互（CLIClient），连接 channel.server 的 CLIServer。

用法：python -m walle.channel.cli [--attach <session_id>] [--list]。
双循环：stdin 行 → input 帧；socket notify → 渲染，call → 交互后回 reply。
"""

import asyncio
import json
import os
import readline  # noqa: F401  # 副作用导入：启用 input() 的行编辑/历史
import uuid

from pydantic import TypeAdapter, ValidationError

from ...spec import (
    ApprovalRequest,
    Delta,
    DeltaEnd,
    Error,
    Inquiry,
    NotificationUnion,
    RequestUnion,
    ToolResult,
    ToolStart,
)
from ..transport import HOST, MAX_FRAME, PORT, FrameTooLargeError, JSONLineConn

# CLI 终端渲染颜色
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"

# notify/call 载荷按 type 判别为具体模型（与 schemas 判别联合一致）
NotificationAdapter = TypeAdapter(NotificationUnion)
RequestAdapter = TypeAdapter(RequestUnion)


class CLIClient:
    """CLI 客户端：连接服务端，stdin 发送 + socket 渲染回复（独立进程）。

    用法：python -m walle.channel.cli [--attach <session_id>] [--list]
    双循环：stdin → input 帧；socket notify → 渲染，call → 交互后回 reply。
    """

    def __init__(
        self,
        host: str = HOST,
        port: int = PORT,
        attach: str = "",
        extensions: list[str] | None = None,
        dir: str | None = None,
        model: dict | None = None,
        agent: str = "",
    ):
        self._host = host
        self._port = port
        self._chat_id = attach or f"cli-{uuid.uuid4().hex[:12]}"
        self._attach = bool(attach)
        self._extensions = extensions  # 可选：新会话要激活的扩展名
        self._dir = dir or os.getcwd()  # 工作目录（会话 bash/沙箱基准；默认客户端启动目录）
        self._model = model  # 可选：本会话模型配置（api_key/base_url/model）
        self._agent = agent  # 可选：新会话使用的 agent 名（缺省由服务端用 default）
        self._reply_done = asyncio.Event()  # 回复完成（delta_end）信号
        self._quit = asyncio.Event()  # 退出信号：EOF / 服务端断开 / 本地 /exit

    @staticmethod
    def _render_error(code: str, message: str) -> None:
        text = f"{code}: {message}" if code else message
        print(f"  {RED}⚠️ {text}{RESET}", flush=True)

    @staticmethod
    def render_notification(notification: NotificationUnion) -> None:
        """渲染服务端 notify 帧（Delta 流式 / 工具事件 / 错误）。"""
        if isinstance(notification, Delta):
            print(notification.delta, end="", flush=True)
        elif isinstance(notification, DeltaEnd):
            print()
            if notification.reason:
                text = (
                    f"{notification.reason}: {notification.detail}"
                    if notification.detail
                    else notification.reason
                )
                print(f"  {RED}⚠️ {text}{RESET}", flush=True)
        elif isinstance(notification, ToolStart):
            print(
                f"  {CYAN}🔧 {notification.tool_name}{RESET} "
                f"{notification.arguments}",
                flush=True,
            )
        elif isinstance(notification, ToolResult):
            if notification.error:
                print(
                    f"  {RED}❌ {notification.tool_call_id}{RESET} "
                    f"{notification.error}",
                    flush=True,
                )
            else:
                print(
                    f"  {GREEN}✅ {notification.tool_call_id}{RESET} "
                    f"{notification.result}",
                    flush=True,
                )
        elif isinstance(notification, Error):
            CLIClient._render_error(notification.code, notification.message)

    @classmethod
    def render_history(cls, messages: list[dict]) -> None:
        """渲染 attach 时的最近对话快照（server 已过滤 system / tool）。"""
        if not messages:
            return
        print(f"  {CYAN}── 历史 {len(messages)} 条 ──{RESET}")
        for m in messages:
            who = "You" if m["role"] == "user" else "AI"
            print(f"  {who}> {m['content']}")
        print(f"  {CYAN}────────────────{RESET}")

    async def run(self) -> None:
        """连接服务端，握手（attach 或新建）后双循环收发。

        退出只有一条路：_quit 置位。EOF、服务端断开、本地 /exit 都归一为它，
        事件循环自然收尾；stdin 读线程是 daemon，不阻塞进程退出。
        """
        reader, writer = await asyncio.open_connection(
            self._host, self._port, limit=MAX_FRAME
        )
        link = JSONLineConn(reader, writer)
        hello = {
            "type": "hello",
            "chat_id": self._chat_id,
            "attach": bool(self._attach),
            "cwd": self._dir,  # 客户端工作目录（服务端建新会话时作为会话 cwd）
        }
        if self._extensions:
            hello["extensions"] = self._extensions
        if self._model:
            hello["model"] = self._model  # 客户端模型配置（服务端建会话级 provider）
        if self._agent:
            hello["agent"] = self._agent  # 新会话使用的 agent（缺省 default）
        await link.send(hello)
        welcome = await link.recv()
        if welcome is None:
            print("服务端未响应")
            link.close()
            await link.wait_closed()
            return
        if welcome.get("type") == "error":
            self._render_error(welcome.get("code", ""), welcome.get("message", ""))
            link.close()
            await link.wait_closed()
            return
        self.render_history(welcome.get("history") or [])
        mode = "恢复会话" if self._attach else "新会话"
        print(
            f"已连接 {self._host}:{self._port}（{mode} {self._chat_id}，Ctrl+C / /exit 退出）"
        )
        stdin_task = asyncio.create_task(self._read_stdin(link))
        sock_task = asyncio.create_task(self._read_socket(link))
        try:
            await self._quit.wait()  # 任一退出源置位即收尾
        finally:
            # 两个循环都取消：/exit 时 socket 仍开着，只等一个会永远挂起
            stdin_task.cancel()
            sock_task.cancel()
            await asyncio.gather(stdin_task, sock_task, return_exceptions=True)
            link.close()
            await link.wait_closed()

    # ── 双循环 ────────────────────────────────────────
    async def _read_stdin(self, link: JSONLineConn) -> None:
        """stdin → input 帧；发送后等回复完成再提示下一个。

        ⚠️ 提示符交给 input()（readline 才知道它占几列），时序靠这里 await 回复完成。
        """
        while not self._quit.is_set():
            try:
                content = (await asyncio.to_thread(input, "You> ")).strip()
            except (EOFError, KeyboardInterrupt, asyncio.CancelledError):
                self._quit.set()  # EOF / Ctrl+C / 取消：归一退出，不打 traceback
                return
            if content in ("/exit", "/quit"):
                self._quit.set()
                return
            if content:
                await link.send(
                    {"type": "input", "content": content, "chat_id": self._chat_id}
                )
                self._reply_done.clear()
                await self._reply_done.wait()  # 等回复完成再提示下一行

    async def _read_socket(self, link: JSONLineConn) -> None:
        """socket 帧：notify → 渲染；delta_end 置回复完成；call → 交互并回 reply。"""
        try:
            while (msg := await link.recv()) is not None:
                if msg.get("type") == "notify":
                    try:
                        notification = NotificationAdapter.validate_python(msg["data"])
                    except ValidationError:
                        continue
                    self.render_notification(notification)
                    # delta_end：正常回复完成；error：服务端处理失败（如模型限流）。
                    if isinstance(notification, (DeltaEnd, Error)):
                        self._reply_done.set()
                elif msg.get("type") == "call":
                    try:
                        request = RequestAdapter.validate_python(msg["data"])
                    except ValidationError:
                        continue
                    reply = await self._handle_request(request)
                    await link.send(
                        {"type": "reply", "id": msg.get("id"), "data": reply}
                    )
        except FrameTooLargeError:
            print(f"  {RED}⚠️ 服务端帧过大{RESET}", flush=True)
        except OSError as exc:
            print(f"  {RED}⚠️ 连接中断: {exc}{RESET}", flush=True)
        finally:
            self._reply_done.set()  # 断开：解除等回复的挂起
            self._quit.set()  # 归一退出

    @staticmethod
    async def _ask(prompt: str) -> str:
        return (await asyncio.to_thread(input, prompt)).strip()

    @staticmethod
    def _format_arguments(arguments: dict) -> str:
        """格式化工具参数：短参数单行展示，长参数多行缩进并截断。"""
        text = json.dumps(arguments, ensure_ascii=False)
        if len(text) <= 120:
            return text
        lines = json.dumps(arguments, ensure_ascii=False, indent=2).splitlines()
        shown = lines[:12]
        if len(lines) > 12:
            shown.append(f"...（其余 {len(lines) - 12} 行省略）")
        return "\n".join(shown)

    async def _handle_request(self, request) -> dict:
        """处理 call 载荷中的请求（Inquiry / ApprovalRequest），返回 reply 数据。"""
        if isinstance(request, Inquiry):
            print(f"  {CYAN}┌─ ❓ 提问 ─────────────────────────────────────{RESET}")
            print(f"  {CYAN}│{RESET} {request.question}")
            options = request.options or []
            if options:
                print(f"  {CYAN}│{RESET} 选项:")
                for i, opt in enumerate(options, 1):
                    print(f"  {CYAN}│{RESET}   {GREEN}{i}.{RESET} {opt}")
            print(f"  {CYAN}└──────────────────────────────────────────────{RESET}")
            return {"content": await self._ask("  回答: ")}
        if isinstance(request, ApprovalRequest):
            args_text = self._format_arguments(request.arguments)
            print(f"  {CYAN}┌─ 🔐 审批请求 ─────────────────────────────────{RESET}")
            print(f"  {CYAN}│{RESET} 工具: {request.tool_name}")
            print(f"  {CYAN}│{RESET} 参数:")
            for ln in args_text.splitlines():
                print(f"  {CYAN}│{RESET}   {ln}")
            print(f"  {CYAN}└──────────────────────────────────────────────{RESET}")
            while True:
                answer = (
                    await self._ask(f"  {GREEN}允许执行?{RESET} (y=是 / n=否): ")
                ).lower()
                if answer in ("y", "yes"):
                    return {"approved": True}
                if answer in ("n", "no"):
                    reason = await self._ask(f"  {RED}拒绝原因(可选){RESET}: ")
                    return {"approved": False, "reason": reason or None}
                print(f"  {RED}请输入 y 或 n{RESET}")
        return {}

    # ── 会话列表（一次性查询渲染，不持有/不缓存）──────
    @classmethod
    async def list_sessions(cls, host: str = HOST, port: int = PORT) -> None:
        """查询并渲染会话列表后退出。

        只展示元数据（id / attached 状态 / 存活时长），不含任何消息内容；
        一次性拉取渲染，客户端不持有全局会话列表。异常统一兜底报错。
        """
        try:
            reader, writer = await asyncio.open_connection(
                host, port, limit=MAX_FRAME
            )
            link = JSONLineConn(reader, writer)
            try:
                await link.send({"type": "list"})
                msg = await link.recv()
            finally:
                link.close()
                await link.wait_closed()
            sessions = (msg or {}).get("sessions") or []
        except Exception as exc:
            print(f"获取会话列表失败: {exc}")
            return
        print(f"{'会话ID':<28} {'状态':<10} {'存活':>8}")
        print("-" * 50)
        for s in sessions:
            state = "运行中" if s.get("attached") else "空闲"
            age = s.get("age_seconds", 0)
            print(f"{s.get('session_id'):<28} {state:<10} {age:>6.0f}s")
