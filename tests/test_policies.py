"""压缩策略测试。"""
import pytest

from ..schemas import Usage, UserMessage
from ..session.policies import PromptLimitPolicy, CompressionContext, PROMPT_LIMIT


class TestPromptLimitPolicy:
    def test_below_limit_no_hit(self):
        policy = PromptLimitPolicy(limit=1000)
        ctx = CompressionContext(
            items=[UserMessage(content="hi")],
            last_usage=Usage(prompt_tokens=100, completion_tokens=10, total_tokens=110),
        )
        assert policy.hit(ctx) is False

    def test_above_limit_hits(self):
        policy = PromptLimitPolicy(limit=1000)
        ctx = CompressionContext(
            items=[UserMessage(content="hi")],
            last_usage=Usage(prompt_tokens=1001, completion_tokens=10, total_tokens=1011),
        )
        assert policy.hit(ctx) is True

    def test_no_usage_no_hit(self):
        policy = PromptLimitPolicy(limit=1000)
        ctx = CompressionContext(items=[UserMessage(content="hi")], last_usage=None)
        assert policy.hit(ctx) is False

    def test_exact_limit_no_hit(self):
        policy = PromptLimitPolicy(limit=1000)
        ctx = CompressionContext(
            items=[],
            last_usage=Usage(prompt_tokens=1000, completion_tokens=0, total_tokens=1000),
        )
        assert policy.hit(ctx) is False

    def test_negative_limit_raises(self):
        with pytest.raises(ValueError, match="limit must be >= 0"):
            PromptLimitPolicy(limit=-1)

    def test_zero_limit(self):
        policy = PromptLimitPolicy(limit=0)
        ctx = CompressionContext(
            items=[],
            last_usage=Usage(prompt_tokens=1, completion_tokens=0, total_tokens=1),
        )
        assert policy.hit(ctx) is True

    def test_default_limit(self):
        policy = PromptLimitPolicy()
        assert policy.limit == PROMPT_LIMIT
