"""ProjectedMessages 投影视图测试（纯切点截断）。"""

import pytest

from ..spec import Projection, UserMessage, Usage
from ..messages import ProjectedMessages, InMemoryMessages, SQLiteMessages


def make_storage(kind, tmp_path=None):
    if kind == "memory":
        return InMemoryMessages()
    return SQLiteMessages(db_path=str(tmp_path / "test.db"), session_id="test")


def sample_messages():
    return [
        UserMessage(content="u1"),
        UserMessage(content="u2"),
        UserMessage(content="u3"),
        UserMessage(content="u4"),
    ]


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
class TestProjectedMessages:
    async def test_get_without_projection_returns_all(self, kind, tmp_path):
        proj = ProjectedMessages(make_storage(kind, tmp_path))
        await proj.add(sample_messages())
        result = await proj.get()
        assert [m.content for m in result] == ["u1", "u2", "u3", "u4"]

    async def test_projection_cuts_head(self, kind, tmp_path):
        """设切点后：前 cut 条移出模型视野，纯截断不生成摘要。"""
        proj = ProjectedMessages(make_storage(kind, tmp_path))
        await proj.add(sample_messages())
        await proj.set_projection(cut=2)
        result = await proj.get()
        assert [m.content for m in result] == ["u3", "u4"]

    async def test_projection_cut_zero_cuts_nothing(self, kind, tmp_path):
        proj = ProjectedMessages(make_storage(kind, tmp_path))
        await proj.add(sample_messages())
        await proj.set_projection(cut=0)
        result = await proj.get()
        assert [m.content for m in result] == ["u1", "u2", "u3", "u4"]

    async def test_projection_cut_beyond_length_keeps_all(self, kind, tmp_path):
        proj = ProjectedMessages(make_storage(kind, tmp_path))
        await proj.add(sample_messages())
        # 切点超出消息数 → 全部保留（不截断）
        await proj.set_projection(cut=10)
        result = await proj.get()
        assert [m.content for m in result] == ["u1", "u2", "u3", "u4"]

    async def test_clear_projection_restores_raw_view(self, kind, tmp_path):
        proj = ProjectedMessages(make_storage(kind, tmp_path))
        await proj.add(sample_messages())
        await proj.set_projection(cut=2)
        await proj.clear_projection()
        result = await proj.get()
        assert [m.content for m in result] == ["u1", "u2", "u3", "u4"]

    async def test_underlying_keeps_full_raw_messages(self, kind, tmp_path):
        proj = ProjectedMessages(make_storage(kind, tmp_path))
        await proj.add(sample_messages())
        await proj.set_projection(cut=2)
        # 投影只影响 get()；底层原文全量保留
        raw = await proj.underlying.get()
        assert [m.content for m in raw] == ["u1", "u2", "u3", "u4"]

    async def test_add_after_projection_appends_to_tail(self, kind, tmp_path):
        proj = ProjectedMessages(make_storage(kind, tmp_path))
        await proj.add(sample_messages())
        await proj.set_projection(cut=2)
        await proj.add([UserMessage(content="u5")])
        result = await proj.get()
        # 新消息在切点后，原样保留
        assert [m.content for m in result] == ["u3", "u4", "u5"]

    async def test_clear_resets_projection(self, kind, tmp_path):
        proj = ProjectedMessages(make_storage(kind, tmp_path))
        await proj.add(sample_messages())
        await proj.set_projection(cut=2)
        await proj.clear()
        result = await proj.get()
        assert len(result) == 0

    async def test_pop_delegates_to_underlying(self, kind, tmp_path):
        proj = ProjectedMessages(make_storage(kind, tmp_path))
        await proj.add(sample_messages())
        popped = await proj.pop()
        assert popped is not None
        assert popped.content == "u1"

    async def test_add_with_usage_delegates(self, kind, tmp_path):
        proj = ProjectedMessages(make_storage(kind, tmp_path))
        usage = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        await proj.add([UserMessage(content="hi")], usage=usage)
        result = await proj.get()
        assert len(result) == 1

    async def test_get_with_limit_applies_after_projection(self, kind, tmp_path):
        proj = ProjectedMessages(make_storage(kind, tmp_path))
        await proj.add(sample_messages())
        await proj.set_projection(cut=2)
        # 切点后剩 [u3, u4]；limit=2 → 全部
        result = await proj.get(limit=2)
        assert [m.content for m in result] == ["u3", "u4"]

    async def test_get_without_projection_limit_returns_head(self, kind, tmp_path):
        # limit 由投影类统一为「取前 N 条」，不依赖底层不对称语义
        proj = ProjectedMessages(make_storage(kind, tmp_path))
        await proj.add(sample_messages())
        result = await proj.get(limit=2)
        assert [m.content for m in result] == ["u1", "u2"]

    async def test_get_returns_copy_not_internal_state(self, kind, tmp_path):
        proj = ProjectedMessages(make_storage(kind, tmp_path))
        await proj.add(sample_messages())
        result = await proj.get()
        result.append(UserMessage(content="injected"))
        again = await proj.get()
        assert [m.content for m in again] == ["u1", "u2", "u3", "u4"]


async def test_projected_messages_satisfies_projection_protocol():
    """ProjectedMessages 满足 Projection 协议；裸存储不满足。"""
    proj = ProjectedMessages(InMemoryMessages())
    assert isinstance(proj, Projection)
    raw = InMemoryMessages()
    assert not isinstance(raw, Projection)
