"""pytest 共享 fixture 与 mock 对象。"""
from dataclasses import dataclass, field
from typing import Any

import pytest

from ..conf import ApprovalConfig, ApprovalDecision, RawRule, ToolConfig
from ..core import ToolExecutor
from ..infra import OpenAIProvider
from ..schemas import Approval, ApprovalRsp, Inquiry, Receive, UserInput
from ..infra import SessionView


# ── Mock LLM 响应对象 ──────────────────────────────────

class FakeUsage:
    def __init__(self, prompt_tokens=10, completion_tokens=5, total_tokens=15):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.function = FakeFunction(name, arguments)

    def model_dump(self):
        return {
            "id": self.id,
            "function": {"name": self.function.name, "arguments": self.function.arguments},
        }


class FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeChoice:
    def __init__(self, message):
        self.message = message


class FakeCompletion:
    def __init__(self, message, usage=None):
        self.choices = [FakeChoice(message)]
        self.usage = usage or FakeUsage()


class FakeCompletions:
    """可预编程的 chat.completions mock（记录每次调用收到的工具名）。"""

    def __init__(self):
        self._responses: list[FakeCompletion] = []
        self._call_count = 0
        self.seen_tools: list[list[str]] = []  # 每次 create 传入的工具名

    def set_responses(self, *responses: FakeCompletion):
        self._responses = list(responses)
        self._call_count = 0
        self.seen_tools = []

    async def create(self, **kwargs):
        if self._call_count >= len(self._responses):
            raise RuntimeError("No more mock responses")
        self.seen_tools.append(
            [t["function"]["name"] for t in kwargs.get("tools") or []]
        )
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp


class FakeClient:
    def __init__(self):
        self.chat = type("FakeChat", (), {})()
        self.chat.completions = FakeCompletions()


class FakeProvider:
    """不依赖真实 API 的 OpenAIProvider mock。

    与真实 provider 同构：Runner 只经 create / stream / model 访问。
    client 保留仅供测试预编程响应（set_responses）。
    """

    def __init__(self, model="test-model"):
        self.client = FakeClient()
        self.model = model

    async def create(self, **kwargs):
        return await self.client.chat.completions.create(**kwargs)

    def stream(self, **kwargs):
        raise NotImplementedError("FakeProvider.stream 未实现（测试无流式场景）")

    @staticmethod
    def set_default(provider):
        OpenAIProvider._default = provider


# ── Mock Channel ───────────────────────────────────────

class FakeChannel:
    """无 I/O 的 Channel 实现，记录所有通知事件，服务返回预设响应。"""

    def __init__(self):
        self.events: list = []  # 记录 notify 的通知
        self._approval_response = ApprovalRsp(approved=True)
        self._inquiry_response = "mock answer"

    async def notify(self, notification):
        self.events.append(notification)

    async def call(self, service):
        match service:
            case Receive():
                return UserInput()
            case Inquiry():
                return self._inquiry_response
            case Approval():
                return self._approval_response

    def set_approval(self, approved: bool, reason: str | None = None):
        self._approval_response = ApprovalRsp(approved=approved, reason=reason)


# ── Fixtures ───────────────────────────────────────────

@pytest.fixture
def fake_provider():
    p = FakeProvider()
    FakeProvider.set_default(p)
    yield p
    FakeProvider.set_default(None)


@pytest.fixture
def fake_channel():
    return FakeChannel()


@pytest.fixture
def allow_all_config():
    return ApprovalConfig(
        rules=[],
        default=ApprovalDecision.ALLOW,
    )


@pytest.fixture
def executor(allow_all_config):
    return ToolExecutor(ToolConfig(approval=allow_all_config))


@dataclass
class FakeSession:
    """满足 SessionView 的测试替身：工具执行期可见的会话状态。"""

    channel: Any = None
    jobs: dict = field(default_factory=dict)
    cwd: str | None = None
    history: Any = None
    ext_runner: Any = None
    bus: Any = None


def make_session(
    *,
    channel=None,
    jobs=None,
    cwd=None,
    history=None,
    ext=None,
    bus=None,
) -> FakeSession:
    """构造测试用会话视图（tool_context 注入它）。"""
    return FakeSession(
        channel=channel,
        jobs=jobs if jobs is not None else {},
        cwd=cwd,
        history=history,
        ext_runner=ext,
        bus=bus,
    )


@pytest.fixture
def tool_context(fake_channel):
    return make_session(channel=fake_channel)
