"""内置工具扩展：注册 bash/ask_user/background/job_result/read/edit/write/
define_tool/history/new_window，由 main 组装进扩展加载器随会话激活。

技能(Skill)不是工具，由 Agent 拼 system prompt、模型用 read 加载。
"""

from ...infra import ExtensionAPI, Tool
from .ask_user import ask_user
from .bash import bash
from .defined import define_tool
from .edit import edit
from .history import history, new_window
from .job import background, job_result
from .read import read
from .write import write


async def builtin_ext(api: ExtensionAPI) -> None:
    """内置工具扩展：注册全部随代码内置的工具（用户扩展可同名覆盖）。"""
    for fn in (
        bash,
        ask_user,
        background,
        job_result,
        read,
        edit,
        write,
        define_tool,
        history,
        new_window,
    ):
        api.register_tool(Tool.from_function(fn))
