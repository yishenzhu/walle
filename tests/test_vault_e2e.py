import asyncio
import tempfile
import os
from pathlib import Path

from ..vault.store import Store
from ..vault.indexer import Indexer
from ..vault.retriever import Retriever
from ..tools.builtin.note import make_search_notes


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
            search_notes = make_search_notes(Retriever(store, indexer))
            out = await search_notes("怎么连生产库")
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
            search_notes = make_search_notes(Retriever(store, indexer))

            # 外部写入（模拟 MCP 写笔记），不经过我们代码
            (vault / "proj" / "新笔记.md").write_text(
                "# 新笔记\n\n## 要点\n生产库密码是 secret123\n",
                encoding="utf-8",
            )
            out = await search_notes("密码")
            assert "新笔记" in out and "secret123" in out
            await store.close()

    asyncio.run(main())
