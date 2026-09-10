# 运行时架构：协议驱动分层（Protocol-First Layering）

walle 的运行时模型：**进程级共享"声明"，会话级自持"运行时"**；跨层只依赖**能力协议**，
具体实现由**装配根**（main / SessionRegistry）注入，避免具体类型跨层牵扯。

本文界定分层、能力协议清单、装配唯一原则与已知待收敛耦合清单。

## 1. 核心原则

1. **能力面开协议，不使用具体类** —— 跨层调用只对着 `Protocol`（能力接口），不 import 具体实现类。

   分层按"类型在边界上扮演的角色"判定，而非"是不是具体类"：

   | 类别 | 内容 | 位置 | 协议能否引用 |
   |---|---|---|---|
   | 能力/操作 | 可被调用方依赖的操作契约 | `protocol/`（Protocol） | 本身是协议 |
   | 领域数据 | 跨边界流动的**值类型**（无后端行为：`Message`/`Usage`/`ModelConfig`…） | `schemas/` | ✅ 应当引用 |
   | 具体实现 | 有状态/后端的实现类（`SQLiteMessages`/`CLIChannel`…） | 各自模块 | ❌ 禁止引用 |

   判定：协议方法/返回里出现的是"数据怎么流过"→ 数据模型，合法；出现的是"哪个实现来做"→ 越界。
   `Message` 是领域数据模型，故 `Messages.get() -> list[Message]` 不违反"不依赖具体类"。
2. **协议单层托底** —— 所有跨层能力协议统一收在根目录 `protocol/`（按能力拆文件：
   channel / messages / runtime）。协议只声明能力面，不提及任何具体实现。
   `channel/protocol.py`、`schemas/protocols.py` 已迁入，各自模块不再定义跨层 Protocol。
3. **装配根是唯一 new 具体类的地方** —— `main.py` 组合扩展池 / provider；
   `SessionRegistry.create` 组合 Session 具体实现。除此之外的模块代码不得
   `new` 别的模块的具体实现。
4. **核心引擎只依赖协议** —— Runner 依赖 `Messages` / `LLM` / `SessionView`；
   不依赖 `OpenAIProvider`、`SQLiteMessages` 等具体实现（具体实现在装配时注入）。
5. **工具只看视图面** —— 工具经 `tool_context -> SessionView` 拿能力，
   绝不 import Session / SessionContext 具体类。

## 2. 分层与依赖方向

| 层 | 位置 | 职责 | 允许依赖 |
|---|---|---|---|
| **协议面** | `schemas/` | 数据模型（Messages/Services/Usage）+ **全部跨层能力协议** | 自身，无业务依赖 |
| 基建实现 | `infra/` | EventBus、Tool、Provider(实现)、诊断、遥测、日志 | → schemas |
| 消息实现 | `messages/` | InMemory / SQLite / Projected（Messages 协议实现） | → schemas |
| 执行引擎 | `core/` | Runner / ToolExecutor / Session(装配根) / Agent | → schemas |
| 工具/扩展 | `tools/` | 内置工具、mcp、skill、approval、sandbox（扩展声明） | → schemas |
| 传输通道 | `channel/` | CLI server/client，实现 `Channel`/会话接入 | → schemas |
| **装配根** | `main.py` | 唯一组装进程级具体实现（provider/扩展表） | → schemas |

> 依赖方向总则：**协议面最底、装配根最顶；一切跨层引用都要指向协议面，而非实现类。**
> 一个模块若需要借用别的模块的能力，就在 `schemas/protocols.py` 声明能力面，由装配根注入实现。

## 3. 统一协议清单（根目录 `protocol/`，按能力模块拆分）

全部跨层能力协议收进 `protocol/` 包：

### 通道与交互（`protocol/channel.py`，移自 `channel/protocol.py`）
- `Channel`（notify 广播 / call 点对点）—— 服务端与传输层的唯一稳定契约。
- `Session`（会话管理能力：get/create/register/list）—— channel 只依赖它，不依赖 core 实现类。
- `SessionConn`（会话身份：chat_id / cwd / model）。

### 会话视图（工具执行期能力，移自 `infra/tool.py`）
- `SessionView`：工具可见面（channel / jobs / cwd / history / ext_runner / bus）。
  `tool_context: ContextVar[SessionView | None]` 保持；实现由装配注入，工具不依赖 Session 具体类。

### 历史存储协议（既有）
- `Messages`（get/add/clear/pop/query/search/count）
- `Projection`（underlying + set_projection 投影能力）
- `ProjectionStore`（切点持久化）

### 模型能力（新增，收敛 provider 耦合）
- `LLM` 协议：`create` / `stream` / `set_model` / `model`。
  Runner 只依赖 `LLM`；`OpenAIProvider` 作为其实现之一，由装配注入。

### 扩展激活（既有）
- `ExtensionRegistrar`（register_tool / remove_tool）—— 工具/define_tool 的注册通道。

## 4. 会话装配（Session 私有件）

```
Session
├─ _bus            EventBus        # 会话私有事件总线（唯一事实源）
├─ _agent_runner   Runner          # 构造注入 _bus + 按会话新建的 ToolExecutor
├─ _ext_runner     ExtensionRunner # 构造注入 _bus；activate() 选中扩展
├─ _agent          Agent           # 当前 agent（set_agent 可切换）
├─ _provider       LLM             # 模型实现（装配注入，Runner 只见协议）
├─ _messages       Messages        # 历史（SQLite/内存/投影，协议实现）
├─ _jobs           dict[str, Job]  # 后台作业表（唯一事实源）
├─ _transport      Channel|None    # 连接端点（attach/detach 切换）
└─ context ──────▶ property：现造 SessionContextEnv 视图（对外访问统一入口）
```

各零件持有**同一 `_bus` 引用**：Runner / ExtensionRunner / `tool_context` / CommandContext
共用会话唯一总线。**一个会话只有一条事件总线。**

## 5. 各上下文边界

| 上下文 | 给谁 | 使命 | 构造/注入 |
|---|---|---|---|
| `SessionEnv`(context) | Runner.run、外部访问 | 跨**轮**会话状态（history/jobs/工具源） | Session property 现造（channel 取当前 _transport） |
| `tool_view` ContextVar | 工具 / 审批扩展 / preflight | 跨单轮所有工具执行的会话能力（经 `tool_context` 注入） | runner 每轮 set；后台任务 run_job 内自设 |
| `CommandContext` | 命令 handler | 单条命令的执行能力 | handle 每次现造 |

> `SessionView`（协议）与 `tool_context` ContextVar 承载运行时注入的会话对象；
> Runner 内部用 `SessionEnv` 具体 dataclass，但**工具与扩展只见 `SessionView` 协议面**，
> 不 import 具体实现类。

## 6. 变量传递边界（单一事实源）

- **bus**：唯一事实源在 Session；Runner 默认构造用 `self._bus`（独立测试），经 Session 使用必须注入会话 bus。
- **channel**：唯一事实源 `Session._transport`；`context.channel` 现造视图；工具/命令各自经 ContextVar/CommandContext 取。
- **jobs**：唯一事实源 Session._jobs（工具写 pending → runner 拉起 → job_result 读）。
- **工具表**：事实源在 ExtensionRunner（activate / define_tool 动态注册实时反映到下一轮）；Agent 不持有工具源。

## 7. 事件流向（核心只发事件，推送归监听方）

| 事件 | 生产者 | 消费者 |
|---|---|---|
| `MESSAGE_DELTA` | Runner 流式增量 | Session → transport.notify(Delta) |
| `MESSAGE_END` | Runner 文本完成 | Session → transport.notify(DeltaEnd)（output 非 None 时） |
| `TOOL_EXECUTION_START` | executor | 审批扩展（Approval）、guard 钩子 |
| `TOOL_EXECUTION_END` | executor | 观测型扩展 |
| `SESSION/AGENT/TURN/MESSAGE_*` | Runner | 观测 / 会话管理 |

> 核心循环不持有推送协议；工具执行通知（ToolStart/ToolResult）目前由 executor
> 直发 channel，与 TOOL_EXECUTION 事件并存 —— 列为本轮待收敛点（见下）。

## 8. 待收敛耦合清单（后续实施 checkpoint）

| 现状耦合 | 目标 |
|---|---|
| `SessionView`（协议）在 `infra/tool.py`，且运行时注入的是具体 dataclass | 协议迁入 `schemas/protocols.py`；工具只见协议面 |
| Runner 直接使用 `OpenAIProvider` 具体实现 | Runner 依赖 `LLM` 协议；provider 由装配注入 |
| channel/server 依赖 core 的 `SessionRegistry` 具体类 | channel 只依赖 `Session` 能力协议 |
| tools/messages 直接 import 具体存储类 | 只依赖 `Messages`/`Projection` 协议 |
| executor 直发 channel（ToolStart/ToolResult）与事件通道并存 | 收敛为事件→监听转发（待后续统一） |
| Runner 默认自建 bus vs 会话 bus | 经 Session 注入会话 bus，默认自建仅测试用 |

> 以上为设计目标与后续实施 checkpoint；**当前文档对应的代码暂未全部收敛**，
> 分轮落地时逐个闭合，见 README「架构」档期。

## 9. 已知设计注意

- `tool_context` 由 ContextVar 承载，只在工具执行窗口内有效；后台任务由 run_job 开头自设。
- 会话 bus 监听顺序 = 注册顺序：审批在 main 组装中先于用户 guard 扩展注册，故审批先表态。
- 命令「命中即拦截」，不存在命中后放行给 agent 的路径。