# Walle · AI 辅助学习系统

> 从学习目标到可导出的课程包：AI 生成课程大纲、课节与交互画布，边学边问，离线可读。

Walle 是一个 AI 辅助学习系统。底层是通用 Agent 运行时（工具调用、多智能体、MCP、插件化、可观测），学习插件在此之上提供课程生成、课节阅读、交互画布、答疑与导出。

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![Pydantic](https://img.shields.io/badge/Pydantic-v2-E92063?logo=pydantic&logoColor=white)](https://docs.pydantic.dev)
[![MCP](https://img.shields.io/badge/MCP-Protocol-0A9F77?logo=modelcontextprotocol)](https://modelcontextprotocol.io)
[![OpenTelemetry](https://img.shields.io/badge/Observability-OpenTelemetry-425CC7?logo=opentelemetry)](https://opentelemetry.io)

---

## ✨ 核心特性

| 特性 | 说明 |
|---|---|
| 🎓 **AI 课程生成** | 从学习目标生成课程大纲、Markdown 课节与交互画布；按章节顺序逐课完成 |
| 🖼️ **交互画布** | 共享 JSXGraph runtime，优先使用 `spec`；全局 `canvas_id`，普通对话和课程共用 |
| 💬 **课程答疑** | 课程页默认 `tutor` 只读答疑，基于 `read_course` 与课程上下文 |
| 📦 **离线导出** | 课程导出为 zip：课节、画布、runtime 与 JSXGraph 一并打包，解压即可打开 |
| 🧭 **学习空间** | 课程列表 + 课节阅读；左侧内容、右侧常驻面板切换「画布 / 对话」，可拖拽分栏 |
| ⚙️ **Agent 循环引擎** | ReAct 式多轮工具调用，流式/非流式双模式，可配置最大轮次 |
| 📝 **Agent 可配置化** | `.agent/agents/*.md` frontmatter 定义（角色/温度/工具筛选/技能白名单），启动按名加载，会话内可切换 |
| 🤝 **多智能体 Handoff** | Agent 可移交任务，支持链式协作 |
| 🔌 **MCP 协议集成** | MCP 作为插件（stdio / Streamable HTTP），连接进程级共享，工具随插件进会话 |
| 🛡️ **工具治理** | 审批即插件：conf 规则（allow/deny/ask）由 `Approval` 插件订阅工具执行事件执行，ASK 经会话通道人工确认；可按工具名 + 参数粒度控制 |
| 🔒 **OS 级沙箱** | 可选 bwrap 沙箱插件：全盘只读 + 会话 cwd 可写，隐藏凭据路径，可断网；同名覆盖内置 bash，无 bwrap 时自动降级 |
| 🧠 **全链路可观测** | OpenTelemetry Traces + Metrics → Grafana / Tempo / Mimir |
| 💬 **Web 通道** | JSON-line 会话服务端 + WebSocket 桥接；多客户端并发、流式回复，断开重连保留状态 |
| 🔌 **插件化** | `apply(ctx, config)` + 服务依赖注入 + 每会话 PluginHost；工具 / 提示段 / 技能 / 事件 / Web 路由都是插件，`.agent/plugins/` 即插即用 |
| 🪟 **上下文窗口管理** | 模型主动换窗口（`read`/`edit` 维护 `.agent/note.md` 工作笔记 + `new_window` 硬切）、`history` 工具无损回源、会话 JSONL + 切点持久化 |

---

## 🎓 学习系统

Walle 的默认使用方式是 AI 辅助学习：

1. 在对话里说明学习目标。
2. 学习插件生成课程大纲、Markdown 课节和交互画布。
3. 在课程页左侧阅读，右侧在「画布 / 对话」之间切换。
4. 标记完成、记录进度；需要时导出离线 zip。

画布是全局资源，课程只通过 `{{canvas:<canvas_id>}}` 引用；普通对话和课程共用同一个 `canvas` 工具与 JSXGraph runtime。

---

## 🏗️ 架构

进程级共享"声明"，会话级自持"运行时"。

```mermaid
flowchart TD
    Main["main.py<br/>组装插件声明 · 启动"]
    Plugins["插件声明<br/>apply(ctx, config)<br/>builtin / sandbox / mcp / skill / approval / learning<br/>+ .agent/plugins/ 用户插件"]
    SessionManager["SessionManager<br/>会话管理器 · 工厂<br/>持插件声明池 / tool_config"]
    S["Session<br/>会话运行时容器<br/>bus · host · runner · tool table"]
    Host["PluginHost<br/>每会话插件运行时<br/>Context 作用域 · 依赖驱动激活"]
    Services["服务适配器<br/>ToolService / PromptService / SkillService"]
    R["Runner<br/>Agent 循环 · 流式增量事件"]
    E["ToolExecutor<br/>preflight 屏障 · 并发 · 超时"]
    A["Agent<br/>定义 · tool_filter · 技能白名单"]
    T["tools 工具<br/>内置 / MCP / 动态 define_tool"]
    Ch["Channel<br/>notify / call<br/>Web 多会话"]

    Main --> Plugins
    Plugins -->|Plugin 声明池| SessionManager
    SessionManager -->|create 会话| S
    S -->|start: launch 选中插件| Host
    Host --> Services
    S --> R
    S --> E
    R --> A
    E --> T
    Services -->|工具 / 提示段 / 技能| R
    Ch --> S

    classDef entry fill:#e8f5e9,stroke:#2e7d32
    classDef core fill:#e3f2fd,stroke:#1565c0
    classDef tool fill:#fff3e0,stroke:#e65100
    class Main,Plugins,SessionManager entry
    class S,Host,Services,R,E,A core
    class T,Ch tool
```

> 详细类职责与内部变量传递边界见 [docs/architecture.md](docs/architecture.md)。

### 核心流程

```mermaid
sequenceDiagram
    participant C as Channel(Web)
    participant S as Session
    participant R as Runner
    participant B as Bus(会话事件)
    participant E as ToolExecutor
    participant M as LLM

    C->>S: handle(UserInput)
    S->>S: 斜杠命令? → CommandContext 分发(命中即返回)
    S->>R: run(agent, input, env)
    loop 多轮迭代
        R->>M: 调用 LLM（流式增量 → MESSAGE_DELTA 事件）
        M-->>R: tool_calls / 文本
        alt 有 tool_calls
            R->>R: tool_context 统一注入本轮 ctx
            R->>E: execute_tool_calls（并发）
            E->>B: TOOL_EXECUTION_START（审批插件 deny/ask → 人工确认）
            E->>E: 执行工具（超时）→ TOOL_EXECUTION_END
            E-->>R: 结果
            alt 含 Handoff
                R->>R: 切换 Agent，继续循环
            end
        else 无 tool_calls
            R-->>S: MESSAGE_END（→ DeltaEnd 推送）
        end
    end
    S-->>C: notify(Delta) 流式输出
    C-->>U: 回复
```

执行中 Ctrl+C → 取消当前 run，回到输入提示；空输入（直接回车）/ 空闲 Ctrl+C → 退出（统一出口）

### 分层职责

架构核心原则：**跨层只依赖能力协议（`spec/`），具体实现由装配根注入**，避免具体类型跨层牵扯。依赖方向由 `tests/test_layering.py` 静态校验，违反即 CI 失败。

| 层 | 目录 | 职责 |
|---|---|---|
| 协议面 | `spec/` | **数据模型 + 全部跨层能力协议**（唯一声明面，不提及实现）：protocol/（Channel/Sessions/SessionInit）、messages（Messages/Projection/ProjectionStore）、events（EventBus）、llm（LLM）、view（SessionView） |
| 入口·装配根 | `main.py` | 唯一组装进程级具体实现：插件声明（builtin/sandbox/mcp/skill/approval/learning/用户插件）、进程默认 provider、会话服务端 |
| 基建实现 | `infra/` | EventBus、Tool、Provider（LLM 实现）、诊断、日志、遥测、指标、插件加载/激活器 |
| 核心引擎 | `core/` | 会话装配（SessionManager：构造存储/provider 并组装 Session）、Agent 循环（Runner）、工具执行器 |
| 工具/插件 | `tools/` | ToolService 实现、内置工具、mcp/skill/approval/sandbox 插件 —— 均依赖协议面与服务能力 |
| 交互通道 | `channel/` | JSON-line 帧传输（`transport.py`）+ 与通道无关的会话服务端（`server.py`）+ Web 工作区；新增通道复用 Channel 协议即可 |
| 消息存储 | `messages/` | File/SQLite/InMemory/Projected 的 Messages 协议实现 + 装配工厂（build_history） |
| 数据模型 | `spec/schemas/` | 消息、判别联合事件（通知/服务）、Token 用量、后台作业模型 |
| 配置 | `conf/` | Pydantic 配置模型 + YAML 加载 |
| 可观测性 | `observability/` | Docker Compose 编排的监控栈 |

---

## 🚀 快速开始

### 前置条件

- **Python ≥ 3.12**（pyproject.toml 要求）
- **bubblewrap**（可选，启用 OS 级沙箱：`apt install bubblewrap`；缺失时沙箱插件自动跳过）
- **Docker + Docker Compose**（可选，用于可观测性栈）

### 安装

```bash
git clone <repo-url> walle
cd walle
pip install -e ".[dev]"
```

> 仓库根目录本身即 `walle` 包（`run.sh` 以 `PYTHONPATH=<父目录> -m walle.main` 运行），
> setuptools 的自动发现只扫子目录、找不到根包，故 `pyproject.toml` 里显式声明了
> 包与目录映射。**新增子包时需同步补 `[tool.setuptools]` 的 packages / package-dir 两处。**

### 开发与检查

```bash
pytest -q                      # 单元测试（385 项，无需 API Key）
ruff check .                   # 静态检查
ruff check --fix .             # 自动修复（import 排序等）
```

`tests/test_layering.py` 用 AST 静态校验分层依赖方向（见 `docs/architecture.md`），
违反即失败。CI（`.github/workflows/ci.yml`）在 Python 3.12 / 3.13 上跑上述两项。

### 配置

```bash
cp conf.yaml.example conf.yaml   # 主配置（日志 / 审批）
cp .env.example .env             # LLM API Key
```

### 启动

```bash
./scripts/run.sh up                    # 后台启动 agent + Web + 可观测性
./scripts/run.sh down                  # 停止全部服务
./scripts/run.sh status                # 服务状态
./scripts/run.sh logs agent -f         # 跟踪日志（web / obs 同理）
./scripts/run.sh restart web           # 重启单个服务（all / agent / web / obs）
./scripts/run.sh agent --no-obs        # 前台仅启动 agent
./scripts/run.sh web --port 8081       # 前台启动 Web，参数原样透传
./scripts/run.sh obs up                # 仅启动可观测性容器
./scripts/run.sh test -k course        # pytest 参数透传
./scripts/run.sh doctor                # Python / 配置 / Docker / 端口自检
./scripts/run.sh help                  # 完整命令列表
```

旧版 `--no-obs` / `--web` / `--stop*` / `--test` / `--status` 参数仍兼容。

Web 工作区（唯一交互通道，需先安装 `fastapi` / `uvicorn`）：

```bash
./scripts/run.sh web              # 服务端未运行时自动后台启动 + Web 通道（前台）
./scripts/run.sh web --port 8081  # web 之后的参数原样转给 Web 服务
PYTHONPATH=.. python -m walle.channel.web   # 也可单独启动，默认 http://127.0.0.1:8080
```

- **工作区与会话**：顶栏「工作区」打开最近目录、手动路径输入和当前目录下的历史会话；选择会话会 attach 恢复，刷新页面默认接续上次会话，也可「新建会话」。
- **学习空间**：顶栏「学习」进入 `/courses`；课程页左侧阅读课节，顶部下拉切换章节，右侧常驻面板可在「画布 / 对话」之间切换，左右栏可拖动调整宽度。进入课程回到生成该课程的原会话，切换课节时课程上下文自动跟随。
- **课程模式**：顶栏「课程模式」在当前连接内切换到 `learning` agent，用于生成/续写课程；关闭后回到通用助手。课程页默认使用只读 `tutor` 答疑，点「填入续写提示」会切到 `learning` 写课。
- **服务重启恢复**：attach 失败时 Web 用同一 `session_id` 重新建会话，从 `data/sessions` 的 JSONL 读回历史并回填到页面。
- **断线自动重连**：异常断开后自动恢复；run 在服务端继续，新连接接收后续事件，服务重启后历史从文件读回。
- **run 续跑**：连接断开不取消当前 run；新连接 attach 后继续接收流式事件，审批/提问会等待重连。
- **对话区**：Markdown 渲染（marked + KaTeX + DOMPurify），支持代码块、表格、引用、链接，以及 `$...$` / `$$...$$` 数学公式；流式输出。
- **画布区**：`canvas` 工具调用显示为可展开的工具行，点开可看参数；行内「打开画布」按钮控制右侧面板，打开后变为「收起画布」。支持拖拽分栏与全屏。
- **交互反馈**：工具参数生成阶段先显示「工具名 生成参数…」，参数完成后进入工具执行状态；审批卡支持允许/拒绝和结果折叠。
- **画布内容**：全局存放在 `data/canvas/<canvas_id>.html`，通过 `/canvas/<session_id>/<canvas_id>` 加载（旧 session/课程目录仅作回退）；优先使用共享 JSXGraph runtime 的 `spec`，完整 HTML 作为兜底。
- **画布 runtime**：`spec` 由 `/plugins/learning/canvas-runtime.js` 渲染，JSXGraph 与 runtime 在导出时只打包一次。
- 画布工具默认在 `conf.yaml.example` 中放行；可以这样触发：`用 canvas 工具画一个 sin(x) 函数图像`。

会话是**持久实体**（跨连接/进程存活）：连接断开 → `detach` 保留状态，当前 run 继续执行；连接接入 → `attach` 绑定新传输并继续接收事件。Web 工作区可从工作目录浏览历史会话并恢复。真正销毁走服务端停机（`--stop`）。

`read` / `write` / `edit` 的相对路径按会话工作目录解析，绝对路径原样使用；与 bash 和沙箱的可写基准一致。

### 📊 可观测性面板

| 服务 | 地址 | 账号 |
|------|------|------|
| Grafana | http://localhost:3000 | admin / admin |
| Tempo | http://localhost:3200 | - |
| Mimir | http://localhost:9009 | - |

可查看：Agent 每轮迭代耗时与 Span、工具调用次数/耗时/错误率、Handoff 事件。

> 使用 `--no-obs` 启动 agent 时，若 `telemetry.enabled` 仍为 `true`，OTLP 导出会持续报
> `DEADLINE_EXCEEDED`。不需要观测时把 `telemetry.enabled` 设为 `false`，或单独运行
> `./scripts/run.sh obs up` 启动 collector。

---

## ⚙️ 配置

### `conf.yaml` — 主配置

```yaml
log:
  level: "DEBUG"
  path: "logs/agent.log"
  backup_count: 30

telemetry:
  enabled: true
  service_name: "agent"
  otlp:
    endpoint: "http://localhost:4317"
    insecure: true
  console_export: false

tool:
  timeout:
    default: 30                       # 全局默认超时（秒）
    overrides:
      ask_user: null                  # None = 豁免超时（等人回答不设时限）
  approval:
    rules:
      - [deny, bash(cmd=rm -rf /)]    # 危险命令直接拒绝
      - [allow, bash(cmd=ls -la *)]   # 安全命令自动放行
      - [allow, ask_user]             # 提问工具自动放行
      - [allow, canvas]               # 画布 HTML 在 sandbox iframe 内渲染，自动放行
      - [allow, create_course]        # 课程包写入 data/courses
      - [allow, write_lesson]         # 课节 md 写入 data/courses
      - [allow, read_course]          # 课程答疑只读
      - [allow, read]                 # 读文件自动放行（技能按需加载全文）
    default: ask                      # 默认需人工审批

session:
  storage: "file"                     # file | sqlite | memory
  path: "data/sessions"               # file=会话根；sqlite=db 文件；memory 忽略

plugin:
  dir: ".agent/plugins"              # 用户插件目录
  enabled: []                         # 空 = 全部启用；非空 = 白名单
  disabled: []                        # disabled 优先于 enabled

# MCP server 配置不在此文件，统一放在 .agent/mcp.yaml（手动编辑）
```

#### 审批规则

规则格式 `[action, pattern]`，pattern 支持 glob：

| action | 含义 |
|---|---|
| `allow` | 放行 |
| `deny` | 拒绝 |
| `ask` | 人工确认（默认） |

示例匹配：`bash`（全部 bash）、`bash(cmd=rm -rf *)`（特定参数）、`ask_user`（工具名）。

工具超时：全局 `tool.timeout.default` 生效，`tool.timeout.overrides` 按工具名覆盖（`None` 豁免超时，如 `ask_user` 等人回答）。

### `.agent/` — 运行时持久化

| 路径 | 内容 | 写入方式 |
|---|---|---|
| `.agent/agents/` | Agent 定义（frontmatter Markdown，文件名即 agent 名） | 手动编辑 |
| `.agent/skills/` | 技能（SKILL.md + 可选 scripts/assets） | `skill-creator` 或手动 |
| `.agent/tools/` | 模型定义的代码工具 | `define_tool` |
| `.agent/plugins/` | 用户插件（`plugin.py` 定义 `apply(ctx, config)`，可同名覆盖内置工具） | 手动编辑 |
| `.agent/note.md` | 工作笔记（todo / goal / 决策；位于会话工作目录下） | 模型 `read` / `edit` |
| `.agent/mcp.yaml` | MCP Server 配置 | 手动编辑 |

以上均在下次启动自动恢复。

### `data/sessions/` — 会话文件

默认 `session.storage: file`：会话正文集中存放在 `data/sessions/<workspace_id>/`，**不写进用户工作目录**，避免污染仓库或随仓库分发。

```
data/sessions/<workspace_id>/
├── workspace.json           # canonical 工作目录路径、名称、更新时间
└── <session_id>/
    ├── meta.json            # 会话身份、cwd、时间戳
    ├── messages.jsonl       # 逐条追加的原始消息
    └── projection.json      # new_window 切点（可选）
```

工作目录只作为分组键（canonical path 的稳定摘要）。删除目录不会自动删除会话；需要“删除工作目录及其会话”时显式清理。`sqlite` 仍可选，用于需要数据库查询/迁移的场景。

### Agent 定义（frontmatter）

每个 Agent 是一个 `.agent/agents/<name>.md` 文件（**文件名 = agent 名**），frontmatter 定义角色与工具筛选，markdown 正文即 system prompt：

```markdown
---
name: coder
description: 编码助手，专注代码编写与重构
temperature: 0.2
tools:
  allow:
    - "*"
  deny:
    - bash
---

你是一名资深编码助手。优先使用 python/bash 完成任务，禁止 bash 执行任意危险命令。
```

| frontmatter 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 必填，必须等于文件名 |
| `description` | string | 可选，Agent 描述 |
| `temperature` | float | 可选，采样温度 |
| `tools.allow` | list[string] | 可选，允许的工具 glob（默认 `[]` 禁用全部，需显式授权） |
| `tools.deny` | list[string] | 可选，拒绝的工具 glob（优先于 allow） |
| `skills` | list[string] | 可选，技能白名单：`[]` 不注入 / `["*"]` 全部 / 列表仅命中项 |

- **工具筛选**：`deny` 优先于 `allow`，支持 `mcp_obsidian*` 等 glob 通配。Agent 不持有工具源——运行时每轮从会话插件 runner 取工具表并经 `tools` 过滤
- **技能注入**：会话激活技能插件后，可用技能清单（名/描述/路径）按 `skills` 白名单注入 system prompt，模型按需加载全文
- **默认 Agent**：`.agent/agents/default.md`，未指定 agent 名时加载
- **课程导师**：`.agent/agents/tutor.md`，只读答疑（仅允许 `read_course`），课程页自动使用
- **课程规划**：`.agent/agents/learning.md`，按章节顺序生成课程包（当前课正文和画布完成后再进入下一课），Web「课程模式」使用
- **会话内切换**：API `Session.set_agent(name)` 按名切换（历史保留）；未指定时用默认 agent

---

## 🔧 插件

### 添加内置工具

```python
# tools/builtin/my_tool.py
from .. import tool_context

async def my_tool(query: str) -> str:
    """工具描述，会自动生成 schema。"""
    ctx = tool_context.get()   # 访问会话视图 SessionView（channel / jobs / bus / cwd / history / metadata）
    return f"result: {query}"
```

插件采用 `apply(ctx, config)` 入口，通过服务能力面注册工具、技能、提示词与事件：

```python
# tools/builtin/plugin.py
from ...plugin import plugin
from ...spec import ToolService

@plugin(name="builtin", inject=(ToolService,))
async def builtin_plugin(ctx, config) -> None:
    tools = ctx.get(ToolService)
    for fn in (bash, ask_user, read, edit, write, ..., history, new_window):
        tools.add(Tool.from_function(fn))
```

- `inject` / `provides`：声明依赖与提供的服务，`PluginHost` 依赖驱动激活
- `ctx.register(Service, impl)`：注册能力，作用域销毁时自动回滚
- `ctx.on(Event, handler)`：订阅事件，handler 签名为 `(event)`
- `ctx.child()` / `ctx.scope(key)`：作用域分层（覆盖全局能力）
- 插件加载顺序无关；失败单项隔离，不影响其他插件

插件还可以注册**动态提示词段**：每次模型调用前按会话 `metadata` 求值，
由 `PromptService` 组装成 system 消息（对应 PydanticAI capability instructions /
LangChain `dynamic_prompt`）：

```python
from ...spec import PromptOrder, PromptSection, PromptService

@plugin(name="my-plugin", inject=(PromptService,))
async def my_plugin(ctx, config) -> None:
    ctx.get(PromptService).section(
        PromptSection(
            name="my-plugin:units",
            order=PromptOrder.RUNTIME,
            text=lambda context: (
                "Use metric units." if context.metadata.get("metric") else ""
            ),
        )
    )
```

领域插件在包内提供 `plugin.py`。agent 插件与 Web 插件分开声明；Web 插件用字符串惰性引用，避免 agent 进程加载 FastAPI：

```python
# learning/plugin.py
PLUGIN = learning_plugin
WEB_PLUGINS = ["walle.learning.web:PLUGIN"]
```

Web 插件注入 `WebService`，自己挂路由、静态资源和 UI 清单：

```python
@plugin(name="learning-web", inject=(WebService,))
async def learning_web(ctx, config):
    web = ctx.get(WebService)
    web.include_router(create_router(web.shell, web.static_dir))
    web.mount_static("/plugins/learning", static)
    web.register_ui({
        "name": "learning",
        "nav": [{"label": "学习", "href": "/courses"}],
        "styles": ["/plugins/learning/course-view.css"],
        "scripts": ["/plugins/learning/views.js"],
    })
```

Shell 启动时拉取 `/api/web/plugins`，注入导航并加载插件脚本；插件脚本通过
`WallePlugins.register({ match, render, onToolResult, canvasUrl })` 注册路由、
页面、工具结果卡和画布 URL。Web 通道只提供宿主能力，不认识具体领域插件。

### 添加 Skill

```bash
# .agent/skills/code-review/SKILL.md
mkdir -p .agent/skills/code-review
```

```markdown
---
name: code-review
description: Review code changes in the current project.
---

技能正文（工作流/指令，可引用同目录 scripts、references 等资源）...
```

技能不是工具：框架把每个技能的 name/description/路径作为**可用技能清单**
注入 agent 的 system prompt（agent 的 `skills` frontmatter 白名单控制哪些
注入），模型在任务匹配时按需加载对应 SKILL.md 全文后执行。agent 缺省
`skills: []` 不注入任何技能；`["*"]` 表示注入全部。

### 添加 MCP Server

`.agent/mcp.yaml`（`name -> 配置` 映射）：

```yaml
my-server:
  command: npx
  args: ["-y", "@some/mcp-server"]
  enabled: true

http-server:
  url: https://example.com/mcp
  headers:
    Authorization: "Bearer xxx"
  enabled: false
```

### 动态定义工具

Agent 用 `define_tool` 提交代码（顶层 `async def` + docstring 即描述），立即生效并持久化到 `.agent/tools/`：

```python
async def weather(city: str) -> str:
    """查询指定城市天气"""
    return f"{city} 晴 25°C"
```

重启自动恢复，无需手动配置。

### 🪟 上下文窗口管理（模型主动换窗口，对齐 note.md + history 思路）

工作笔记 = 工作目录下**普通文件** `.agent/note.md`（共享、可手动编辑、可 git 管理）：todo / goal / 决策用 markdown 结构自由组织。窗口切换**由模型自己决定**：觉得会话太长、阶段完成时，先 `read` + `edit` 把要点维护进 note.md，再调 `new_window` **硬切**——切点推进，旧轮移出模型视野，窗口只剩当前轮。旧原文在会话 JSONL 中全量保留（切点持久化，重启不失效），需要时用 `history` 工具回源。无压缩插件、无 token 阈值自动折叠。

| 能力 | 说明 |
|---|---|
| `.agent/note.md` | 普通 md 工作笔记：模型 `read` 读、`edit` 局部更新（todo/goal/决策） |
| `edit` 工具 | 通用文件局部替换（与 `read` 对称；写入默认需审批，note.md 可白名单放行） |
| `write` 工具 | 整文件写入（新建/覆盖，目录自动创建；换行与 BOM 沿用原文件；默认需审批） |
| `new_window` | 模型主动硬切：推进投影切点，窗口只剩当前轮 |
| `history` 工具 | 只读检索当前会话完整原文（原始措辞/命令/数值），切窗后回源 |

### 🖼️ Web 画布工具

`canvas` 是内置工具，参数为 `title` / `spec` / `html` / `canvas_id?`：

- 优先 `spec`：由共享 JSXGraph runtime 渲染，适合向量、矩阵、函数图、投影等 2D 数学可视化；
- `html` 仅作为兜底，用于 spec 无法表达的页面；
- 画布写入全局 `data/canvas/<canvas_id>.html`，返回 `canvas_id` / `title` / `version`；
- 课程课节通过 `{{canvas:<canvas_id>}}` 引用同一份全局画布；导出时按引用收集；
- 同标题默认复用同一画布；更新已有画布时传回上次返回的 `canvas_id` 可原地刷新；
- Web 端工具行可展开查看参数，行内「打开画布 / 收起画布」控制右侧面板；
- 画布在 sandbox iframe 中渲染；导出时打包 runtime 与 JSXGraph，离线可用；
- 详细 spec / kit 见 `.agent/skills/canvas-spec/SKILL.md`；
- `conf.yaml.example` 默认 `[allow, canvas]`，不触发审批；画布文件在 `data/` 下，已被 `.gitignore` 排除。

### 📚 课程包

课程包保存课程结构与课节；画布是全局资源，导出时按课节引用收集：

```
data/courses/<course_id>/
├── course.json            # 标题、目标、课节索引（顺序/canvas_ids）
├── progress.json          # 学习进度（已完成课节 / 上次课节）
├── lessons/<lesson>.md    # 课节正文，可混排 Markdown、LaTeX、{{canvas:id}}
└── canvas/<canvas>.html   # 旧课程画布（可选，兼容读取；新画布在 data/canvas/）
```

学习插件（`learning/`）提供的工具与动态指令；核心运行时只认识会话元数据，不认识 course/lesson：

- `create_course(title, goal, lessons?, course_id?)`：创建/更新课程与课节大纲；`lessons` 为有序标题列表；
- `write_lesson(course_id, title, markdown, order?, lesson_id?)`：写入课节 md；先调用 `canvas` 生成画布，再用 `{{canvas:<id>}}` 引用；
- `canvas(title, spec=..., html=..., canvas_id=...)`：内置工具，普通对话和课程共用；课程只保存 `canvas_id`；
- `read_course(course_id?, lesson_id?, outline?)`：课程答疑读取当前课节全文或课程大纲（默认取会话上下文）；
- Web 端 `/courses` 列出课程，`/courses/<id>` 左侧阅读 Markdown/KaTeX，右侧在「画布 / 对话」间切换，并记住上次课节；
- 顶部下拉切换章节，上一课/下一课与标记完成；进入课程回到生成该课程的原会话，切换课节时课程上下文自动更新；
- 空课节提供「填入续写提示」，在右侧「对话」继续生成，按章节顺序补齐后续课节；
- `/courses` 列表显示每门课的完成进度，点击直接回到该课继续位置；
- `/courses/<id>/export` 导出 zip：`index.html` + `data.js` + 本地 marked/KaTeX/DOMPurify + 课节/画布 + `canvas-runtime.js` 与 JSXGraph，解压后离线打开。

### 配置多智能体 Handoff

```python
from walle.core import Agent, Handoff
from walle.tools import Tool

# 工具源：返回工具列表的 callable（运行期新增的 define_tool 工具实时反映）
def all_tools() -> list[Tool]:
    return [search_tool, write_tool]

researcher = Agent(
    name="researcher",
    description="负责信息检索与调研",
    tools=all_tools,
    instruction="你是调研助手...",
)

writer = Agent(
    name="writer",
    description="负责撰写报告",
    tools=all_tools,
    instruction="你是写作助手...",
    handoffs=[Handoff(target=researcher)],  # writer 可移交给 researcher
)
```

代码方式灵活，但角色/工具组合固定时更推荐 **frontmatter 定义**（见上文）：每个 Agent 一个 `.md` 文件，启动按名加载、会话内可切换（`Session.set_agent`），无需改代码。

---

## 📁 项目结构

```
walle/
├── main.py                    # 入口（装配根）
├── plugin/                   # 插件运行时（EventBus / Context / PluginHost）
│   ├── events.py              #   事件类 + 分发（emit / bail / parallel）
│   ├── runtime.py             #   Plugin / Context 作用域 / PluginHost 生命周期
│   └── discovery.py           #   目录与包内 plugin.py 发现
├── conf.yaml.example          # 配置模板
├── pyproject.toml             # 依赖声明 + 显式包映射 + ruff/pytest 配置
├── .github/workflows/ci.yml   # CI：ruff check + pytest（3.12 / 3.13）
├── spec/                      # 协议面（叶子包：数据模型 + 能力协议，不依赖任何实现）
│   ├── protocol/              #   跨层能力协议（唯一声明面）
│   │   ├── channel.py         #     Channel / Sessions / SessionInit
│   │   ├── messages.py        #     Messages / Projection / ProjectionStore
│   │   ├── tool.py            #     ToolService（注册 / schema / 执行）
│   │   ├── prompt.py          #     PromptService（动态提示词段）
│   │   ├── skill.py           #     SkillService（技能目录）
│   │   ├── web.py             #     WebService（插件挂载 Web 路由）
│   │   ├── events.py          #     EventBus（事件总线能力）
│   │   ├── llm.py             #     LLM（模型能力）
│   │   └── view.py            #     SessionView（工具可见会话面）
│   └── schemas/               #   领域数据模型
│       ├── message.py         #     消息类型
│       ├── events.py          #     事件总线载荷（Session/Turn/Tool 事件）
│       ├── channel.py         #     通道载荷（连接/通知/服务 + 判别联合）
│       ├── job.py             #     后台作业（Job/JobStatus + Dispatch/Result）
│       ├── diagnostics.py     #     资源诊断
│       └── usage.py           #     Token 用量
├── core/                      # 核心引擎
│   ├── agent.py               #   Agent / Handoff 模型 + frontmatter 加载/工具筛选
│   ├── runner.py              #   Agent 运行循环
│   ├── executor.py            #   工具执行器（查找 / 裁决 / 超时 / 通知）
│   ├── command.py             #   CommandService 默认实现（Commands）
│   ├── prompt.py              #   PromptService 默认实现（Prompts）
│   ├── session.py             #   Session + SessionManager（会话装配根 + 每会话 Host）
│   └── __init__.py            #   core 公共导出
├── channel/                   # 交互通道
│   ├── transport.py           #   JSON-line 帧传输
│   ├── server.py              #   与通道无关的会话服务端（SessionServer/SessionConn）
│   └── web/                   #   Web 通道（WebSocket ↔ SessionServer）
│       ├── app.py             #     FastAPI 应用与桥接（对话/画布；注册 WebService）
│       ├── plugins.py         #     Web 插件发现（WEB_PLUGINS，惰性导入）
│       ├── index.html         #     Shell：通用导航/插件视图 + 对话 + 画布
│       ├── static/app/        #     conversation.js / plugins.js（通用插件宿主）
│       ├── static/            #     marked / KaTeX / DOMPurify（Markdown、公式与净化）
│       └── __main__.py        #     入口（python -m walle.channel.web）
├── tools/                     # 工具与插件声明
│   ├── mcp.py                 #   MCP 配置 + 客户端（MCPManager.plugin）
│   ├── skill.py               #   技能扫描（native_skill 插件）
│   ├── approval.py            #   审批插件（native_approval：规则 + 人工确认）
│   ├── sandbox.py             #   沙箱插件（bwrap 同名覆盖内置 bash）
│   ├── __init__.py            #   tools 公共导出
│   └── builtin/               #   内置工具插件
│       ├── bash.py            #     Bash 执行
│       ├── read.py            #     文件读取
│       ├── edit.py            #     文件局部替换（降级匹配 + 换行/BOM 保真）
│       ├── write.py           #     整文件写入
│       ├── ask_user.py        #     向用户提问
│       ├── canvas.py          #     Web 画布（spec/HTML 落盘 + CanvasResult）
│       ├── paths.py           #     文件工具路径解析（会话 cwd）
│       ├── defined_tool.py    #     define_tool 动态定义工具
│       ├── job.py             #     后台作业（background / job_result）
│       ├── history.py         #     会话历史（history / new_window）
│       └── plugin.py          #     builtin 插件（向 ToolService 注册工具）
├── learning/                  # 可选学习插件（通用运行时之上）
│   ├── plugin.py              #   课程工具 + 动态提示段 + PLUGIN / WEB_PLUGINS
│   ├── tools.py               #   课程包工具（create_course / write_lesson / read_course）
│   ├── store.py               #   课程包文件存储（course.json / lessons / progress）
│   ├── web.py                 #   课程 Web 插件（路由 / 静态资源 / UI 清单）
│   ├── static/                #   views.js / course-view.css / canvas-runtime.js / vendor
│   ├── web_export.py          #   课程包 zip 导出
│   └── export.html            #   离线课程阅读器模板
├── messages/                  # 消息存储
│   ├── file.py                #   工作目录分组的 JSONL 持久化（默认）
│   ├── in_memory.py           #   内存实现
│   ├── sqlite.py              #   SQLite 持久化（可选）
│   ├── projected.py           #   投影视图（切点截断模型视野）
│   ├── projection_store.py    #   切点持久化（文件 / SQLite 两种后端）
│   └── factory.py             #   装配工厂（build_history / build_ephemeral_history）
├── infra/                     # 基础设施
│   ├── tool.py                #   Tool 值类型 + Tools 会话工具表 + tool_context
│   ├── skill.py               #   会话技能表（Skills）
│   ├── diagnostics.py         #   资源诊断
│   ├── logger.py              #   日志（含 Trace 注入）
│   ├── telemetry.py           #   OpenTelemetry 初始化
│   ├── metrics.py             #   指标定义
│   ├── provider.py            #   LLM Provider（create/stream/set_model）
│   └── sqlite_store.py        #   SQLite 存储工具
├── conf/                      # 配置
│   └── config.py              #   Pydantic 配置模型
├── eval/                      # 评测套件（自建任务 + τ-bench 适配）
│   ├── spec.py                #   任务 schema（YAML → TaskSpec）
│   ├── tasks/                 #   任务定义（YAML）
│   ├── harness.py             #   无头执行器
│   ├── graders.py             #   规则评分器
│   ├── metrics.py             #   指标聚合
│   ├── report.py              #   报告渲染
│   ├── run.py                 #   评测入口
│   └── bench/                 #   τ-bench 公开基准适配
├── tests/                     # 测试（pytest + pytest-asyncio）
│   └── test_layering.py       #   分层依赖约束（AST 静态检查，CI 门禁）
├── observability/             # 可观测性栈
│   ├── docker-compose.yaml    #   OTel + Tempo + Mimir + Grafana
│   └── *.yaml                 #   各服务配置
├── .agent/                    # Agent 运行时持久化
│   ├── agents/                #   Agent 定义（frontmatter Markdown）
│   ├── skills/                #   技能（skill-creator 生成）
│   ├── tools/                 #   模型定义的工具（define_tool）
│   ├── plugins/               #   用户插件（plugin.py，可同名覆盖内置）
│   ├── note.md                #   工作笔记（模型 read/edit 维护，运行时生成）
│   └── mcp.yaml               #   MCP Server 配置（手动编辑）
└── scripts/
    ├── run.sh                 # 控制台入口（子命令 + 旧参数兼容）
    └── lib/                   # common / services / commands 三个职责模块
```

---

## 🛠️ 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.12+ |
| LLM SDK | OpenAI Python SDK（兼容任意 OpenAI API 格式模型） |
| 数据模型 | Pydantic v2 |
| 工具协议 | MCP (Model Context Protocol) |
| 可观测性 | OpenTelemetry + Grafana + Tempo + Mimir |
| 持久化 | 文件（JSONL，默认）+ SQLite（可选） |
| Web | FastAPI + WebSocket + 原生 JS（marked + KaTeX + DOMPurify） |
| 配置 | YAML + Pydantic |
| 异步 | asyncio |

---

## 🧪 评测

`eval/` 是自建的能力评测套件：无头执行（真实 LLM + 内置工具，无人工交互），
按域覆盖核心引擎能力，自动评分并生成报告。

### 任务域（14 任务）

| 域 | 任务数 | 覆盖能力 |
|---|---|---|
| bash | 5 | shell 统计 / 文件读写 |
| combined | 3 | bash 多工具流水线 |
| define_tool | 2 | 模型运行期定义工具并立即使用 |
| background | 2 | 后台作业派发 → job_result 取回 |
| handoff | 2 | 多智能体链式移交 |

### 运行

```bash
PYTHONPATH=.. .venv/bin/python -m walle.eval.run             # 全量 14 任务
PYTHONPATH=.. .venv/bin/python -m walle.eval.run --smoke     # 冒烟（1 任务）
PYTHONPATH=.. .venv/bin/python -m walle.eval.run --domain bash
PYTHONPATH=.. .venv/bin/python -m walle.eval.run --repeat 3  # 每任务 3 次取均值
PYTHONPATH=.. .venv/bin/python -m walle.eval.run --render-only   # 重渲染上次报告（不调 LLM）
```

输出到 `eval/report/`：`report.md`（总体/分域/明细/失败详情，含与上次的趋势对比）、
`results.csv`、`results.json`（趋势对比用）、`results_detail.json`（逐任务完整明细，
供 `--render-only` 重渲染）。评分规则：最终输出 exact/contains/numeric/regex 判定 +
期望工具调用序列（顺序敏感子序列）双检。全部任务级失败（如 provider 故障）时不会覆盖已有报告。

### 结果（2026-08-15，deepseek-v4-flash，temperature=0）

| 指标 | 值 |
|---|---|
| 成功率 | 14/14 (100%) |
| 平均轮次 | 2.7 |
| 平均 token/任务 | 2,041 |
| 平均耗时/任务 | 7.7s |
| 工具调用（总/错误） | 36 / 2 |

> 自建套件为回归基线（任务与评分可复现；因需真实 LLM 调用，需 API Key 手动运行，
> 不进 CI——CI 跑的是无需 Key 的单元测试与静态检查）；公开基准见下节（能力对标）。

---

## 🏆 公开基准：τ-bench

[τ-bench](https://github.com/sierra-research/tau-bench)（Sierra）零售客服基准：
状态机环境 + LLM 用户模拟，reward 由数据库状态 hash 与输出匹配**确定性计算**，
数字可直接对标论文基线。

### 接入方式

`eval/bench/` 只借 τ-bench 的**数据集 + 状态机环境 + 评分**，不借它的 agent 协议
与 LLM 调用：整个环境（工具执行 + 用户模拟）折叠成 Walle 工具集，由 **Walle Runner
驱动 ReAct 循环**；用户模拟器走同一网关（litellm 默认端点不可达，自实现
`WalleUserSimulationEnv`）。模型纯文本输出按官方协议兜底为 respond 继续对话。

```bash
# 安装（PyPI 未发布，需 GitHub；代理环境先下 tarball）
pip install "tau-bench @ git+https://github.com/sierra-research/tau-bench.git"

PYTHONPATH=.. .venv/bin/python -m walle.eval.bench.run_tau --limit 5     # 小规模验证
PYTHONPATH=.. .venv/bin/python -m walle.eval.bench.run_tau              # 全量 retail test（115 用例）
PYTHONPATH=.. .venv/bin/python -m walle.eval.bench.run_tau --env airline --split dev
```

报告输出到 `eval/report/tau/`（复用自建套件的报告管线）。支持 `--concurrency N`
线程池并发（每用例独立 env/provider）与 `--resume` 断点续跑（每完成一个
用例即写盘）。

### 结果（retail test 全量 115 用例，deepseek-v4-flash）

| 指标 | 值 |
|---|---|
| 成功率 | **97/115 (84.3%)** |
| 平均轮次 | 10.8 |
| 平均 token/用例 | 73,602 |
| 平均耗时/用例 | 72.9s |
| 工具调用（总/错误） | 924 / 11 |

> 用户模拟器与 agent 同模型（deepseek-v4-flash），若用更强模型模拟用户，
> 分数通常还有提升空间。失败用例集中在模型任务判断（如提前结束对话），
> 工具错误 11 次均被模型自愈重试。
> 单任务执行日志可直接观察：`--task <name> --repeat 3` 用于稳定性测量。

