import asyncio
import tempfile
from pathlib import Path

from ..tools.registry import ToolRegistry
from ..conf import Config, LogConfig


def test_search_notes_registered(monkeypatch, tmp_path):
    async def main():
        # 隔离 .agent：不读真实 mcp.yaml / skills / tools
        monkeypatch.setattr("walle.conf.DOT_AGENT", tmp_path)
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d) / "vault"
            (vault / "proj").mkdir(parents=True)
            (vault / "proj" / "生产库.md").write_text(
                "# 生产库\n\n## 连接方式\n通过 SSH 隧道\n", encoding="utf-8"
            )
            conf = Config(
                log=LogConfig(level="INFO", path="x.log", backup_count=1),
                vault={"enabled": True, "path": str(vault)},
            )
            reg = await ToolRegistry().initialize(conf)
            names = [t.name for t in reg.all_tools()]
            print("TOOLS:", names)
            assert "search_notes" in names, names
            schema = next(t.formatted_schema() for t in reg.all_tools() if t.name == "search_notes")
            print("SCHEMA:", schema)
            assert "query" in schema["function"]["parameters"]["properties"]
            await reg.close()

    asyncio.run(main())
