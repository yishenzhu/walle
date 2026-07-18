from openai import AsyncOpenAI
import os
from typing import Self


class OpenAIProvider:
    _default: Self | None = None

    def __init__(self, api_key: str, base_url: str, model: str):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model

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
