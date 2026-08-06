import asyncio
import tempfile
import os
from pathlib import Path

from ..vault.store import Store
from ..vault.indexer import Indexer
from ..vault.retriever import Retriever
from ..tools.builtin.note import make_semantic_search


def test_search_notes_e2e():
    async def main():
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d) / "vault"
            (vault / "proj").mkdir(parents=True)
            (vault / "proj" / "生产库.md").write_text(
                "# 生产库\n\n## 连接方式\n通过 SSH 隧道，端口 5432\n",
                encoding="utf-8",
            )
            store = Store(os.path.join(d, "v.db"))
            indexer = Indexer(str(vault), store)
            await indexer.full_build()
            semantic_search = make_semantic_search(Retriever(store, indexer))
            out = await semantic_search("怎么连生产库")
            print(out)
            assert "生产库 / 连接方式" in out and "来源" in out
            await store.close()

    asyncio.run(main())


def test_lazy_refresh_e2e():
    """模拟外部（如 MCP）写入新笔记后，检索前懒刷新能捕捉。"""
    async def main():
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d) / "vault"
            (vault / "proj").mkdir(parents=True)
            store = Store(os.path.join(d, "v.db"))
            indexer = Indexer(str(vault), store)
            await indexer.full_build()
            semantic_search = make_semantic_search(Retriever(store, indexer))

            # 外部写入（模拟 MCP 写笔记），不经过我们代码
            (vault / "proj" / "新笔记.md").write_text(
                "# 新笔记\n\n## 要点\n生产库密码是 secret123\n",
                encoding="utf-8",
            )
            out = await semantic_search("密码")
            assert "新笔记" in out and "secret123" in out
            await store.close()

    asyncio.run(main())


def test_ensure_indexed_incremental():
    """ensure_indexed：首次全量，二次增量（不重建未变文件）。"""
    async def main():
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d) / "vault"
            (vault / "proj").mkdir(parents=True)
            (vault / "proj" / "a.md").write_text("# A\n\n内容 alpha\n", encoding="utf-8")
            store = Store(os.path.join(d, "v.db"))
            indexer = Indexer(str(vault), store)

            await indexer.ensure_indexed()   # 首次 → 全量
            assert await store.is_indexed()

            # 二次 → 增量：新增文件应被索引
            (vault / "proj" / "b.md").write_text("# B\n\n内容 beta\n", encoding="utf-8")
            await indexer.ensure_indexed()
            mtimes = await store.file_mtimes()
            assert "proj/a.md" in mtimes and "proj/b.md" in mtimes
            await store.close()

    asyncio.run(main())


def test_ensure_indexed_rebuild_on_corrupt():
    """ensure_indexed：索引损坏（files 表空）时降级全量重建。"""
    async def main():
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d) / "vault"
            (vault / "proj").mkdir(parents=True)
            (vault / "proj" / "a.md").write_text("# A\n\n内容 gamma\n", encoding="utf-8")
            store = Store(os.path.join(d, "v.db"))
            indexer = Indexer(str(vault), store)
            await indexer.full_build()

            # 模拟索引损坏：清空 files 表（索引"不可信"）
            await store.clear()
            assert not await store.is_indexed()

            # ensure_indexed 应全量重建
            await indexer.ensure_indexed()
            mtimes = await store.file_mtimes()
            assert "proj/a.md" in mtimes
            await store.close()

    asyncio.run(main())
