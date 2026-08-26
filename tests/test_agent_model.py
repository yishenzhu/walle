"""Agent 输出模型（models.yaml）构建与加载测试。

覆盖：
- _build_model: 简写/完整字段、array+items、object 嵌套、default 可选、null、报错
- _load_model: 未提供返回 None、文件缺失、条目不存在

通过 root 参数（tmp_path）隔离，不依赖真实 .agent/。
"""

import pytest
from pydantic import BaseModel

from ..core.agent import Agent


def _write_models_yaml(root, content: str) -> None:
    (root / "models.yaml").write_text(content, encoding="utf-8")


# ── _build_model：字段构建 ───────────────────────────────────────────────────


def test_build_shorthand():
    """简写字段（类型字符串）→ 必填、无默认。"""
    M = Agent._build_model("T", {"title": "str", "age": "int"})
    assert issubclass(M, BaseModel)
    assert M.model_fields["title"].annotation is str
    assert M.model_fields["age"].annotation is int
    assert set(M.model_fields) == {"title", "age"}


def test_build_full_with_desc_and_default():
    """完整字段：desc + default → 可选字段带默认值。"""
    M = Agent._build_model(
        "T", {"name": {"type": "str", "desc": "名字", "default": "匿名"}}
    )
    inst = M()
    assert inst.name == "匿名"
    prop = M.model_json_schema()["properties"]["name"]
    assert prop["description"] == "名字"
    assert prop["default"] == "匿名"
    assert "name" not in M.model_json_schema().get("required", [])


def test_build_array_items():
    """array + items 定元素类型。"""
    M = Agent._build_model("T", {"points": {"type": "array", "items": "str"}})
    assert M.model_fields["points"].annotation == list[str]
    schema = M.model_json_schema()["properties"]["points"]
    assert schema["type"] == "array"
    assert schema["items"] == {"type": "string"}


def test_build_nested_object():
    """object 嵌套递归建子模型。"""
    M = Agent._build_model(
        "T",
        {
            "meta": {
                "type": "object",
                "properties": {"author": {"type": "str"}},
            }
        },
    )
    sub = M.model_fields["meta"].annotation
    assert issubclass(sub, BaseModel)
    assert sub.model_fields["author"].annotation is str


def test_build_null():
    """type: null(YAML None) → NoneType。"""
    M = Agent._build_model("T", {"x": {"type": None}})
    assert M.model_fields["x"].annotation is type(None)


def test_build_union_raises():
    """联合类型 list 暂不支持 → 抛错。"""
    with pytest.raises(ValueError, match="联合类型暂不支持"):
        Agent._build_model("T", {"x": {"type": ["str", "null"]}})


def test_build_unknown_type_raises():
    """未知类型 → 抛错。"""
    with pytest.raises(ValueError, match="未知类型"):
        Agent._build_model("T", {"x": "foobar"})


# ── _load_model：加载注册表 ──────────────────────────────────────────────────


def test_load_model_success(tmp_path):
    """正常加载命名模型。"""
    _write_models_yaml(
        tmp_path,
        "summary:\n  title: str\n  points:\n    type: array\n    items: str\n",
    )
    cls = Agent._load_model("summary", path=tmp_path / "models.yaml")
    assert cls is not None
    assert issubclass(cls, BaseModel)
    assert cls.__name__ == "output_summary"


def test_load_no_output_model(tmp_path):
    """Agent.load 无 output_model 时 output_type 为 None。"""
    (tmp_path / "plain.md").write_text(
        "---\nname: plain\ndescription: 无输出模型\n---\nplain agent\n",
        encoding="utf-8",
    )
    agent = Agent.load("plain", root=tmp_path)
    assert agent.output_type is None


def test_load_model_file_missing(tmp_path):
    """models.yaml 缺失时抛 ValueError。"""
    with pytest.raises(ValueError, match="models file not found"):
        Agent._load_model("summary", path=tmp_path / "models.yaml")


def test_load_model_entry_missing(tmp_path):
    """条目不存在时抛 ValueError。"""
    _write_models_yaml(tmp_path, "summary:\n  title: str\n")
    with pytest.raises(ValueError, match="输出模型"):
        Agent._load_model("report", path=tmp_path / "models.yaml")
