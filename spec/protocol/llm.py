"""模型能力协议：Runner 只依赖本面调用 LLM。

create（一次性）/ stream（流式）/ model（读当前模型名）为引擎实际使用面；
具体实现（infra.OpenAIProvider / 任意 OpenAI 兼容端点）由装配注入。
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLM(Protocol):
    @property
    def model(self) -> str: ...

    def set_model(self, model: str) -> str: ...

    async def create(self, **kwargs) -> Any: ...

    def stream(self, **kwargs) -> Any: ...
