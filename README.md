# Walle 🤖

> 一个从零构建的 AI Agent 框架 —— 工具调用 · 多智能体 · MCP · 自我进化 · 全链路可观测

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![Pydantic](https://img.shields.io/badge/Pydantic-v2-E92063?logo=pydantic&logoColor=white)](https://docs.pydantic.dev)
[![MCP](https://img.shields.io/badge/MCP-Protocol-0A9F77?logo=modelcontextprotocol)](https://modelcontextprotocol.io)
[![OpenTelemetry](https://img.shields.io/badge/Observability-OpenTelemetry-425CC7?logo=opentelemetry)](https://opentelemetry.io)

---

## ✨ 核心特性

| 特性 | 说明 |
|---|---|
| ⚙️ **Agent 循环引擎** | ReAct 式多轮工具调用，流式/非流式双模式，可配置最大轮次 |
| 🤝 **多智能体 Handoff** | Agent 可移交任务，支持链式协作 |
| 🔌 **MCP 协议集成** | 对接任意 MCP Server（stdio / Streamable HTTP），自动发现工具 |
| 🛡️ **工具治理** | glob 三态审批（allow / deny / ask）+ 超时保护，按工具名 + 参数粒度控制 |
| 🐍 **CodeAct 执行** | 持久 Jupyter kernel，Python 状态跨调用保留，异常返回 traceback 供自我调试 |
| 📈 **全链路可观测** | OpenTelemetry Traces + Metrics → Grafana / Tempo / Mimir |
| 📱 **飞书集成** | 应用机器人：长连接接收消息 + 流式更新回复（打字机效果），CLI 可降级为观察者 |
| 🔄 **自我进化** | 用代码定义工具（`define_tool`）、动态接入 MCP（`add_mcp`）、沉淀技能（Skill），持久化 `.agent/` 重启恢复 |

---

## 🏗️ 架构

```
┌─────────────────────────────────────────────────────────────┐
│                        main.py (入口)                        │
│                   组装依赖 · 启动 REPL 循环                   │
└───────────────────────────┬─────────────────────────────────┘
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
     ┌──────────────┐           ┌──────────────┐
     │   Channel    │           │    Runner    │
     │  (notify/call)│           │  (Agent 循环) │
     │ CLI/Fanout/Feishu│        │  流式/批量执行 │
     └──────────────┘           └──────┬───────┘
                                       │
                    ┌──────────────────┼──────────────────┐
                    ▼                  ▼                  ▼
            ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
            │   Session    │  │ ToolExecutor │  │    Agent     │
            │  会话管理     │  │  工具执行器   │  │  智能体定义   │
            │  Memory/SQLite│ │  审批·并发     │  │  Handoff     │
            │  自动压缩     │  └──────┬───────┘  └──────────────┘
            └──────────────┘         │
                               ┌─────┴──────────────┐
                               │  ToolRegistry       │
                               │  ├ MCP (远程工具)    │
                               │  └ DefinedTool (定义)│
                               └────────────────────┘
```

### 核心流程

```
用户输入 → Channel.call(Receive())     # 服务：读输入，有返回
         → Session.add(UserMessage)
         → Runner.run() 循环:
             1. 构建消息列表 (历史 + System Instruction)
             2. 调用 LLM (流式/批量)
             3. 若有 tool_calls → ToolExecutor 并发执行
                ├─ 审批检查 (ApprovalPolicy → Approver)
                ├─ 执行工具 (内置 / MCP / 动态)
                └─ 返回结果到 Session
             4. 若有 Handoff → 切换 Agent，继续循环
             5. 无 tool_calls → 返回最终结果
         → Channel.notify(Delta) 流式输出     # 通知：广播，无返回
执行中 Ctrl+C → 取消当前 run，回到输入提示；空输入（直接回车）/ 空闲 Ctrl+C → 退出（统一出口）
```

### 分层职责

| 层 | 目录 | 职责 |
|---|---|---|
| 入口 | `main.py` | 依赖注入组装，启动 REPL 循环 |
| 核心引擎 | `core/` | Agent 模型、运行循环、工具执行、审批策略 |
| 交互通道 | `channel/` | Channel 协议（notify 广播 / call 点对点）、CLI 实现、Fanout 多观察者、飞书应用机器人（长连接收发）、审批/提问服务 |
| 工具系统 | `tools/` | 注册表、MCP 客户端、内置工具、动态工具摄入 |
| 会话管理 | `session/` | 消息存储、压缩策略、持久化 |
| 数据模型 | `schemas/` | 消息、判别联合事件（通知/服务）、Token 用量的 Pydantic 模型 |
| 基础设施 | `infra/` | 日志、遥测、指标、LLM Provider、Jupyter kernel |
| 配置 | `conf/` | Pydantic 配置模型 + YAML 加载 |
| 可观测性 | `observability/` | Docker Compose 编排的监控栈 |

---

## 🚀 快速开始

### 前置条件

- **Python ≥ 3.11**
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
./scripts/run.sh --no-obs        # 仅 agent
./scripts/run.sh --feishu        # 飞书交互 + 可观测性
./scripts/run.sh --obs-only      # 仅可观测性容器
./scripts/run.sh --stop-obs      # 停止可观测性
./scripts/run.sh --test          # 运行测试
```

启动后在终端输入消息即可对话，直接回车或按 Ctrl+C 退出。

> `--feishu` 模式需先在 `conf.yaml` 配置 `feishu.app_id` / `feishu.app_secret`；未配置会报错提示。

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
  timeout: 30                       # 工具执行超时（秒），null 禁用
  approval:
    rules:
      - [allow, mcp_obsidian*]        # MCP 工具自动放行
      - [ask, jupyter]                # 代码执行默认需人工确认
      - [deny, bash(cmd=rm -rf /)]    # 危险命令直接拒绝
      - [allow, bash(cmd=ls -la *)]   # 安全命令自动放行
      - [allow, ask_user]             # 提问工具自动放行
    default: ask                      # 默认需人工审批

feishu:
  app_id: ""          # 应用机器人 App ID（开发者后台 → 凭证与基础信息），留空则不启用
  app_secret: ""       # 应用机器人 App Secret
```

#### 飞书集成

创建企业自建应用并开启机器人能力，配置 `im:message` 系列权限，在开发者后台将订阅方式设为「使用长连接接收事件」。填入 `conf.yaml` 的 `feishu.app_id` / `feishu.app_secret` 后，以飞书模式启动：

```bash
./scripts/run.sh --feishu
```

- 用户在飞书发消息 → agent 处理 → 回复到飞书
- 流式回复通过「创建消息 → 更新消息」实现打字机效果
- 工具调用 / 结果 / 错误即时推送
- CLI 降级为本地只读观察者（`ConsoleObserver`），方便调试

#### 审批规则

规则格式 `[action, pattern]`，pattern 支持 glob：

| action | 含义 |
|---|---|
| `allow` | 放行 |
| `deny` | 拒绝 |
| `ask` | 人工确认（默认） |

示例匹配：`bash`（全部 bash）、`bash(cmd=rm -rf *)`（特定参数）、`ask_user`（工具名）。

### `.agent/` — 运行时持久化

| 路径 | 内容 | 写入方式 |
|---|---|---|
| `.agent/skills/` | 技能（SKILL.md + 可选 scripts/assets） | `skill-creator` 或手动 |
| `.agent/tools/` | 模型定义的代码工具 | `define_tool` |
| `.agent/mcp.yaml` | MCP Server 配置 | `add_mcp` 或手动编辑 |

三者均在下次启动自动恢复。

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

---

## 📁 项目结构

```
walle/
├── main.py                    # 入口
├── conf.yaml.example          # 配置模板
├── pyproject.toml             # 依赖声明
├── core/                      # 核心引擎
│   ├── agent.py               #   Agent / Handoff 模型
│   ├── runner.py              #   Agent 运行循环
│   ├── executor.py            #   工具执行器（审批·并发·超时）
│   └── approval.py            #   审批规则引擎
├── channel/                   # 交互通道
│   ├── channel.py             #   Channel Protocol (notify/call) + CLI 实现
│   ├── fanout.py              #   FanoutChannel 通知侧多观察者
│   ├── feishu.py              #   飞书应用机器人（长连接收发 + 流式）
│   └── observers.py           #   LogObserver / ConsoleObserver
├── tools/                     # 工具系统
│   ├── tool.py                #   Tool 模型 + ContextVar
│   ├── registry.py            #   工具注册表
│   ├── mcp.py                 #   MCP 配置 + 客户端
│   ├── defined.py             #   模型定义工具（校验/持久化）
│   └── builtin/               #   内置工具
│       ├── bash.py            #     Bash 执行
│       ├── ask_user.py        #     向用户提问
│       └── skill.py           #     Skill 加载器

├── session/                   # 会话管理
│   ├── protocol.py            #   Session Protocol
│   ├── memory.py              #   内存实现
│   ├── sqlite.py              #   SQLite 持久化
│   ├── compressible_session.py#   可压缩会话装饰器
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
├── observability/             # 可观测性栈
│   ├── docker-compose.yaml    #   OTel + Tempo + Mimir + Grafana
│   └── *.yaml                 #   各服务配置
├── .agent/                    # Agent 运行时持久化
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
| 语言 | Python 3.11+ |
| LLM SDK | OpenAI Python SDK（兼容任意 OpenAI API 格式模型） |
| 数据模型 | Pydantic v2 |
| 工具协议 | MCP (Model Context Protocol) |
| 可观测性 | OpenTelemetry + Grafana + Tempo + Mimir |
| 持久化 | SQLite |
| 配置 | YAML + Pydantic |
| 异步 | asyncio |

