import asyncio

from ..tools.usage import ToolUsage


def test_record_and_get(tmp_path):
    async def main():
        u = ToolUsage(str(tmp_path / "u.db"))
        await u.record("bash", "执行命令")
        await u.record("bash", "执行命令")
        await u.record("search_notes", "搜索笔记")

        bash = await u.get("bash")
        assert bash["usage_count"] == 2
        assert bash["description"] == "执行命令"
        assert bash["status"] == "hot"

        all_tools = await u.all()
        assert all_tools[0]["name"] == "bash"  # 使用次数多排前
        assert len(all_tools) == 2
        await u.close()

    asyncio.run(main())


def test_record_unknown_returns_none(tmp_path):
    async def main():
        u = ToolUsage(str(tmp_path / "u.db"))
        assert await u.get("nonexistent") is None
        await u.close()

    asyncio.run(main())
