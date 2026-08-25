"""集中存放所有输出 BaseModel。

agent 的 frontmatter 通过 `output_model: <名称>` 引用本文件中的类，
名称与类名一致。模型之间可互相嵌套引用，放一起更自然。
"""

from pydantic import BaseModel, Field


class Summary(BaseModel):
    """摘要模型。"""

    title: str = Field(description="标题")
    points: list[str] = Field(description="要点列表")
