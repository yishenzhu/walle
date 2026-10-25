"""命令服务测试：注册 / 查找 / 执行 / 别名 / 错误结果。"""

import pytest

from ..core.commands import Commands, parse_command
from ..spec import Command, CommandRequest, CommandResult


class Marker:
    pass


class FakeContext:
    def __init__(self, services=None):
        self._services = services or {}

    def get(self, service):
        return self._services[service]


async def test_parse_command():
    assert parse_command("/review main") == ("review", "main")
    assert parse_command("/review") == ("review", "")
    assert parse_command("普通消息") is None
    assert parse_command("/Review") is None


async def test_register_find_list_and_alias():
    async def handler(request, ctx):
        return CommandResult(text=request.raw_input)

    commands = Commands()
    command = Command(
        name="review",
        description="审查当前分支",
        handler=handler,
        aliases=("r",),
        input_hint="branch",
    )
    off = commands.register(command)
    assert commands.find("review") is command
    assert commands.find("r") is command
    assert commands.list()[0].input_hint == "branch"

    with pytest.raises(ValueError):
        commands.register(Command(name="r", description="dup", handler=handler))

    off()
    assert commands.find("review") is None
    assert commands.find("r") is None


async def test_execute_passes_request_and_context():
    seen = {}

    async def handler(request: CommandRequest, ctx):
        seen["id"] = request.command_id
        seen["raw"] = request.raw_input
        return CommandResult(text=f"{request.raw_input}:{ctx.get(Marker)}")

    commands = Commands()
    commands.register(Command(name="review", description="review", handler=handler))
    result = await commands.execute("/review main", FakeContext({Marker: "x"}))
    assert result == CommandResult(text="main:x")
    assert seen["raw"] == "main"
    assert seen["id"].startswith("cmd-")


async def test_execute_normalizes_none_and_string_and_errors():
    async def silent(request, ctx):
        return None

    async def text(request, ctx):
        return "done"

    async def boom(request, ctx):
        raise RuntimeError("boom")

    commands = Commands()
    commands.register(Command(name="silent", description="s", handler=silent))
    commands.register(Command(name="text", description="t", handler=text))
    commands.register(Command(name="boom", description="b", handler=boom))

    assert await commands.execute("/silent", FakeContext()) == CommandResult()
    assert await commands.execute("/text", FakeContext()) == CommandResult(text="done")
    assert await commands.execute("/boom", FakeContext()) == CommandResult(
        kind="error", text="boom"
    )
    assert await commands.execute("普通消息", FakeContext()) is None
    assert await commands.execute("/missing", FakeContext()) is None


async def test_commands_are_instance_isolated():
    async def handler(request, ctx):
        return CommandResult(text=request.raw_input)

    one, two = Commands(), Commands()
    one.register(Command(name="one", description="1", handler=handler))
    two.register(Command(name="two", description="2", handler=handler))
    assert one.find("two") is None
    assert two.find("one") is None
