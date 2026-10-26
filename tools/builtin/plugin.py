"""内置工具插件：向 ToolService 注册内置工具。"""

from ...infra import Tool
from ...plugin import plugin
from ...spec import ToolService
from .ask_user import ask_user
from .bash import bash
from .canvas import canvas
from .defined_tool import define_tool
from .edit import edit
from .history import history, new_window
from .job import background, job_result
from .read import read
from .write import write

# 内置工具注册顺序（唯一来源）
BUILTIN_TOOLS = (
    bash,
    ask_user,
    canvas,
    background,
    job_result,
    read,
    edit,
    write,
    define_tool,
    history,
    new_window,
)


@plugin(name="builtin", inject=(ToolService,))
async def builtin_plugin(ctx, config) -> None:
    """Built-in tool plugin: register all built-in tools with ToolService."""
    tools = ctx.get(ToolService)
    for fn in BUILTIN_TOOLS:
        tools.add(Tool.from_function(fn))
