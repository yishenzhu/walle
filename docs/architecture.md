# 运行时架构：协议驱动分层（Protocol-First Layering）

walle 的运行时模型：**进程级共享"声明"，会话级自持"运行时"**；跨层只依赖
**能力协议**，具体实现由**装配根**（`main.py` / `SessionManager`）注入，
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
   | 具体实现 | 有状态/后端的实现类（`SQLiteMessages`/`SessionServer`…） | 各自模块 | ❌ 禁止引用 |

   判定：协议方法/返回里出现的是"数据怎么流过"→ 数据模型，合法；出现的是
   "哪个实现来做"→ 越界。`Message` 是领域数据模型，故
   `Messages.get() -> list[Message]` 不违反"不依赖具体类"。

2. **协议单层托底** —— 所有跨层能力协议统一收在 `spec/protocol/`（按能力拆
   文件：channel / messages / runtime / events / llm / view）。协议只声明能力面，
   不提及任何具体实现。`spec/` 是**叶子包**：不 import 任何其他层。

3. **装配根是唯一 new 具体类的地方** —— `main.py` 组合插件声明 / 进程默认
   provider；`SessionManager.create` 组合 Session 具体实现（历史存储、会话级
   provider）。除此之外的模块代码不得 `new` 别的模块的具体实现。

4. **核心引擎只依赖协议** —— `Runner` 依赖 `Messages` / `LLM` / `ToolTable`
   / `EventBus`（均为 `spec` 协议）；不依赖 `OpenAIProvider`、`SQLiteMessages`
   等具体实现（具体实现在装配时注入）。

5. **工具只看视图面** —— 工具经 `tool_context -> SessionView` 拿能力，
   绝不 import Session / SessionContext 具体类。

## 2. 分层与依赖方向

实际依赖方向（由 AST 检查，箭头即 import 方向）：

```
main.py ──▶ channel / conf / core / infra / tools / plugin / learning

spec      ──▶ (无)                                      （叶子）
plugin    ──▶ (无)                                      （插件运行时）
conf      ──▶ (无)                                      （叶子）
channel   ──▶ spec, plugin
infra     ──▶ conf, spec, plugin
messages  ──▶ infra, spec
tools     ──▶ conf, plugin, infra, spec
learning  ──▶ plugin, infra, spec
core      ──▶ conf, plugin, infra, messages, spec       （含装配根 SessionManager）
```

| 层 | 位置 | 职责 | 允许依赖 |
|---|---|---|---|
| **协议面** | `spec/` | 数据模型（schemas）+ **全部跨层能力协议** | 自身，无业务依赖 |
| 配置 | `conf/` | Pydantic 配置模型 + YAML 加载 | 自身 |
| 插件运行时 | `plugin/` | EventBus / Plugin / Context 作用域 / PluginHost / 发现 | 自身 |
| 基建实现 | `infra/` | Tool、Provider(LLM 实现)、诊断、遥测、日志、旧式插件兼容层 | `conf`, `spec`, `plugin` |
| 消息实现 | `messages/` | InMemory / SQLite / Projected（Messages 协议实现）+ 装配工厂 | `infra`, `spec` |
| 执行引擎 | `core/` | Runner / ToolExecutor / Agent；Session 与会话装配根 SessionManager；插件服务适配 | `conf`, `plugin`, `infra`, `messages`, `spec` |
| 工具/插件 | `tools/` | 内置工具、ToolService 实现、mcp/skill/approval/sandbox 插件 | `conf`, `plugin`, `infra`, `spec` |
| 可选领域包 | `learning/` | 学习插件：课程工具、课程提示段、课程包存储、Web 路由 | `plugin`, `infra`, `spec` |
| 传输通道 | `channel/` | 与通道无关的 SessionServer + Web 通道，实现 `Channel`/会话接入 | `spec`, `plugin` |
| **装配根** | `main.py` | 唯一组装进程级具体实现（provider / 插件声明 / 会话管理器） | 全部 |

> 依赖方向总则：**协议面最底、装配根最顶；一切跨层引用都要指向协议面，而非
> 实现类。** 一个模块若需要借用别的模块的能力，就在 `spec/protocol/` 声明能力
> 面，由装配根注入实现。
>
> `core/session.py` 是**会话装配根**，按设计允许 import `messages` 并构造具体
> 存储；`core` 的其余模块（runner / executor / agent）不得依赖具体注入件——
> 该规则由 `tests/test_layering.py` 强制。
>
> `channel/transport.py` 是会话服务端与 Web bridge 共用的 JSON-line
> 帧实现（编解码 / 超时 / 帧上限 / 幂等关闭）；`SessionConn` 只保留
> Channel 语义（notify / call / pending）。
>
> 除 `reply` 外的帧统一走 `on_inbound(frame)`，类型按命名空间区分：
> `input.create` / `session.set_agent` / `session.set_metadata` / `run.cancel`。
> 传输层不识别 agent、course 等业务，由 `SessionServer` 解释。
> hello 只负责会话身份/传输配置；插件上下文通过 `session.set_metadata` 更新。
> `on_inbound` 会被 await；`input.create` 必须提交后立即返回，run task 由 `Session` 持有。
>
> 领域插件在包内 `plugin.py` 声明 `PLUGIN`；Web 插件用 `WEB_PLUGINS`
> 惰性引用（`module:attr`），由 Web 进程发现并注入 `WebService`。`WebService`
> 提供 `include_router` / `mount_static` / `register_ui`：插件自己挂路由、静态
> 资源和 UI 清单；Shell 拉取 `/api/web/plugins` 后注入导航并加载插件脚本。
> Web 通道只注册宿主实现，不认识具体领域插件。
> 插件入口统一为 `apply(ctx, config)`，依赖经 `inject`/`provides` 声明。

## 3. 统一协议清单（`spec/protocol/`）

全部跨层能力协议收进 `spec/protocol/` 包，经 `spec/` 顶层导出：

### 通道与交互（`channel.py`）
- `Channel`（notify 广播 / call 点对点）—— 服务端与传输层的唯一稳定契约。
- `Sessions`（通道可见的会话能力：get / create / list / 浏览 / close）
  —— channel 只依赖它，不依赖 core 实现类；register/remove 属管理器内部管理。
- `SessionInit`（创建会话所需的最小输入面：session_id / cwd / model / agent）
  —— `SessionConn` 与 `SessionSpec` 都可满足，spec 不反向依赖 channel。

### 历史存储（`messages.py`）
- `Messages`（get/add/clear/pop/query/search/count/close）
- `Projection`（underlying + set_projection 投影能力）
- `ProjectionStore`（切点持久化）

### 会话视图（`view.py`）
- `SessionView`：工具可见面（channel / jobs / cwd / history / tool_table / bus /
  metadata）。
  `tool_context: ContextVar[SessionView | None]` 保持；实现由装配注入，工具不依赖
  Session 具体类。刻意不含 provider / agents / depth / session_id。

### 插件服务（`tool.py` / `prompt.py` / `skill.py`）
- `ToolService`（add / remove / has / all_tools）—— 会话工具表：插件注册
  工具，Runner 每轮实时取工具源。
- `PromptService`（section / assemble）—— 动态 system 提示段，按
  `AssembleContext`（agent / depth / metadata）求值。
- `SkillService`（add / remove / list）—— 技能目录，支持 glob 筛选。

### 工具表能力（`runtime.py`）
- `ToolTable`（ToolService + skills）—— 会话工具表：工具经 `SessionView` 拿本面
  动态注册，Runner 经本面取每轮工具源与技能清单。

### 模型能力（`llm.py`）
- `LLM`（model / set_model / create / stream）—— Runner 只依赖本面；
  `OpenAIProvider` 作为其实现之一，由装配根注入。

### 事件总线（`events.py`）
- `EventBus`（on / off / emit / bail / parallel）—— Runner / Executor / 插件共用；实现位于 `plugin/events.py`，`infra` 只做转发导出。

> 事件**载荷**（`TurnEndEvent` 等 dataclass）是纯数据，定义在
> `spec/schemas/events.py`，不在 `spec/protocol/`。二者勿混：
> protocol 是能力接口，schemas 是值类型。

## 4. 会话装配（Session 私有件）

```
SessionManager.create(conn)                 # 装配根
├─ build_history(storage, db, session_id)    # messages.factory：投影包装
├─ provider（conn.model 新建 / 沿用进程默认）  # 均为 spec.LLM
└─ Session(session_id, history, provider, plugins, transport, cwd)
    ├─ _bus            EventBus        # 会话唯一事件总线（Runner / Executor / PluginHost 共用）
    ├─ _host           PluginHost      # 每会话插件运行时（start 时 launch）
    ├─ _plugins        list[Plugin]    # 本会话选中的插件声明
    ├─ _prompt         PromptService   # 插件提示段（Runner 组装 system）
    ├─ _agent_runner   Runner          # 构造注入按会话新建的 ToolExecutor
    ├─ _tool_table     SessionToolTable # 会话工具表/技能表（ToolService 落点）
    ├─ _agent          Agent           # 当前 agent（set_agent 可切换）
    ├─ _provider       LLM             # 装配注入，Runner 只见协议
    ├─ _history        Messages        # 装配注入（投影包装），Session 不自行 new
    ├─ _jobs           dict[str, Job]  # 后台作业表（唯一事实源）
    ├─ _metadata       dict[str, Any]  # 客户端/插件元数据（核心不解释键名）
    ├─ _transport      Channel|None    # 连接端点（attach/detach 切换）
    └─ _pending        list[Notification] # 当前 turn 的断连补发缓冲
        context ──────▶ property：现造 SessionContext 视图（对外访问统一入口）
```

`start()` 先注册会话级服务适配器（ToolService / PromptService / SkillService），
再 `PluginHost.launch(plugins)`；插件 `apply(ctx, config)` 只经 ctx 注册，
`Session.close()` 时随 host 作用域逆序回收。

各零件持有**同一 `_bus` 引用**：Runner / Executor / PluginHost /
`tool_context` / CommandContext 共用会话唯一总线。工具预执行由 executor
直接 `bail(PreExecuteToolEvent)`，审批 / guard 插件订阅同一事件。

run task 由 Session 持有；transport 断开只影响事件投递，不取消 run。
新连接 attach 后先收历史，再补发当前 turn 的通知。

插件可注册**动态提示段**：每次模型调用前按
`Session.metadata` 求值，由 `PromptService` 组装成 system 消息。学习插件的
课程上下文即由此注入，核心不感知 course/lesson。

## 5. 各上下文边界

| 上下文 | 给谁 | 使命 | 构造/注入 |
|---|---|---|---|
| `SessionContext`(context) | Runner.run、外部访问 | 跨**轮**会话状态（history/jobs/工具源） | Session property 现造（channel 取当前 _transport） |
| `tool_context` ContextVar | 工具 / 审批插件 / preflight | 跨单轮所有工具执行的会话能力（`SessionView` 视图） | runner 每轮 set；后台任务 run_job 内自设 |
| `CommandContext` | 命令 handler | 单条命令的执行能力 | handle 每次现造 |

> `SessionView`（协议）与 `tool_context` ContextVar 承载运行时注入的会话对象；
> Runner 内部用 `SessionContext` 具体 dataclass，但**工具与插件只见 `SessionView`
> 协议面**，不 import 具体实现类。

## 6. 变量传递边界（单一事实源）

- **bus**：唯一事实源在 Session；`SessionContext.bus` 为 None 时 Runner 现造
  一条（仅供独立测试），经 Session 使用必须注入会话 bus。
- **channel**：唯一事实源 `Session._transport`；`context.channel` 现造视图；工具/
  命令各自经 ContextVar / CommandContext 取。
- **jobs**：唯一事实源 `Session._jobs`（工具写 pending → runner 拉起 →
  job_result 读）。
- **metadata**：唯一事实源 `Session._metadata`；客户端经
  `session.set_metadata` 合并，核心不解释键名，插件在 PromptService 段里读取。
- **工具表**：插件经 ToolService 注册，落到会话 `SessionToolTable`（define_tool
  动态注册实时反映到下一轮）；Agent 不持有工具源。工具/技能注册随会话关闭释放，
  单插件卸载只回收事件订阅，不回收其注册项。
- **provider**：进程默认由装配根构造一次注入 SessionManager；会话自带 `model` 配置时
  由 SessionManager 就地新建，其余沿用进程默认。
- **子 agent 历史**：`SessionContext.history_factory` 提供隔离历史来源（装配注入
  的内存工厂），引擎不 import 具体存储。

## 7. 事件流向（核心只发事件，推送归监听方）

事件载荷定义在 `spec/schemas/events.py`（纯值类型 dataclass），
`infra/__init__` 转发导出以便插件使用。

| 事件 | 生产者 | 消费者 |
|---|---|---|
| `MessageDeltaEvent` | Runner 流式增量 | Session → transport.notify(Delta) |
| `MessageEndEvent` | Runner 文本完成 | Session → transport.notify(DeltaEnd)（output 非 None 时） |
| `PreExecuteToolEvent` | executor | 审批 / guard 插件（ctx.bail） |
| `ToolExecutionEndEvent` | executor | 观测型插件 |
| `ToolExecutionStartEvent` | —（legacy） | 保留给旧插件导入；核心不再发出 |
| `ToolResultEvent` | 工具服务 | 插件观测方 |
| `Session*Event` / `Agent*Event` / `Turn*Event` / `MessageStartEvent` | Runner | 观测 / 会话管理 |

> 载荷只携带值类型：需要与会话状态交互的 handler 从 `SessionView` /
> `tool_context` 取，事件本身不塞 `Messages` / `LLM` 等能力对象——否则
> schemas 会反向依赖 protocol 而成环。
>
> 核心循环不持有推送协议；工具执行通知（ToolStart/ToolResult）目前由 executor
> 直发 channel，与 TOOL_EXECUTION 事件并存 —— 见下节待收敛点。

## 8. 待收敛耦合清单（后续实施 checkpoint）

| 现状耦合 | 目标 |
|---|---|
| `messages/` 依赖 `infra.SQLiteStore`（sqlite 连接辅助，`sqlite.py` / `projection_store.py`） | 存储辅助可下沉到独立模块，或上提为协议面注入 |
| executor 直发 channel（ToolStart/ToolResult）与事件通道并存 | 收敛为事件→监听转发（待后续统一） |
| `conf/` 被 `infra`/`tools`/`core` 直接 import | 配置可作为装配参数注入，使 `conf` 也归装配根 |

> 以上为设计目标与后续实施 checkpoint；**当前文档对应的代码暂未全部收敛**，
> 分轮落地时逐个闭合。

## 9. 已知设计注意

- `tool_context` 由 ContextVar 承载，只在工具执行窗口内有效；后台任务由 run_job
  开头自设。
- 会话 bus 监听顺序 = 注册顺序：审批在 main 组装中先于用户 guard 插件注册，故
  审批先表态。
- 命令「命中即拦截」，不存在命中后放行给 agent 的路径。
- 子 agent 派发（`Agent.as_tool`）需 `SessionContext.history_factory`；缺失时报错
  而非回退到具体存储。
