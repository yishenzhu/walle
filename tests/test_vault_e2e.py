import asyncio
import tempfile
import os
from pathlib import Path

from ..vault.store import Store
from ..vault.indexer import Indexer
from ..vault.retriever import Retriever
from ..vault.writer import NoteWriter
from ..tools.builtin.note import make_search_notes, make_write_note


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


def test_write_note_e2e():
    async def main():
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d) / "vault"
            (vault / "proj").mkdir(parents=True)
            store = Store(os.path.join(d, "v.db"))
            indexer = Indexer(str(vault), store)
            await indexer.full_build()
            write_note = make_write_note(NoteWriter(str(vault), indexer))

            # 正常写入 → 返回相对路径，写入后立即可检索
            rel = await write_note("proj/新笔记.md", "# 新笔记\n\n## 要点\n生产库密码是 secret123\n")
            assert rel == "proj/新笔记.md"
            search_notes = make_search_notes(Retriever(store))
            out = await search_notes("密码")
            assert "新笔记" in out and "secret123" in out

            # 目录穿越被拒绝
            out = await write_note("../evil.md", "x")
            assert "写入失败" in out and not (vault.parent / "evil.md").exists()

            # 写 .obsidian 被拒绝
            out = await write_note(".obsidian/evil.md", "x")
            assert "写入失败" in out
            await store.close()

    asyncio.run(main())
