# τ-bench 公开基准适配

`walle.eval.bench` 把 [τ-bench](https://github.com/sierra-research/tau-bench)（Sierra
零售/航空客服基准）接入 Walle 的评测体系。

## 设计

只借 τ-bench 三样东西，其余全用 Walle 自己的：

| 组件 | 来源 |
|---|---|
| 数据集（retail 115 test / airline 100 test） | τ-bench 包内置 |
| 状态机环境（env.step，工具执行 + 用户模拟 + 确定性评分） | τ-bench `Env` |
| reward（数据库状态 hash + 输出匹配，`done` 时计算） | τ-bench `calculate_reward` |
| Agent 循环（ReAct + 工具调用 + 历史管理） | **Walle `Runner`** |
| 工具映射（τ-bench JSON Schema → Walle Tool，fn 内部调 env.step） | 本目录 `tau_adapter.py` |
| 用户模拟器（litellm 默认端点不可达，改走 Walle 同一网关） | `WalleUserSimulationEnv` |
| token / 轮次 / 工具调用统计 + 报告 | Walle `eval/` 复用 |

关键适配点：
- 每个 τ-bench 用例 = 一个 Walle 会话：环境工具 + `respond` 工具全部注册给 Agent，
  Runner 驱动完整 ReAct 循环；工具 fn 内部 `env.step(Action(...))` 并加锁串行
  （模型可能并行发起 tool_calls，而 env 共享数据库状态）。
- 官方协议中 agent 的回复 = respond 动作。模型主动调 `respond` 时正常流转；
  模型输出纯文本时，外层循环把文本兜底为 respond 并继续（与官方
  tool-calling agent 语义一致），直到用户模拟器输出 `###STOP###`（对话 done）。
- 构造 env 时用 `user_strategy="human"`（litellm 的 LLM 策略构造期就会发起
  API 调用），构造后替换 `env.user` 为 `WalleUserSimulationEnv`。

## 运行

```bash
PYTHONPATH=.. .venv/bin/python -m walle.eval.bench.run_tau --limit 5
PYTHONPATH=.. .venv/bin/python -m walle.eval.bench.run_tau             # 全量 retail test
PYTHONPATH=.. .venv/bin/python -m walle.eval.bench.run_tau --env airline --split dev
```

报告输出到 `eval/report/tau/`。成本：约 65-70k token/用例（wiki 每轮都在
prompt 中），全量 115 用例约 7-8M token，用 `--price-*` 传入单价出成本列。
