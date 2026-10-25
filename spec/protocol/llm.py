"""模型能力协议：Runner 只依赖本面调用 LLM。"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLM(Protocol):
    @property
    def model(self) -> str: ...

    def set_model(self, model: str) -> str: ...

    async def create(self, **kwargs) -> Any: ...

    def stream(self, **kwargs) -> Any: ...
