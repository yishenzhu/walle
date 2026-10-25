"""跨层能力协议面：底层声明、各层实现、装配注入。

只定义能力接口（Protocol），具体实现分布在 infra / messages / channel /
core 中，由装配根注入。使用方统一从本包导入协议，不 import 具体实现类。
"""

from .channel import Channel, SessionInit, Sessions
from .events import EventBus
from .llm import LLM
from .messages import Messages, Projection, ProjectionStore
from .prompt import (
    AssembleContext,
    PromptAssembly,
    PromptOrder,
    PromptSection,
    PromptService,
)
from .runtime import PluginLayer, ToolTable
from .skill import SkillService
from .tool import ToolService
from .view import SessionView
from .web import WebService

__all__ = [
    "Channel",
    "SessionInit",
    "Sessions",
    "PluginLayer",
    "ToolTable",
    "ToolService",
    "SkillService",
    "PromptService",
    "PromptOrder",
    "PromptSection",
    "PromptAssembly",
    "AssembleContext",
    "Messages",
    "Projection",
    "ProjectionStore",
    "EventBus",
    "LLM",
    "SessionView",
    "WebService",
]