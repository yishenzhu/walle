"""Agent 输出模型（output_model）加载测试。

覆盖 _load_model 的成功与各异常分支：
- 未提供 model_name -> None
- 正常加载同名 BaseModel 子类
- 文件缺失 -> ValueError
- 类不存在 -> ValueError
- 类非 BaseModel 子类 -> ValueError

用 monkeypatch 把 DOT_AGENT 指向 tmp_path，隔离真实 .agent/。
"""

from pathlib import Path

import pytest
from pydantic import BaseModel

from ..core import agent as agent_mod
from ..core.agent import Agent


@pytest.fixture
def agents_dir(tmp_path, monkeypatch):
    """构造隔离的 .agent/agents 目录，并把 agent.DOT_AGENT 指向 tmp_path。"""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    monkeypatch.setattr(agent_mod, "DOT_AGENT", tmp_path)
    return agents_dir


def _write_models_py(agents_dir: Path, content: str) -> None:
    (agents_dir / "models.py").write_text(content, encoding="utf-8")


def test_load_model_none_returns_none(agents_dir):
    """未提供 model_name 时返回 None（即使 models.py 存在）。"""
    _write_models_py(
        agents_dir, "from pydantic import BaseModel\nclass Foo(BaseModel): pass\n"
    )
    assert Agent._load_model(None) is None


def test_load_model_success(agents_dir):
    """正常加载同名 BaseModel 子类。"""
    _write_models_py(
        agents_dir,
        "from pydantic import BaseModel\nclass Report(BaseModel):\n    title: str\n",
    )
    cls = Agent._load_model("Report")
    assert cls is not None
    assert issubclass(cls, BaseModel)
    assert cls.__name__ == "Report"


def test_load_model_file_missing(tmp_path, monkeypatch):
    """models.py 缺失时抛 ValueError。"""
    monkeypatch.setattr(agent_mod, "DOT_AGENT", tmp_path)
    # 不创建 agents/models.py
    with pytest.raises(ValueError, match="output models file not found"):
        Agent._load_model("Report")


def test_load_model_class_missing(agents_dir):
    """类名不存在时抛 ValueError。"""
    _write_models_py(
        agents_dir, "from pydantic import BaseModel\nclass Foo(BaseModel): pass\n"
    )
    with pytest.raises(ValueError, match="输出模型"):
        Agent._load_model("Report")


def test_load_model_not_basemodel(agents_dir):
    """类不是 BaseModel 子类时抛 ValueError。"""
    _write_models_py(agents_dir, "class NotAModel:\n    pass\n")
    with pytest.raises(ValueError, match="不是 BaseModel"):
        Agent._load_model("NotAModel")
