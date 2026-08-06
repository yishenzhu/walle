import asyncio

from ..tools.builtin.skill import Skill, create_skill


def test_create_skill_and_load(tmp_path, monkeypatch):
    """创建技能 → 可被 Skill.load 加载为工具。"""
    root = tmp_path / "skills"
    monkeypatch.setattr(Skill, "ROOT_DIR", root)

    async def main():
        out = await create_skill("code-review", "代码审查流程", "## 步骤\n1. 先看 diff\n2. 检查边界条件")
        print(out)
        assert "code-review" in out

        tools = Skill.load(root_dir=root)
        assert len(tools) == 1 and tools[0].name == "code-review"
        assert tools[0].description == "代码审查流程"

    asyncio.run(main())


def test_create_skill_invalid_name(tmp_path, monkeypatch):
    """非法技能名被拒绝（防路径穿越）。"""
    monkeypatch.setattr(Skill, "ROOT_DIR", tmp_path / "skills")

    async def main():
        out = await create_skill("../../evil", "x", "y")
        assert "创建失败" in out
        assert not (tmp_path / "evil").exists()

    asyncio.run(main())
