"""内置扩展：把随代码内置的工具（bash/ask_user/background/job_result/read/
define_tool）以扩展声明形式导出，由 main 组装进扩展加载器，随会话激活。

技能(Skill)不是工具，由 Agent 拼 system prompt、模型用 read 加载，不在此注册。
"""

from ...infra import ExtensionAPI, Tool
from .ask_user import ask_user
from .bash import bash
from .defined import define_tool
from .job import background, job_result
from .read import read


async def builtin_ext(api: ExtensionAPI) -> None:
    """内置工具扩展：注册全部随代码内置的工具（用户扩展可同名覆盖）。"""
    for fn in (bash, ask_user, background, job_result, read, define_tool):
        api.register_tool(Tool.from_function(fn))
