"""压缩框架：TURN_END 时按策略判断 + 压缩方法折叠超长历史。

这是一个「核心框架 + 注入策略」的组件：
- 框架本身只依赖 CompressionPolicy / Compressor 抽象，不绑定具体实现
- 具体策略（policy 判断触发、compressor 生成摘要）由装配方注入
  （main.py / 测试），可插拔替换

触发时机：订阅 TURN_END（runner 已把 usage/history/provider 放进事件
上下文）。命中策略后，读底层全量原文确定切点，调 compressor 生成摘要，
写回投影视图（history.set_projection）。

底层原文永不删除——压缩只更新投影（读时折叠）。history 若不是可投影
存储（Projection 协议，如 ProjectedMessages），直接跳过，保证对裸存储
无副作用。
"""

import logging

from ..infra import ExtensionAPI, TurnEndEvent, COMPRESS
from ..schemas.protocols import Projection
from .compressors import Compressor
from .policies import CompressionPolicy, CompressionContext

logger = logging.getLogger(__name__)


class Compaction:
    """把超长会话历史在投影层折叠成摘要的框架。

    用法（装配方注入具体策略）::

        comp = Compaction(
            policy=PromptLimitPolicy(limit=4096),
            compressor=SummaryCompressor(),
        )
    """

    def __init__(
        self,
        policy: CompressionPolicy,
        compressor: Compressor,
        *,
        keep_recent: int = 4,
    ):
        self._policy = policy
        self._compressor = compressor
        self._keep_recent = keep_recent  # 切点后保留的最近消息条数

    async def as_ext(self, api: ExtensionAPI) -> None:
        """注册 TURN_END handler（与 approval/mcp/skill 同形态）。"""

        async def maybe_compact(evt: TurnEndEvent) -> None:
            history = evt.history
            # 只压缩可投影存储（Projection 协议）；裸存储没有投影语义，跳过
            if not isinstance(history, Projection):
                return

            raw = await history.underlying.get()
            if len(raw) <= self._keep_recent:
                return

            # 触发判断基于真实历史（policy 可看 items / last_usage 任意信号）
            if not self._policy.hit(
                CompressionContext(items=raw, last_usage=evt.usage)
            ):
                return

            cut = len(raw) - self._keep_recent
            head = raw[:cut]
            summary = await self._compressor(
                head, provider=evt.provider
            )
            if summary is None:
                return  # 摘要失败不写投影，保持现状
            await history.set_projection(cut, summary)
            logger.info(
                f"compact: turn={evt.turn} agent={evt.agent} "
                f"session={evt.session_id} messages={len(raw)}->{cut}"
            )
            COMPRESS.add(1, {"messages.before": len(raw), "messages.after": cut})

        api.on(TurnEndEvent, maybe_compact)
