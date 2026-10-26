"""技能（Skill）插件 + Agent 技能提示词拼接测试。"""


from ..core import Agent
from ..infra import Skill


def _make_skill(root, name: str, description: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n正文指令。",
        encoding="utf-8",
    )


def test_skill_scan_returns_meta_with_path(tmp_path, monkeypatch):
    """SkillLoader.scan 返回技能声明（name/description/path），不是工具。"""
    from ..tools import skill as skill_mod

    monkeypatch.setattr(skill_mod, "AGENT_DIR", tmp_path)
    _make_skill(tmp_path / "skills", "grilling", "Grill the user")
    _make_skill(tmp_path / "skills", "code-review", "Review code")

    metas = skill_mod.SkillLoader.scan()
    assert len(metas) == 2
    m = metas[0]
    assert isinstance(m, Skill)
    assert m.name == "code-review"
    assert m.path.endswith("code-review/SKILL.md")


def test_skill_scan_empty_dir(tmp_path, monkeypatch):
    from ..tools import skill as skill_mod

    monkeypatch.setattr(skill_mod, "AGENT_DIR", tmp_path)
    assert skill_mod.SkillLoader.scan() == []


def test_skill_scan_skips_broken(tmp_path, monkeypatch):
    """缺 description 的坏技能被跳过，不阻断其余。"""
    from ..tools import skill as skill_mod

    monkeypatch.setattr(skill_mod, "AGENT_DIR", tmp_path)
    bad = tmp_path / "skills" / "bad"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text(
        "---\nname: bad\n---\nno description", encoding="utf-8"
    )
    _make_skill(tmp_path / "skills", "good", "Good skill")

    metas = skill_mod.SkillLoader.scan()
    assert [m.name for m in metas] == ["good"]


class TestAgentSkillPrompt:
    @staticmethod
    def _skills() -> list[Skill]:
        return [
            Skill(
                name="grilling",
                description="Grill the user",
                path="/skills/grilling/SKILL.md",
            ),
            Skill(
                name="review",
                description="Review code",
                path="/skills/review/SKILL.md",
            ),
        ]

    def test_no_skills_returns_empty(self):
        """skills 白名单为空 → 不注入技能提示词。"""
        assert Agent(instruction="x").skill_prompt(self._skills()) == ""

    def test_whitelist_filters(self):
        """skills=["*"] 注入全部；具体名只注入命中项。"""
        all_prompt = Agent(instruction="x", skills=["*"]).skill_prompt(self._skills())
        assert "- grilling: Grill the user" in all_prompt
        assert "- review: Review code" in all_prompt
        assert "SKILL.md" in all_prompt  # 路径可见，供按需加载
        assert "read" not in all_prompt  # 不耦合具体加载工具

        one_prompt = Agent(instruction="x", skills=["grilling"]).skill_prompt(
            self._skills()
        )
        assert "grilling" in one_prompt
        assert "review" not in one_prompt

    def test_empty_skills_list_returns_empty(self):
        """会话无可用技能（空列表）→ 不注入。"""
        assert Agent(instruction="x", skills=["*"]).skill_prompt([]) == ""

    def test_description_with_special_chars(self):
        """description 含特殊字符不影响纯文本清单。"""
        skills = [
            Skill(
                name="x",
                description="uses <code> & more",
                path="/s/x/SKILL.md",
            )
        ]
        prompt = Agent(instruction="x", skills=["*"]).skill_prompt(skills)
        assert "uses <code> & more" in prompt  # 原样保留，无需转义

    def test_whitelist_no_match_returns_empty(self):
        """白名单命中不到任何技能 → 空串。"""
        assert (
            Agent(instruction="x", skills=["nope"]).skill_prompt(self._skills()) == ""
        )


async def test_skill_plugin_registers_into_session(tmp_path, monkeypatch):
    """技能插件：扫描 .agent/skills/，注册到会话技能表。"""
    from ..infra import SkillTable
    from ..plugin import PluginHost
    from ..spec import SkillService
    from ..tools import skill as skill_mod

    monkeypatch.setattr(skill_mod, "AGENT_DIR", tmp_path)
    _make_skill(tmp_path / "skills", "grilling", "Grill the user")
    _make_skill(tmp_path / "skills", "review", "Review code")

    table = SkillTable()
    host = PluginHost()
    await host.root.register(SkillService, table)
    try:
        entries = await host.launch([skill_mod.native_skill])
        assert entries[0].state.value == "active"
        skills = {s.name: s for s in table.list()}
        assert set(skills) == {"grilling", "review"}
        assert skills["grilling"].path.endswith("grilling/SKILL.md")
    finally:
        await host.dispose()
