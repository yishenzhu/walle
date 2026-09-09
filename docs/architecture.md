# 运行时架构：会话组装与上下文传递

walle 的运行时模型：**进程共享声明（扩展/MCP），会话自持运行时**。
本文界定核心类职责与内部变量的传递边界，避免通道多份持有、职责漂移。

## 1. 核心类一览

| 类 | 层级 | 定位 | 生命周期 |
|---|---|---|---|
| `ExtensionRegistry` | infra | 进程级扩展**加载器**：add/discover → `load()` 产出 `Extension` 声明（tools/handlers/skills/commands） | 进程级一个 |
| `ExtensionRunner` | infra | 会话级扩展**激活层**：把选中扩展落到本会话的 bus + 工具表 + 技能表 + 命令表 | **每会话一个** |
| `Session` | core | 会话实体 = 运行时容器：组装 bus / executor / runner / ext_runner，持历史与作业 | 每连接一个 |
| `Runner` | core | Agent 循环：多轮调 LLM → 工具执行 → handoff；只发事件不碰 transport | 每会话一个（Session 组装） |
| `ToolExecutor` | core | 工具执行器：preflight 事件屏障 → 执行（超时）；**审批是扩展**（订阅 TOOL_EXECUTION_START），不内置 | 每会话一个 |
| `SessionContext` | core | Session 每次 `run()` 传给 Runner 的**环境包**（history/channel/jobs/ext_runner/agents） | Session 持一份，随 attach 更新 |
| `ToolContext` | infra | **工具执行期上下文**：持有 SessionContext（满足 `SessionView`）+ 本轮 bus，工具/审批扩展/钩子原地 `get()` | runner 每轮构造一次 |
| `CommandContext` | infra | **命令执行上下文**：暴露 channel/bus，用法由命令自决 | handle 每次构造 |

## 2. 会话装配（Session 私有件）

```
Session
├─ _bus            EventBus        # 会话私有事件总线（唯一事实源）
├─ _agent_runner   Runner          # 构造时注入 _bus + 按会话新建的 ToolExecutor
├─ _ext_runner     ExtensionRunner # 构造时注入 _bus；activate() 选中扩展
├─ _agent          Agent           # 当前 agent（set_agent 可切换）
├─ _provider       OpenAIProvider
├─ _messages       Messages        # 历史（SQLite/内存）
├─ _jobs           dict[str, Job]  # 后台作业表（唯一事实源）
├─ _transport      Channel|None    # 连接端点（attach/detach 切换，唯一 channel 事实源）
└─ context ──────▶ property：每次现造 SessionContext 视图
                   （channel=_transport / jobs=_jobs / ext_runner=_ext_runner…），
                   对外访问会话能力的统一入口，也是 runner.run 的 env
```

各零件持有**同一 `_bus` 引用**：Session 的事件总线 = Runner 的事件总线 =
ExtensionRunner 激活落点 = 每轮 ToolContext.bus = 命令 CommandContext.bus。
**一个会话只有一条事件总线**，事件隔离天然成立。

## 3. 各上下文边界的判定

| 上下文 | 给谁 | 使命 | 何时构造/注入 |
|---|---|---|---|
| `SessionContext`(context/env) | Runner.run、外部访问 | 跨**轮**的会话状态（history/jobs/工具源） | Session property，每次访问现造视图（channel 取当前 _transport） |
| `ToolContext`(tool_context) | 工具 / 审批扩展 / 钩子 | 跨**单轮内所有工具执行**的会话能力（转发 session + 本轮 bus） | runner 每轮构造并 `set`；后台作业 run_job 内自设 |
| `CommandContext` | 命令 handler | 单条命令的执行能力 | handle 每次构造（channel 取 context.channel） |

**判定规则**：
- 会话生命周期级状态（历史/作业/扩展激活）→ `SessionContext`
- 工具执行级能力（channel 推送、动态注册工具、作业表）→ `ToolContext`（经 ContextVar，**不随参数传**）
- 命令是一次性动作 → `CommandContext`（每次现造，attach 切换天然正确）

## 4. 变量传递边界（消除双持有/镜像）

### bus：单一事实源在 Session
```
Session._bus ──注入──▶ Runner._bus
            ──注入──▶ ExtensionRunner._bus
每轮：ctx.bus = self._bus（runner 内）→ executor 经 tool_context.get().bus
命令：CommandContext.bus = Session._bus
```
注意：`Runner` 的默认构造会**自建 bus**（独立运行/测试）。凡经 Session 使用的
Runner 必须显式注入会话 bus，否则扩展事件与工具钩子会落到两条总线上。

### channel：唯一事实源 Session._transport（内部私有件）
```
Session._transport ──context 视图──▶ context.channel（每次现造，供外部/runner）
                   ──每轮──────────▶ ToolContext.channel（executor 推送/审批）
                   ──每次命令─────▶ CommandContext.channel
```
attach/detach 只改 `_transport` 一处；Session 内部（Delta 转发/handle）直接
用私有件，不绕 context。对外读 channel 统一经 `session.context.channel`。

### jobs：唯一事实源 Session._jobs（context 视图引用同一 dict）
```
Session._jobs ──context 视图──▶ context.jobs（外部读写同一 dict）
              ──每轮引用──────▶ ToolContext.jobs（background 写入点）
```
作业表跨轮存活：background 工具写 pending → runner 每轮 `launch_pending`
从 ctx 拉起 → run_job 写回结果 → job_result 读取。

### 工具表：事实源是 ExtensionRunner
```
ExtensionRunner._tools ──activate/register_tool 写入
                       ──all_tools()──▶ runner 每轮快照（dict 传 executor）
```
Agent **不持有工具**：`Agent.available_tools(source)` 只做 `tool_filter`
过滤，源由 runner 每轮从 `env.ext_runner.all_tools()` 取。工具表随扩展激活
/define_tool 动态注册实时反映到下一轮。

### 工具执行期动态注册通道（define_tool）
```
SessionContext.ext_runner（Session 组装时放入 env，满足 ExtRunner 能力面）
   └─ runner 每轮 ──▶ ToolContext.ext_runner（转发 session，同一实例）
        └─ define_tool 经 tool_context.get().ext_runner.register_tool(tool) 就地注册
```
ToolContext 不依赖具体 ExtensionRunner——只依赖 `ExtRunner` 协议
（schemas/protocols.py：register_tool/remove_tool），避免 infra/tool 与
infra/extension 相互 import 成环。注册通道在 env 与每轮 ctx 各出现一次，
是**同一实例**；不把注册通道放进 executor 构造参数。

### 历史回源与工作笔记（history 工具 + .agent/note.md 文件）
```
ToolContext.history ──► Messages 协议（search/count/query，查底层原文，
                        不经投影）── 供 history 工具回源折叠前的细节
工作笔记 = 工作目录下普通文件 .agent/note.md（无专用存储/协议/注入）：
模型用通用 read 读、edit 局部更新（todo/goal/决策用 md 结构组织）
```
history 工具定义在 messages/tool.py（消息层能力，不经工具注册链可直接
复用）；new_window 同文件——模型维护好 note.md 后主动硬切窗口
（折叠旧轮，投影只留当前轮）。ToolContext.history 直接转发
SessionContext.history；笔记文件不经会话上下文（模型按需 read）。

## 5. 一次输入的执行链（数据流）

```
CLIChannel.on_input
  └─ Session.handle(UserInput)
       ├─ dispatch(content, CommandContext(channel=_transport, bus=_bus))
       │    ├─ 命中：命令 handler 自决（channel.notify 推送 / call 提问）
       │    └─ 未命中 ↓
       └─ Runner.run(agent, input, env=_make_env(), streamed=True)
            loop turn:
              1. _build_messages（history + instruction + 技能清单[经
                 env.ext_runner.skills × agent.skills 白名单]）
              2. tool_source = env.ext_runner.all_tools()
                 tools = _build_tools(agent, tool_source, env)  # 过滤 + handoff/subagent
              3. ctx = ToolContext(session=env, bus=self._bus)
                 tool_context.set(ctx)          # 本轮统一注入一次
                 # 工具视角别名：ctx.channel/jobs/cwd/history/ext 转发到 env
              4. 有 tool_calls：
                   execute_calls(tool_calls, tools)   # 并发，as_completed
                     └─ execute_tool 内 tool_context.get() → ctx
                          ├─ notify ToolStart（channel）
                          ├─ bus.emit TOOL_EXECUTION_START（审批扩展在此
                          │    判 deny/ask：ASK 经 ctx.channel 问用户）
                          ├─ 工具 fn 执行（经 tool_context 可动态注册/提问）
                          └─ bus.emit TOOL_EXECUTION_END（观测）
              5. launch_pending(tools)：background 作业 create_task
                 └─ run_job(job, tools, ctx) 新 task 开头 tool_context.set(ctx)
              6. 无 tool_calls → break，发 MESSAGE_END
```

## 6. 事件流向（Runner 只发事件，推送归监听者）

| 事件 | 生产者 | 消费者 |
|---|---|---|
| `MESSAGE_DELTA` | Runner 流式增量 | Session 监听 → transport.notify(Delta) |
| `MESSAGE_END` | Runner（文本输出完成） | Session 监听 → transport.notify(DeltaEnd)（output 非 None 时） |
| `TOOL_EXECUTION_START` | executor | 审批扩展（Approval）、guard 钩子（HookVerdict block/改写） |
| `TOOL_EXECUTION_END` | executor | 观测型扩展（result/error/elapsed） |
| `SESSION/AGENT/TURN/MESSAGE_START·END` | Runner | 观测 / 会话管理 |

约定：**核心循环不直接持有推送协议**（Delta/DeltaEnd 等 Notification 由
监听方构造）；工具执行通知（ToolStart/ToolResult）目前在 executor 直发
channel，与 TOOL_EXECUTION 事件并存——为已知待收敛点。

## 7. 已知设计注意

- `ToolContext` 由 ContextVar 承载，**只在工具执行窗口内有效**；runner 每轮
  set 覆盖，后台作业（新 task）由 run_job 开头自设。不要假设它能跨轮存活。
- 会话 bus 上的事件监听顺序 = 注册顺序：审批扩展在 main 组装中先于用户
  guard 扩展注册，故审批先于 guard 表态。
- 命令"命中即拦截"，不存在命中后放行给 agent 的路径（输入处理序：命令
  优先于 agent）。
