"""write 工具测试：整文件写入（新建 / 覆盖 / 保真）。"""

from ..tools.builtin.write import write


class TestWrite:
    async def test_create_new_file(self, tmp_path):
        f = tmp_path / "new.md"
        out = await write(str(f), "hello\n")
        assert "created" in out
        assert f.read_text(encoding="utf-8") == "hello\n"

    async def test_creates_parent_dirs(self, tmp_path):
        f = tmp_path / "a" / "b" / "note.md"
        out = await write(str(f), "x\n")
        assert "created" in out
        assert f.read_text(encoding="utf-8") == "x\n"

    async def test_overwrite_existing(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("old\n", encoding="utf-8")
        out = await write(str(f), "new\n")
        assert "updated" in out
        assert f.read_text(encoding="utf-8") == "new\n"

    async def test_empty_content(self, tmp_path):
        f = tmp_path / "empty.md"
        out = await write(str(f), "")
        assert "created" in out
        assert f.read_text(encoding="utf-8") == ""

    async def test_missing_path_error(self):
        out = await write("", "x")
        assert "path is required" in out

    async def test_directory_rejected(self, tmp_path):
        out = await write(str(tmp_path), "x")
        assert "not a file" in out

    async def test_preserves_crlf_on_overwrite(self, tmp_path):
        """覆盖 CRLF 文件时沿用其换行风格。"""
        f = tmp_path / "note.md"
        f.write_bytes(b"old\r\nline\r\n")
        out = await write(str(f), "a\nb\n")
        assert "updated" in out
        assert f.read_bytes() == b"a\r\nb\r\n"

    async def test_preserves_bom_on_overwrite(self, tmp_path):
        """覆盖带 BOM 的文件时 BOM 保留。"""
        f = tmp_path / "note.md"
        f.write_bytes(b"\xef\xbb\xbfold\n")
        out = await write(str(f), "new\n")
        assert "updated" in out
        assert f.read_bytes() == b"\xef\xbb\xbfnew\n"

    async def test_new_file_uses_lf(self, tmp_path):
        """新建文件即使 content 带 CRLF 也写为 LF。"""
        f = tmp_path / "new.md"
        await write(str(f), "a\r\nb\r\n")
        assert f.read_bytes() == b"a\nb\n"
