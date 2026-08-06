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
            await Indexer(str(vault), store).full_build()
            search_notes = make_search_notes(Retriever(store))
            out = await search_notes("怎么连生产库")
            print(out)
            assert "生产库 / 连接方式" in out and "来源" in out
            await store.close()

    asyncio.run(main())
