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
        """追加新小节：old_string 用文件末尾锚点（普通文件语义）。"""
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

    async def test_empty_old_string_rejected(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("x\n", encoding="utf-8")
        out = await edit(str(f), "", "y")
        assert "old_string must not be empty" in out

    async def test_keeps_unrelated_content(self, tmp_path):
        """只动目标片段，其余原文原样保留（局部替换核心语义）。"""
        f = tmp_path / "note.md"
        original = "## A\n- keep1\n## B\n- change me\n## C\n- keep2\n"
        f.write_text(original, encoding="utf-8")
        await edit(str(f), "- change me", "- changed")
        assert f.read_text(encoding="utf-8") == (
            "## A\n- keep1\n## B\n- changed\n## C\n- keep2\n"
        )
