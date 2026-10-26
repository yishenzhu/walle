"""跨层能力协议面：底层声明、各层实现、装配注入。"""

from .agent import AgentService
from .channel import Channel, SessionInit, Sessions
from .command import Command, CommandContext, CommandHandler, CommandService
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
from .skill import SkillService
from .tool import ToolService
from .view import SessionView
from .web import WebService

__all__ = [
    "AgentService",
    "Channel",
    "Command",
    "CommandContext",
    "CommandHandler",
    "CommandService",
    "SessionInit",
    "Sessions",
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