"""工具 JSON Schema 的 strict 兼容性回归测试。"""

from __future__ import annotations

from ..infra import Tool
from ..infra.tool import strict_compatible
from ..learning.tools import create_course, write_lesson


async def all_required(a: str, b: int) -> str:
    """两个参数都没有默认值。"""
    return f"{a}{b}"


async def with_default(a: str, b: int = 1) -> str:
    """带默认值的参数不满足 strict。"""
    return f"{a}{b}"


def test_all_required_is_strict():
    schema = Tool.from_function(all_required).to_schema()["function"]
    assert schema["strict"] is True
    assert strict_compatible(schema["parameters"]) is True


def test_default_param_is_not_strict():
    schema = Tool.from_function(with_default).to_schema()["function"]
    assert schema["strict"] is False
    assert strict_compatible(schema["parameters"]) is False


def test_optional_params_downgrade_strict():
    assert Tool.from_function(create_course).to_schema()["function"]["strict"] is False
    assert Tool.from_function(write_lesson).to_schema()["function"]["strict"] is False


def test_nested_object_must_be_closed():
    open_object = {
        "type": "object",
        "properties": {"x": {"type": "object", "properties": {"y": {"type": "string"}}}},
        "required": ["x"],
        "additionalProperties": False,
    }
    assert strict_compatible(open_object) is False

    closed_object = {
        "type": "object",
        "properties": {
            "x": {
                "type": "object",
                "properties": {"y": {"type": "string"}},
                "required": ["y"],
                "additionalProperties": False,
            }
        },
        "required": ["x"],
        "additionalProperties": False,
    }
    assert strict_compatible(closed_object) is True
