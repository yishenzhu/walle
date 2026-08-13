# Walle 🤖

> 一个从零构建的 AI Agent 框架 —— 工具调用 · 多智能体 · MCP · 自我进化 · 全链路可观测

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![Pydantic](https://img.shields.io/badge/Pydantic-v2-E92063?logo=pydantic&logoColor=white)](https://docs.pydantic.dev)
[![MCP](https://img.shields.io/badge/MCP-Protocol-0A9F77?logo=modelcontextprotocol)](https://modelcontextprotocol.io)
[![OpenTelemetry](https://img.shields.io/badge/Observability-OpenTelemetry-425CC7?logo=opentelemetry)](https://opentelemetry.io)

---

## ✨ 核心特性

| 特性 | 说明 |
|---|---|
| ⚙️ **Agent 循环引擎** | ReAct 式多轮工具调用，流式/非流式双模式，可配置最大轮次 |
| 📝 **Agent 可配置化** | `.agent/agents/*.md` frontmatter 定义（角色/温度/工具筛选），启动按名加载，会话内可切换 |
| 🤝 **多智能体 Handoff** | Agent 可移交任务，支持链式协作 |
| 🔌 **MCP 协议集成** | 对接任意 MCP Server（stdio / Streamable HTTP），自动发现工具 |
| 🛡️ **工具治理** | glob 三态审批（allow / deny / ask）+ 超时保护，按工具名 + 参数粒度控制 |
| 🐍 **CodeAct 执行** | 持久 Jupyter kernel，Python 状态跨调用保留，异常返回 traceback 供自我调试 |
| 📈 **全链路可观测** | OpenTelemetry Traces + Metrics → Grafana / Tempo / Mimir |
| 💬 **CLI 多会话** | JSON-line 协议多客户端并发会话，流式/非流式回复 |
| 🔄 **自我进化** | 用代码定义工具（`define_tool`）、动态接入 MCP（`add_mcp`）、沉淀技能（Skill），持久化 `.agent/` 重启恢复 |

---

## 🏗️ 架构

```mermaid
flowchart TD
    Main["main.py<br/>组装依赖 · 启动循环"]
    Channel["Channel<br/>notify / call<br/>CLI 多会话 (JSON-line)"]
    Runner["Runner<br/>Agent 循环 · 流式/批量"]
    Session["Session<br/>会话实体 · attach/detach<br/>Memory / SQLite · 自动压缩"]
    Exec["ToolExecutor<br/>工具执行器<br/>审批 · 并发 · 超时"]
    AgentNode["Agent<br/>智能体定义<br/>Handoff · 工具筛选"]
    Reg["ToolRegistry<br/>MCP 远程工具<br/>DefinedTool 定义工具"]

    Main --> Channel
    Main --> Runner
    Runner --> Session
    Runner --> Exec
    Runner --> AgentNode
    Exec --> Reg

    classDef entry fill:#e8f5e9,stroke:#2e7d32
    classDef core fill:#e3f2fd,stroke:#1565c0
    classDef tool fill:#fff3e0,stroke:#e65100
    class Main entry
    class Runner,Session,Exec,AgentNode core
    class Channel,Reg tool
```

### 核心流程

```mermaid
sequenceDiagram
    participant U as 用户
    participant C as Channel
    participant S as Session
    participant R as Runner
    participant T as ToolExecutor
    participant M as LLM

    U->>C: 输入
    C->>S: handle(UserInput)
    S->>R: run(agent, input)
    loop 多轮迭代
        R->>M: 调用 LLM（流式/批量）
        M-->>R: 返回（tool_calls / 最终回复）
        alt 有 tool_calls
            R->>T: 并发执行
            T->>T: 审批检查 → 执行
            T-->>R: 结果
            alt 含 Handoff
                R->>R: 切换 Agent，继续循环
            end
        else 无 tool_calls
            R-->>S: 最终结果
        end
    end
    S-->>C: notify(Delta) 流式输出
    C-->>U: 回复
```

执行中 Ctrl+C → 取消当前 run，回到输入提示；空输入（直接回车）/ 空闲 Ctrl+C → 退出（统一出口）

### 分层职责

| 层 | 目录 | 职责 |
|---|---|---|
| 入口 | `main.py` | 依赖注入组装，启动 REPL 循环 |
| 核心引擎 | `core/` | Agent 模型、运行循环、工具执行、审批策略 |
| 交互通道 | `channel/` | Channel 协议（notify 广播 / call 点对点）、CLI 多会话服务端（JSON-line 协议） |
| 工具系统 | `tools/` | 注册表、MCP 客户端、内置工具、动态工具摄入 |
| 消息存储 | `messages/` | 消息协议、内存/SQLite 持久化、压缩策略（会话实体在 `core/session.py`） |
| 数据模型 | `schemas/` | 消息、判别联合事件（通知/服务）、Token 用量的 Pydantic 模型 |
| 基础设施 | `infra/` | 日志、遥测、指标、LLM Provider、Jupyter kernel |
| 配置 | `conf/` | Pydantic 配置模型 + YAML 加载 |
| 可观测性 | `observability/` | Docker Compose 编排的监控栈 |

---

## 🚀 快速开始

### 前置条件

- **Python ≥ 3.12**（pyproject.toml 要求）
- **Docker + Docker Compose**（可选，用于可观测性栈）

### 安装

```bash
git clone <repo-url> walle
cd walle
pip install -e ".[dev]"
```

### 配置

```bash
cp conf.yaml.example conf.yaml   # 主配置（日志 / 审批）
cp .env.example .env             # LLM API Key
```

### 启动

```bash
./scripts/run.sh                 # 启动 agent + 可观测性（默认）
./scripts/run.sh --no-obs        # 仅启动 agent
./scripts/run.sh --cli           # 服务端 + CLI 客户端（一键对话）
./scripts/run.sh --status        # 服务端状态 + 会话列表
./scripts/run.sh --attach <id>   # 恢复（attach）已有会话
./scripts/run.sh --stop          # 停止常驻 agent 服务端
./scripts/run.sh --obs-only      # 仅启动可观测性容器
./scripts/run.sh --stop-obs      # 停止可观测性
./scripts/run.sh --test          # 运行测试
```

启动后可用 `python -m walle.channel.cli` 连接对话（JSON-line 协议多会话）。

会话是**持久实体**（跨连接存活）：连接断开 → `detach` 保留状态（历史/kernel），可 `--attach <id>` 重连恢复；连接接入 → `attach` 绑定新传输。真正销毁走服务端停机（`--stop`）。服务端空闲 Ctrl+C 退出。

### 📊 可观测性面板

| 服务 | 地址 | 账号 |
|------|------|------|
| Grafana | http://localhost:3000 | admin / admin |
| Tempo | http://localhost:3200 | - |
| Mimir | http://localhost:9009 | - |

可查看：Agent 每轮迭代耗时与 Span、工具调用次数/耗时/错误率、会话压缩触发、Handoff 事件。

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
      - [ask, jupyter]                # 代码执行默认需人工确认
      - [allow, ask_user]             # 提问工具自动放行
      - [allow, grilling]             # 技能工具自动放行
    default: ask                      # 默认需人工审批

session:
  storage: "sqlite"                   # sqlite | memory
  db_path: "data/session.db"          # sqlite 存储路径（相对项目根）

# MCP server 配置不在此文件，统一放在 .agent/mcp.yaml（模型可动态添加）
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
| `.agent/mcp.yaml` | MCP Server 配置 | `add_mcp` 或手动编辑 |

以上均在下次启动自动恢复。

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

你是一名资深编码助手。优先使用 python/jupyter 完成任务，禁止 bash 执行任意命令。
```

| frontmatter 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 必填，必须等于文件名 |
| `description` | string | 可选，Agent 描述 |
| `temperature` | float | 可选，采样温度 |
| `tools.allow` | list[string] | 可选，允许的工具 glob（默认 `["*"]` 全放行） |
| `tools.deny` | list[string] | 可选，拒绝的工具 glob（优先于 allow） |

- **工具筛选**：`deny` 优先于 `allow`，支持 `mcp_obsidian*` 等 glob 通配；工具源实时反映运行时 `define_tool` / `add_mcp` 新增的工具
- **默认 Agent**：`.agent/agents/default.md`，未指定 agent 名时加载
- **会话内切换**：API `Session.set_agent(name)` 按名切换（历史/kernel 保留）；未指定时用默认 agent

---

## 🔧 扩展

### 添加内置工具

```python
# tools/builtin/my_tool.py
from .. import tool_context

async def my_tool(query: str) -> str:
    """工具描述，会自动生成 schema。"""
    ctx = tool_context.get()   # 访问 ToolContext（kernel / interact）
    return f"result: {query}"
```

```python
# tools/registry.py
self.add_function(my_tool)
```

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

技能的 system prompt 内容...
```

框架启动时自动加载为工具。

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

Agent 也可在对话中用 `add_mcp` 动态添加，连接成功后自动持久化。

### 动态定义工具

Agent 用 `define_tool` 提交代码（顶层 `async def` + docstring 即描述），立即生效并持久化到 `.agent/tools/`：

```python
async def weather(city: str) -> str:
    """查询指定城市天气"""
    return f"{city} 晴 25°C"
```

重启自动恢复，无需手动配置。

### 配置多智能体 Handoff

```python
from walle.core import Agent, Handoff

researcher = Agent(
    name="researcher",
    description="负责信息检索与调研",
    tools=[search_tool],
    instruction="你是调研助手...",
)

writer = Agent(
    name="writer",
    description="负责撰写报告",
    tools=[write_tool],
    instruction="你是写作助手...",
    handoffs=[Handoff(target=researcher)],  # writer 可移交给 researcher
)
```

代码方式灵活，但角色/工具组合固定时更推荐 **frontmatter 定义**（见上文）：每个 Agent 一个 `.md` 文件，启动按名加载、会话内可切换（`Session.set_agent`），无需改代码。

---

## 📁 项目结构

```
walle/
├── main.py                    # 入口
├── conf.yaml.example          # 配置模板
├── pyproject.toml             # 依赖声明
├── core/                      # 核心引擎
│   ├── agent.py               #   Agent / Handoff 模型 + frontmatter 加载/工具筛选
│   ├── runner.py              #   Agent 运行循环
│   ├── executor.py            #   工具执行器（审批·并发·超时）
│   ├── approval.py            #   审批规则引擎
│   └── session.py             #   会话实体（attach/detach，支持切换 Agent）
├── channel/                   # 交互通道
│   ├── protocol.py            #   Channel Protocol (notify/call)
│   └── cli.py                 #   CLI 多会话服务端（JSON-line 协议）
├── tools/                     # 工具系统
│   ├── tool.py                #   Tool 模型 + ContextVar
│   ├── registry.py            #   工具注册表
│   ├── mcp.py                 #   MCP 配置 + 客户端
│   ├── defined.py             #   模型定义工具（校验/持久化）
│   └── builtin/               #   内置工具
│       ├── bash.py            #     Bash 执行
│       ├── ask_user.py        #     向用户提问
│       └── skill.py           #     Skill 加载器

├── messages/                  # 消息存储
│   ├── protocol.py            #   Messages Protocol
│   ├── in_memory.py           #   内存实现
│   ├── sqlite.py              #   SQLite 持久化
│   ├── compressible.py        #   可压缩消息装饰器
│   ├── compressors.py         #   摘要压缩器
│   └── policies.py            #   压缩触发策略
├── schemas/                   # 数据模型
│   ├── message.py             #   消息类型
│   ├── events.py              #   判别联合事件（通知/服务）
│   ├── channel.py             #   服务载荷（UserInput / ApprovalRsp）
│   └── usage.py               #   Token 用量
├── infra/                     # 基础设施
│   ├── logger.py              #   日志（含 Trace 注入）
│   ├── telemetry.py           #   OpenTelemetry 初始化
│   ├── metrics.py             #   指标定义
│   ├── provider.py            #   LLM Provider
│   └── jupyter.py             #   Jupyter kernel（持久 Python 解释器）
├── conf/                      # 配置
│   └── config.py              #   Pydantic 配置模型
├── tests/                     # 测试（pytest + pytest-asyncio）
├── observability/             # 可观测性栈
│   ├── docker-compose.yaml    #   OTel + Tempo + Mimir + Grafana
│   └── *.yaml                 #   各服务配置
├── .agent/                    # Agent 运行时持久化
│   ├── agents/                #   Agent 定义（frontmatter Markdown）
│   ├── skills/                #   技能（skill-creator 生成）
│   ├── tools/                 #   模型定义的工具（define_tool）
│   └── mcp.yaml               #   MCP Server 配置（add_mcp）
└── scripts/
    └── run.sh                 # 一键启动脚本
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
| 持久化 | SQLite |
| 配置 | YAML + Pydantic |
| 异步 | asyncio |

