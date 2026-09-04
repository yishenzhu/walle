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
| `SessionContext` | core | Session 每次 `run()` 传给 Runner 的**环境包**（消息/channel/jobs/ext_runner） | Session 持一份，随 attach 更新 |
| `ToolContext` | infra | **工具执行期上下文**：经 `tool_context` ContextVar 注入，工具/审批扩展/钩子原地 `get()` | runner 每轮构造一次 |
| `CommandContext` | infra | **命令执行上下文**：暴露 channel/bus，用法由命令自决 | handle 每次构造 |

## 2. 会话装配（Session 私有件）

```
Session
├─ _bus            EventBus        # 会话私有事件总线（唯一事实源）
├─ _tool_executor  ToolExecutor    # 持超时策略；审批经 bus 上的扩展
├─ _agent_runner   Runner          # 构造时注入 _bus + _tool_executor
├─ _ext_runner     ExtensionRunner # 构造时注入 _bus；activate() 选中扩展
├─ _agent_builder  Callable        # 进程注入的 Agent 工厂（main 传入）
├─ _agent          Agent           # 当前 agent（set_agent 可切换）
├─ _messages       Messages        # 历史（SQLite/内存）
├─ _jobs           dict[str, Job]  # 后台作业表（唯一事实源）
├─ _transport      Channel|None    # 连接端点（attach/detach 切换，唯一 channel 事实源）
```

各零件持有**同一 `_bus` 引用**：Session 的事件总线 = Runner 的事件总线 =
ExtensionRunner 激活落点 = 每轮 ToolContext.bus = 命令 CommandContext.bus。
**一个会话只有一条事件总线**，事件隔离天然成立。

## 3. 各上下文边界的判定

| 上下文 | 给谁 | 使命 | 何时构造/注入 |
|---|---|---|---|
| `SessionContext`(env) | Runner.run | 跨**轮**的会话状态（消息/jobs/工具源） | Session 每次 run 现造（channel 取当前 transport） |
| `ToolContext`(tool_context) | 工具 / 审批扩展 / 钩子 | 跨**单轮内所有工具执行**的会话能力 | runner 每轮构造并 `set`；后台作业 run_job 内自设 |
| `CommandContext` | 命令 handler | 单条命令的执行能力 | handle 每次构造（channel 取当前 transport） |

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

### channel：唯一事实源 Session._transport，无镜像副本
```
Session._transport ──现造 env────▶ SessionContext.channel（每次 run 一个）
                   ──每轮────────▶ ToolContext.channel（executor 推送/审批）
                   ──每次命令───▶ CommandContext.channel
```
`Session` 不维护常驻 env/channel 副本：每次 `run` 由 `_make_env()` 按当前
`_transport` 现造会话环境，attach/detach 只改 `_transport` 一处。

### jobs：事实源是 Session._jobs（env 每次现造只是引用视图）
```
Session._jobs ──现造 env 引用──▶ 每次 run 的 SessionContext.jobs
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
SessionContext.ext_runner.register_tool（Session 组装时放入 env）
   └─ runner 每轮 ──▶ ToolContext.register_tool（execute 前 set）
        └─ define_tool 经 tool_context.get().register_tool(tool) 就地注册
```
注册回调在 env 与每轮 ctx 各出现一次，是**同一 bound method**；不把
register_tool 放进 executor 构造参数。

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
                 tools = _build_tools(agent, tool_source)  # available_tools 过滤 + handoffs
              3. ctx = ToolContext(channel=env.channel, jobs=env.jobs,
                     bus=self._bus, register_tool=env.ext_runner.register_tool)
                 tool_context.set(ctx)          # 本轮统一注入一次
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
