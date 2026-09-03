"""内置扩展定义：把随代码内置的工具（bash/ask_user/background/job_result/read）
以扩展声明形式导出，由 main 组装进扩展加载器，随会话激活。

技能(Skill)不是工具，由 Agent 拼 system prompt、模型用 read 加载，不在此注册。
"""

from .tool import Tool
from .builtin import ask_user, bash, background, job_result, read


async def load_builtin_extensions(api) -> None:
    """内置工具扩展：注册全部随代码内置的工具（用户扩展可同名覆盖）。"""
    for fn in (bash, ask_user, background, job_result, read):
        api.register_tool(Tool.from_function(fn))
