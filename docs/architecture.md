# 运行时架构：协议驱动分层（Protocol-First Layering）

walle 的运行时模型：**进程级共享"声明"，会话级自持"运行时"**；跨层只依赖
**能力协议**，具体实现由**装配根**（`main.py` / `SessionRegistry`）注入，
避免具体类型跨层牵扯。

本文界定分层、能力协议清单、装配原则与依赖方向约束。文中所有路径与依赖
关系均由 `tests/test_layering.py` 静态校验，文档与代码不一致会被 CI 拦下。

## 1. 核心原则

1. **能力面开协议，不使用具体类** —— 跨层调用只对着 `Protocol`（能力接口），
   不 import 具体实现类。

   分层按"类型在边界上扮演的角色"判定，而非"是不是具体类"：

   | 类别 | 内容 | 位置 | 协议能否引用 |
   |---|---|---|---|
   | 能力/操作 | 可被调用方依赖的操作契约 | `spec/protocol/`（Protocol） | 本身是协议 |
   | 领域数据 | 跨边界流动的**值类型**（无后端行为：`Message`/`Usage`/`Job`…） | `spec/schemas/` | ✅ 应当引用 |
   | 具体实现 | 有状态/后端的实现类（`SQLiteMessages`/`CLIChannel`…） | 各自模块 | ❌ 禁止引用 |

   判定：协议方法/返回里出现的是"数据怎么流过"→ 数据模型，合法；出现的是
   "哪个实现来做"→ 越界。`Message` 是领域数据模型，故
   `Messages.get() -> list[Message]` 不违反"不依赖具体类"。

2. **协议单层托底** —— 所有跨层能力协议统一收在 `spec/protocol/`（按能力拆
   文件：channel / messages / runtime / events / llm / view）。协议只声明能力面，
   不提及任何具体实现。`spec/` 是**叶子包**：不 import 任何其他层。

3. **装配根是唯一 new 具体类的地方** —— `main.py` 组合扩展池 / 进程默认
   provider；`SessionRegistry.create` 组合 Session 具体实现（历史存储、会话级
   provider）。除此之外的模块代码不得 `new` 别的模块的具体实现。

4. **核心引擎只依赖协议** —— `Runner` 依赖 `Messages` / `LLM` / `ToolTable`
   / `EventBus`（均为 `spec` 协议）；不依赖 `OpenAIProvider`、`SQLiteMessages`
   等具体实现（具体实现在装配时注入）。

5. **工具只看视图面** —— 工具经 `tool_context -> SessionView` 拿能力，
   绝不 import Session / SessionContext 具体类。

## 2. 分层与依赖方向

实际依赖方向（由 AST 检查，箭头即 import 方向）：

```
main.py ──▶ channel / conf / core / infra / tools      （装配根，最顶）

spec      ──▶ (无)                                      （叶子）
conf      ──▶ (无)                                      （叶子）
channel   ──▶ spec
infra     ──▶ conf, spec
messages  ──▶ infra, spec
tools     ──▶ conf, infra, spec
core      ──▶ conf, infra, messages, spec               （含装配根 SessionRegistry）
```

| 层 | 位置 | 职责 | 允许依赖 |
|---|---|---|---|
| **协议面** | `spec/` | 数据模型（schemas）+ **全部跨层能力协议** | 自身，无业务依赖 |
| 配置 | `conf/` | Pydantic 配置模型 + YAML 加载 | 自身 |
| 基建实现 | `infra/` | EventBus、Tool、Provider(LLM 实现)、诊断、遥测、日志、扩展加载/激活 | `conf`, `spec` |
| 消息实现 | `messages/` | InMemory / SQLite / Projected（Messages 协议实现）+ 装配工厂 | `infra`, `spec` |
| 执行引擎 | `core/` | Runner / ToolExecutor / Agent；Session 与会话装配根 SessionRegistry | `conf`, `infra`, `messages`, `spec` |
| 工具/扩展 | `tools/` | 内置工具、mcp、skill、approval、sandbox（扩展声明） | `conf`, `infra`, `spec` |
| 传输通道 | `channel/` | CLI server/client，实现 `Channel`/会话接入 | `spec` |
| **装配根** | `main.py` | 唯一组装进程级具体实现（provider / 扩展表 / 注册表） | 全部 |

> 依赖方向总则：**协议面最底、装配根最顶；一切跨层引用都要指向协议面，而非
> 实现类。** 一个模块若需要借用别的模块的能力，就在 `spec/protocol/` 声明能力
> 面，由装配根注入实现。
>
> `core/session.py` 是**会话装配根**，按设计允许 import `messages` 并构造具体
> 存储；`core` 的其余模块（runner / executor / agent）不得依赖具体注入件——
> 该规则由 `tests/test_layering.py` 强制。

## 3. 统一协议清单（`spec/protocol/`）

全部跨层能力协议收进 `spec/protocol/` 包，经 `spec/` 顶层导出：

### 通道与交互（`channel.py`）
- `Channel`（notify 广播 / call 点对点）—— 服务端与传输层的唯一稳定契约。
- `Sessions`（会话管理能力：get / create / register / list / remove / close）
  —— channel 只依赖它，不依赖 core 实现类。

### 历史存储（`messages.py`）
- `Messages`（get/add/clear/pop/query/search/count/close）
- `Projection`（underlying + set_projection 投影能力）
- `ProjectionStore`（切点持久化）

### 会话视图（`view.py`）
- `SessionView`：工具可见面（channel / jobs / cwd / history / ext_runner / bus）。
  `tool_context: ContextVar[SessionView | None]` 保持；实现由装配注入，工具不依赖
  Session 具体类。刻意不含 provider / agents / depth / session_id。

### 扩展激活（`runtime.py`）
- `ToolTable`（register_tool / remove_tool / all_tools / skills）—— 会话工具表：
  工具经 `SessionView` 拿本面动态注册，Runner 经本面取每轮工具源与技能清单。

### 模型能力（`llm.py`）
- `LLM`（model / set_model / create / stream）—— Runner 只依赖本面；
  `OpenAIProvider` 作为其实现之一，由装配根注入。

### 事件总线（`events.py`）
- `EventBus`（on / off / emit）—— Runner 只依赖本面发事件，具体总线由装配注入。

> 事件**载荷**（`TurnEndEvent` 等 dataclass）是纯数据，定义在
> `spec/schemas/events.py`，不在 `spec/protocol/`。二者勿混：
> protocol 是能力接口，schemas 是值类型。

## 4. 会话装配（Session 私有件）

```
SessionRegistry.create(conn)                 # 装配根
├─ build_history(storage, db, session_id)    # messages.factory：投影包装
├─ provider（conn.model 新建 / 沿用进程默认）  # 均为 spec.LLM
└─ Session(session_id, history, provider, extensions, transport, cwd)
    ├─ _bus            EventBus        # 会话私有事件总线（唯一事实源）
    ├─ _agent_runner   Runner          # 构造注入按会话新建的 ToolExecutor
    ├─ _ext_runner     ExtensionRunner # 注入 _bus；activate() 选中扩展
    ├─ _agent          Agent           # 当前 agent（set_agent 可切换）
    ├─ _provider       LLM             # 装配注入，Runner 只见协议
    ├─ _history        Messages        # 装配注入（投影包装），Session 不自行 new
    ├─ _jobs           dict[str, Job]  # 后台作业表（唯一事实源）
    └─ _transport      Channel|None    # 连接端点（attach/detach 切换）
        context ──────▶ property：现造 SessionContext 视图（对外访问统一入口）
```

各零件持有**同一 `_bus` 引用**：Runner / ExtensionRunner / `tool_context` /
CommandContext 共用会话唯一总线。**一个会话只有一条事件总线。**

## 5. 各上下文边界

| 上下文 | 给谁 | 使命 | 构造/注入 |
|---|---|---|---|
| `SessionContext`(context) | Runner.run、外部访问 | 跨**轮**会话状态（history/jobs/工具源） | Session property 现造（channel 取当前 _transport） |
| `tool_view` ContextVar | 工具 / 审批扩展 / preflight | 跨单轮所有工具执行的会话能力（经 `tool_context` 注入） | runner 每轮 set；后台任务 run_job 内自设 |
| `CommandContext` | 命令 handler | 单条命令的执行能力 | handle 每次现造 |

> `SessionView`（协议）与 `tool_context` ContextVar 承载运行时注入的会话对象；
> Runner 内部用 `SessionContext` 具体 dataclass，但**工具与扩展只见 `SessionView`
> 协议面**，不 import 具体实现类。

## 6. 变量传递边界（单一事实源）

- **bus**：唯一事实源在 Session；`SessionContext.bus` 为 None 时 Runner 现造
  一条（仅供独立测试），经 Session 使用必须注入会话 bus。
- **channel**：唯一事实源 `Session._transport`；`context.channel` 现造视图；工具/
  命令各自经 ContextVar / CommandContext 取。
- **jobs**：唯一事实源 `Session._jobs`（工具写 pending → runner 拉起 →
  job_result 读）。
- **工具表**：事实源在 ExtensionRunner（activate / define_tool 动态注册实时反映
  到下一轮）；Agent 不持有工具源。
- **provider**：进程默认由装配根构造一次注入 Registry；会话自带 `model` 配置时
  由 Registry 就地新建，其余沿用进程默认。
- **子 agent 历史**：`SessionContext.history_factory` 提供隔离历史来源（装配注入
  的内存工厂），引擎不 import 具体存储。

## 7. 事件流向（核心只发事件，推送归监听方）

事件载荷定义在 `spec/schemas/events.py`（纯值类型 dataclass），
`infra/__init__` 转发导出以便扩展使用。

| 事件 | 生产者 | 消费者 |
|---|---|---|
| `MESSAGE_DELTA` | Runner 流式增量 | Session → transport.notify(Delta) |
| `MESSAGE_END` | Runner 文本完成 | Session → transport.notify(DeltaEnd)（output 非 None 时） |
| `TOOL_EXECUTION_START` | executor | 审批扩展（Approval）、guard 钩子 |
| `TOOL_EXECUTION_END` | executor | 观测型扩展 |
| `SESSION/AGENT/TURN/MESSAGE_*` | Runner | 观测 / 会话管理 |

> 载荷只携带值类型：需要与会话状态交互的 handler 从 `SessionView` /
> `tool_context` 取，事件本身不塞 `Messages` / `LLM` 等能力对象——否则
> schemas 会反向依赖 protocol 而成环。
>
> 核心循环不持有推送协议；工具执行通知（ToolStart/ToolResult）目前由 executor
> 直发 channel，与 TOOL_EXECUTION 事件并存 —— 见下节待收敛点。

## 8. 待收敛耦合清单（后续实施 checkpoint）

| 现状耦合 | 目标 |
|---|---|
| `messages/` 依赖 `infra.SQLiteStore`（sqlite 连接辅助，`sqlite.py` / `meta.py`） | 存储辅助可下沉到独立模块，或上提为协议面注入 |
| executor 直发 channel（ToolStart/ToolResult）与事件通道并存 | 收敛为事件→监听转发（待后续统一） |
| `conf/` 被 `infra`/`tools`/`core` 直接 import | 配置可作为装配参数注入，使 `conf` 也归装配根 |

> 以上为设计目标与后续实施 checkpoint；**当前文档对应的代码暂未全部收敛**，
> 分轮落地时逐个闭合。

## 9. 已知设计注意

- `tool_context` 由 ContextVar 承载，只在工具执行窗口内有效；后台任务由 run_job
  开头自设。
- 会话 bus 监听顺序 = 注册顺序：审批在 main 组装中先于用户 guard 扩展注册，故
  审批先表态。
- 命令「命中即拦截」，不存在命中后放行给 agent 的路径。
- 子 agent 派发（`Agent.as_tool`）需 `SessionContext.history_factory`；缺失时报错
  而非回退到具体存储。
