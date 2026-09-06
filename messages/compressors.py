"""压缩方法：把一段对话消息折叠为摘要文本。

与破坏式压缩（原地改写消息列表）不同，这里的压缩方法只产出摘要文本，
不修改传入的消息——是否/如何落盘由调用方（投影视图）决定。
"""

import logging
import time
from typing import Any, Protocol, runtime_checkable

from ..infra import COMPRESS_DURATION, load_prompt, OpenAIProvider
from ..schemas import Message, UserMessage, SystemMessage

logger = logging.getLogger(__name__)


@runtime_checkable
class Compressor(Protocol):
    """压缩方法协议：可调用对象，把消息段折叠为摘要文本。

    实现 ``__call__(items, **kwargs) -> str | None``。items 是要压缩的
    消息段；其余运行环境（如 provider）经 kwargs 传入，由需要它的实现
    自行取用——纯本地策略（截断等）无需感知 LLM。

    失败时应返回 None（调用方跳过，不破坏会话），而非抛异常。
    """

    async def __call__(
        self, items: list[Message], **kwargs: Any
    ) -> str | None: ...


class SummaryCompressor:
    """默认压缩方法：用传入的 provider 把消息段摘要成纯文本。

    系统提示词来自 .agent/prompts/summary.md（与代码分离，可独立编辑）。
    构造时加载并缓存提示词；非破坏——只返回摘要文本，不改动传入消息。
    provider 缺失或 LLM 调用失败时返回 None。
    """

    def __init__(self):
        self._prompt = load_prompt("summary")

    async def __call__(
        self, items: list[Message], *, provider: OpenAIProvider | None = None
    ) -> str | None:
        if provider is None:
            return None

        prompt = [
            SystemMessage(content=self._prompt),
            UserMessage(content=str(items)),
        ]
        start = time.monotonic()
        try:
            completion = await provider.create(
                messages=[m.model_dump() for m in prompt],
                temperature=0.1,
            )
        except Exception:  # noqa: BLE001 - 摘要失败不毒化调用方
            logger.exception("summarize failed")
            return None
        COMPRESS_DURATION.record((time.monotonic() - start) * 1000)
        return completion.choices[0].message.content
