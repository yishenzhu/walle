from openai import AsyncOpenAI
import os
from typing import Self


class OpenAIProvider:
    """OpenAI 兼容 Provider：封装底层 client，对外只暴露 create / stream / model。

    - `create` / `stream` 内部填充 model，调用方不需要感知模型参数
    - `set_model()` 运行时切换模型（会话中途切换，下一轮立即生效）
    - 底层 client 私有（`_client`），不对外暴露
    """

    _default: Self | None = None

    def __init__(self, api_key: str, base_url: str, model: str):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def set_model(self, model: str) -> str:
        """运行时切换模型，返回切换前的模型名。"""
        old, self.model = self.model, model
        return old

    async def create(self, **kwargs):
        """批量补全：内部填充 model，返回 Completion（含 usage）。"""
        return await self._client.chat.completions.create(model=self.model, **kwargs)

    def stream(self, **kwargs):
        """流式补全：内部填充 model，返回 AsyncStream（async with 消费）。"""
        return self._client.chat.completions.stream(model=self.model, **kwargs)

    @classmethod
    def get_default(cls):
        return cls._default

    @classmethod
    def set_default(cls, provider: Self):
        cls._default = provider

    @classmethod
    def load_env(cls):
        from dotenv import load_dotenv

        load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL")
        model = os.getenv("OPENAI_MODEL")
        if api_key and base_url and model:
            cls.set_default(cls(api_key, base_url, model))
