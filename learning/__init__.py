"""学习插件包：课程工具、课程提示段与课程存储的使用方。"""

from .plugin import course_instructions, learning_plugin
from .tools import create_course, read_course, write_lesson

__all__ = [
    "course_instructions",
    "learning_plugin",
    "create_course",
    "read_course",
    "write_lesson",
]
