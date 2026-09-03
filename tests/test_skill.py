"""技能（Skill）扫描 + Agent 技能提示词拼接测试。"""

import pytest

from ..core import Agent
from ..tools.builtin import Skill, SkillMeta


def _make_skill(root, name: str, description: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n正文指令。",
        encoding="utf-8",
    )


def test_skill_scan_returns_meta_with_path(tmp_path, monkeypatch):
    """Skill.scan 返回元数据（name/description/path），不是工具。"""
    from ..tools.builtin import skill as skill_mod

    monkeypatch.setattr(skill_mod, "DOT_AGENT", tmp_path)
    _make_skill(tmp_path / "skills", "grilling", "Grill the user")
    _make_skill(tmp_path / "skills", "code-review", "Review code")

    metas = Skill.scan()
    assert len(metas) == 2
    m = metas[0]
    assert isinstance(m, SkillMeta)
    assert m.name == "code-review"
    assert m.path.endswith("code-review/SKILL.md")


def test_skill_scan_empty_dir(tmp_path, monkeypatch):
    from ..tools.builtin import skill as skill_mod

    monkeypatch.setattr(skill_mod, "DOT_AGENT", tmp_path)
    assert Skill.scan() == []


def test_skill_scan_skips_broken(tmp_path, monkeypatch):
    """缺 description 的坏技能被跳过，不阻断其余。"""
    from ..tools.builtin import skill as skill_mod

    monkeypatch.setattr(skill_mod, "DOT_AGENT", tmp_path)
    bad = tmp_path / "skills" / "bad"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("---\nname: bad\n---\nno description", encoding="utf-8")
    _make_skill(tmp_path / "skills", "good", "Good skill")

    metas = Skill.scan()
    assert [m.name for m in metas] == ["good"]


class TestAgentSkillPrompt:
    def test_no_skills_returns_empty(self):
        """skills 白名单为空 → 不注入技能提示词。"""
        assert Agent(instruction="x").skill_prompt() == ""

    def test_whitelist_filters(self, tmp_path, monkeypatch):
        """skills=["*"] 注入全部；具体名只注入命中项。"""
        from ..tools.builtin import skill as skill_mod

        monkeypatch.setattr(skill_mod, "DOT_AGENT", tmp_path)
        _make_skill(tmp_path / "skills", "grilling", "Grill the user")
        _make_skill(tmp_path / "skills", "review", "Review code")

        all_prompt = Agent(instruction="x", skills=["*"]).skill_prompt()
        assert "- grilling: Grill the user" in all_prompt
        assert "- review: Review code" in all_prompt
        assert "SKILL.md" in all_prompt  # 路径可见，供按需加载
        assert "read" not in all_prompt  # 不耦合具体加载工具

        one_prompt = Agent(instruction="x", skills=["grilling"]).skill_prompt()
        assert "grilling" in one_prompt
        assert "review" not in one_prompt

    def test_description_with_special_chars(self, tmp_path, monkeypatch):
        """description 含特殊字符不影响纯文本清单。"""
        from ..tools.builtin import skill as skill_mod

        monkeypatch.setattr(skill_mod, "DOT_AGENT", tmp_path)
        _make_skill(tmp_path / "skills", "x", "uses <code> & more")

        prompt = Agent(instruction="x", skills=["*"]).skill_prompt()
        assert "uses <code> & more" in prompt  # 原样保留，无需转义

    def test_whitelist_no_match_returns_empty(self, tmp_path, monkeypatch):
        """白名单命中不到任何技能 → 空串。"""
        from ..tools.builtin import skill as skill_mod

        monkeypatch.setattr(skill_mod, "DOT_AGENT", tmp_path)
        _make_skill(tmp_path / "skills", "grilling", "Grill the user")

        assert Agent(instruction="x", skills=["nope"]).skill_prompt() == ""
