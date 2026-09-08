"""edit 工具测试：局部查找替换（与 read 对称的通用文件编辑）。

note.md 等文件都是普通文件：read 读 → edit 改，无专用 note 工具。
"""

from ..tools.builtin.edit import edit


class TestEdit:
    async def test_replace_fragment(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("## Todo\n- [ ] ship\n- [x] done\n", encoding="utf-8")
        out = await edit(str(f), "- [ ] ship", "- [x] ship")
        assert "edited" in out
        assert f.read_text(encoding="utf-8") == "## Todo\n- [x] ship\n- [x] done\n"

    async def test_append_via_tail_anchor(self, tmp_path):
        """追加新小节：old_text 用文件末尾锚点（普通文件语义）。"""
        f = tmp_path / ".agent" / "note.md"
        f.parent.mkdir(parents=True)
        f.write_text("## Todo\n- task1\n", encoding="utf-8")
        out = await edit(str(f), "- task1", "- task1\n\n## Goal\n- finish")
        assert "edited" in out
        text = f.read_text(encoding="utf-8")
        assert "## Goal" in text
        assert "- task1" in text

    async def test_delete_fragment(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("## Decisions\n- retry=3\n## Todo\n- t1\n", encoding="utf-8")
        out = await edit(str(f), "- retry=3\n", "")
        assert "edited" in out
        assert f.read_text(encoding="utf-8") == "## Decisions\n## Todo\n- t1\n"

    async def test_not_found_error(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("hello\n", encoding="utf-8")
        out = await edit(str(f), "nope", "x")
        assert "not found" in out
        assert f.read_text(encoding="utf-8") == "hello\n"

    async def test_multiple_matches_error(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("same\nsame\n", encoding="utf-8")
        out = await edit(str(f), "same", "other")
        assert "matches 2 times" in out
        assert f.read_text(encoding="utf-8") == "same\nsame\n"

    async def test_missing_file_error(self, tmp_path):
        out = await edit(str(tmp_path / "nope.md"), "a", "b")
        assert "not found" in out

    async def test_empty_old_text_rejected(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("x\n", encoding="utf-8")
        out = await edit(str(f), "", "y")
        assert "old_text must not be empty" in out

    async def test_keeps_unrelated_content(self, tmp_path):
        """只动目标片段，其余原文原样保留（局部替换核心语义）。"""
        f = tmp_path / "note.md"
        original = "## A\n- keep1\n## B\n- change me\n## C\n- keep2\n"
        f.write_text(original, encoding="utf-8")
        await edit(str(f), "- change me", "- changed")
        assert f.read_text(encoding="utf-8") == (
            "## A\n- keep1\n## B\n- changed\n## C\n- keep2\n"
        )

    async def test_ignores_trailing_whitespace(self, tmp_path):
        """模型抄来的 old 丢了行尾空格，仍应命中（降级 1）。"""
        f = tmp_path / "note.md"
        f.write_text("def f():   \n    pass\t\n", encoding="utf-8")
        out = await edit(str(f), "def f():\n    pass", "def g():\n    pass")
        assert "ignored trailing whitespace" in out
        assert f.read_text(encoding="utf-8") == "def g():\n    pass\n"

    async def test_ignores_indentation_and_reindents(self, tmp_path):
        """缩进不匹配时按命中处重排新内容（降级 2）。"""
        f = tmp_path / "note.md"
        f.write_text("class A:\n        def m(self):\n            pass\n", encoding="utf-8")
        out = await edit(
            str(f),
            "def m(self):\n    pass",
            "def m(self):\n    return 1",
        )
        assert "ignored indentation" in out
        assert f.read_text(encoding="utf-8") == (
            "class A:\n        def m(self):\n            return 1\n"
        )

    async def test_preserves_crlf(self, tmp_path):
        """CRLF 文件编辑后仍是 CRLF。"""
        f = tmp_path / "note.md"
        f.write_bytes(b"## A\r\n- old\r\n## B\r\n")
        out = await edit(str(f), "- old", "- new")
        assert "edited" in out
        assert f.read_bytes() == b"## A\r\n- new\r\n## B\r\n"

    async def test_preserves_bom(self, tmp_path):
        """带 BOM 的文件编辑后 BOM 仍在。"""
        f = tmp_path / "note.md"
        f.write_bytes(b"\xef\xbb\xbf- old\n")
        out = await edit(str(f), "- old", "- new")
        assert "edited" in out
        assert f.read_bytes() == b"\xef\xbb\xbf- new\n"

    async def test_crlf_old_text_matches_lf_file(self, tmp_path):
        """old 带 CRLF 而文件是 LF，也能命中（模型输出风格混杂）。"""
        f = tmp_path / "note.md"
        f.write_text("a\nb\n", encoding="utf-8")
        out = await edit(str(f), "a\r\nb", "c\nd")
        assert "edited" in out
        assert f.read_text(encoding="utf-8") == "c\nd\n"

    async def test_fuzzy_match_still_rejects_ambiguous(self, tmp_path):
        """降级匹配命中多处时，仍按歧义拒绝。"""
        f = tmp_path / "note.md"
        f.write_text("  x  \n  x  \n", encoding="utf-8")
        out = await edit(str(f), "x", "y")
        assert "matches 2 times" in out
        assert f.read_text(encoding="utf-8") == "  x  \n  x  \n"
