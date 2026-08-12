import logging
import time
from typing import Protocol, runtime_checkable
from ..infra import OpenAIProvider, tracer, COMPRESS_DURATION
from ..schemas import Message, UserMessage, SystemMessage

logger = logging.getLogger(__name__)


@runtime_checkable
class Compressor(Protocol):
    async def compress(self, items: list[Message]) -> list[Message]: ...


SUMMARY_PROMPT = """你是对话历史压缩器。将给定对话压缩为摘要，作为后续对话的上下文。

## 输出要求
- 直接输出摘要正文，不要任何前言、后记、解释或 Markdown 标题
- 用简洁要点（bullet）组织，每条一个事实
- 保留具体值（人名、数字、日期、ID），禁止模糊化为"某""若干""之前"
- 字段 content 之外的字段（如 reasoning_content）一律忽略

## 保留
- 用户属性与偏好：姓名、习惯、约束
- 用户决定与意图
- 关键结论与最终结果
- 进行中的任务与待办
- 工具调用：只留结果中的关键信息，丢弃调用过程

## 丢弃
- 闲聊与寒暄
- 推理过程（只留结论）
- 重复内容（合并）
- 失败或放弃的尝试
- 工具原始输出
"""


class SummaryCompressor:
    async def compress(self, items: list[Message]) -> list[Message]:
        provider = OpenAIProvider.get_default()
        if not provider or not provider.model:
            return items

        with tracer.start_as_current_span("summary.compress") as span:
            items_count = len(items)
            span.set_attribute("items.count", items_count)

            split_index = next(
                (
                    i
                    for i in range(items_count // 2, items_count)
                    if items[i].role == "user"
                ),
                None,
            )

            if split_index is None:
                return items

            summary_input = [
                SystemMessage(content=SUMMARY_PROMPT),
                UserMessage(content=str(items[:split_index])),
            ]

            start = time.monotonic()
            completion = await provider.client.chat.completions.create(
                model=provider.model,
                messages=[m.model_dump() for m in summary_input],  # type: ignore
                temperature=0.1,
            )
            elapsed_ms = (time.monotonic() - start) * 1000
            COMPRESS_DURATION.record(elapsed_ms)

            summary_text = completion.choices[0].message.content
            logger.debug(f"summary:\n{summary_text}")

            items[:split_index] = [SystemMessage(content=summary_text or "")]

            return items
