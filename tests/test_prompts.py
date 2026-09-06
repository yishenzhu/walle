"""提示词资源加载 + SummaryCompressor（经 provider.create 调 LLM）。"""
import pytest

from ..infra import load_prompt
from ..messages import SummaryCompressor
from ..schemas import UserMessage

from .conftest import FakeCompletion, FakeMessage, FakeProvider


def test_load_prompt_reads_md_body():
    """load_prompt 从 .agent/prompts/ 读纯正文。"""
    text = load_prompt("summary")
    assert "对话历史压缩器" in text
    assert not text.startswith("---")  # 纯正文，非 frontmatter


def test_load_prompt_missing_raises():
    with pytest.raises(FileNotFoundError):
        load_prompt("no_such_prompt")


async def test_summary_compressor_uses_provider_create():
    """SummaryCompressor 经 provider.create 调 LLM，返回摘要文本。"""
    provider = FakeProvider()
    provider.client.chat.completions.set_responses(
        FakeCompletion(FakeMessage(content="summarized"))
    )
    comp = SummaryCompressor()

    out = await comp([UserMessage(content="hi")], provider=provider)

    assert out == "summarized"
    # provider.create 被调用（mock 侧计数）
    assert provider.client.chat.completions._call_count == 1


async def test_summary_compressor_failure_returns_none(monkeypatch):
    """LLM 抛异常时返回 None（不毒化调用方）。"""
    provider = FakeProvider()

    async def boom(**kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(provider, "create", boom)
    comp = SummaryCompressor()

    out = await comp([UserMessage(content="hi")], provider=provider)
    assert out is None


async def test_summary_compressor_no_provider_returns_none():
    """无 provider 时返回 None（跳过压缩）。"""
    comp = SummaryCompressor()
    out = await comp([UserMessage(content="hi")])
    assert out is None
