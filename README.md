# Walle

一个从零构建的 AI Agent 框架，支持工具调用、流式输出、多智能体协作、MCP 协议集成，并内置完整的 OpenTelemetry 可观测性方案。

## 架构

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
     │  (交互抽象)   │           │  (Agent 循环) │
     │  CLIChannel  │           │  流式/批量执行 │
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
                               ┌─────┴──────┐
                               │  ToolReg.  │
                               │ 内置 + MCP  │
                               └────────────┘
```

## 核心特性

- **Agent 循环引擎** — 支持 ReAct 式多轮工具调用，可配置最大轮次，流式与非流式两种模式
- **多智能体 Handoff** — Agent 可将任务移交给其他 Agent，支持链式协作
- **MCP 协议集成** — 对接任意 MCP Server（stdio / Streamable HTTP），自动发现并注册远程工具
- **工具审批系统** — 基于 glob 模式的三态策略（allow / deny / ask），按工具名和参数粒度控制
- **工具超时保护** — 可配置全局超时，防止工具卡死阻塞 Agent 循环
- **会话压缩** — 当 token 超过阈值时自动触发摘要压缩，保留关键信息，降低上下文成本
- **可插拔架构** — Channel、Session、Compressor 均为 Protocol 接口，可按需替换实现
- **全链路可观测** — OpenTelemetry Traces + Metrics，对接 Grafana / Tempo / Mimir 可视化栈
- **Skill 系统** — 从 `.agent/skills/` 目录加载 Markdown 技能文件，动态注入为工具

## 快速开始

### 前置条件

- Python >= 3.11
- Docker + Docker Compose（可选，用于可观测性栈）

### 安装

```bash
git clone <repo-url> walle
cd walle
pip install -e ".[dev]"
```

### 配置

```bash
cp conf.yaml.example conf.yaml   # 编辑配置（MCP Server、审批规则等）
cp .env.example .env             # 填入 LLM API Key
```

### 启动

```bash
# 启动 agent + 可观测性容器（默认）
./scripts/run.sh

# 仅启动 agent，不启动可观测性
./scripts/run.sh --no-obs

# 仅启动可观测性容器
./scripts/run.sh --obs-only

# 停止可观测性容器
./scripts/run.sh --stop-obs

# 运行测试
./scripts/run.sh --test
```

启动后在终端输入消息即可与 Agent 对话，输入 `:q` 退出。

### 可观测性面板

启动可观测性容器后访问：

| 服务 | 地址 | 账号 |
|------|------|------|
| Grafana | http://localhost:3000 | admin / admin |
| Tempo | http://localhost:3200 | - |
| Mimir | http://localhost:9009 | - |

在 Grafana 中可查看 Agent 运行链路（Traces）和指标（Metrics），包括：
- Agent 每轮迭代的耗时与 Span
- 工具调用的次数、耗时、错误率
- 对话压缩触发次数与前后消息数
- Agent Handoff 事件

## 架构设计

### 分层职责

| 层 | 目录 | 职责 |
|---|---|---|
| 入口 | `main.py` | 依赖注入组装，启动 REPL 循环 |
| 核心引擎 | `core/` | Agent 模型、运行循环、工具执行、审批策略 |
| 交互通道 | `channel/` | 用户 I/O 抽象，CLI 实现 |
| 工具系统 | `tools/` | 工具注册、MCP 客户端、内置工具 |
| 会话管理 | `session/` | 消息存储、压缩策略、持久化 |
| 数据模型 | `schemas/` | 消息、事件、Token 用量的 Pydantic 模型 |
| 基础设施 | `infra/` | 日志、遥测、指标、LLM Provider |
| 配置 | `conf/` | Pydantic 配置模型 + YAML 加载 |
| 可观测性 | `observability/` | Docker Compose 编排的监控栈 |

### 核心流程

```
用户输入 → Channel.receive()
         → Session.add(UserMessage)
         → Runner.run() 循环:
             1. 构建消息列表 (历史 + System Instruction)
             2. 调用 LLM (流式/批量)
             3. 若有 tool_calls → ToolExecutor 并发执行
                ├─ 审批检查 (ApprovalPolicy)
                ├─ 执行工具 (内置 / MCP)
                └─ 返回结果到 Session
             4. 若有 Handoff → 切换 Agent，继续循环
             5. 无 tool_calls → 返回最终结果
         → Channel.send(TextDelta) 流式输出
```

## 配置

主配置文件为 `conf.yaml`：

```yaml
log:
  level: "DEBUG"
  path: "logs/agent.log"
  backup_count: 30

telemetry:
  enabled: true
  otlp:
    endpoint: "http://localhost:4317"
    insecure: true

approval:
  rules:
    - [deny, bash(cmd=rm -rf /)]    # 危险命令直接拒绝
    - [allow, bash(cmd=ls -la *)]   # 安全命令自动放行
    - [allow, ask_user]             # 提问工具自动放行
  default: ask                       # 默认需人工审批

tool_timeout: 30                     # 工具执行超时（秒），null 禁用

mcp:
  tavily:
    url: https://mcp.tavily.com/mcp
    headers:
      Authorization: "Bearer your-key"
    enabled: false
```

### 审批规则

规则格式为 `[action, pattern]`，pattern 支持 glob 匹配：

- `bash` — 匹配所有 bash 调用
- `bash(cmd=rm -rf *)` — 匹配特定参数模式
- `ask_user` — 匹配工具名

action 三种：`allow`（放行）、`deny`（拒绝）、`ask`（人工确认）。

## 扩展

### 添加内置工具

```python
# tools/builtin/my_tool.py
from .. import tool_context

async def my_tool(query: str) -> str:
    """工具描述，会自动生成 schema。"""
    ctx = tool_context.get()
    # 通过 ctx 访问 channel / session / provider
    return f"result: {query}"
```

在 `ToolRegistry` 中注册：

```python
# tools/registry.py
self.add_function(my_tool)
```

### 添加 Skill

在 `.agent/skills/` 下创建目录，放入 `SKILL.md`：

```markdown
---
name: code-review
description: Review code changes in the current project.
---

技能的 system prompt 内容...
```

框架启动时自动加载为工具，Agent 可调用注入上下文。

### 添加 MCP Server

在 `conf.yaml` 中配置：

```yaml
mcp:
  my-server:
    command: npx
    args: ["-y", "@some/mcp-server"]
    enabled: true
```

或使用 HTTP 模式：

```yaml
mcp:
  my-server:
    url: https://example.com/mcp
    headers:
      Authorization: "Bearer xxx"
```

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

## 项目结构

```
walle/
├── main.py                    # 入口
├── conf.yaml.example          # 配置模板（复制为 conf.yaml 使用）
├── pyproject.toml             # 依赖声明
├── core/                      # 核心引擎
│   ├── agent.py               #   Agent / Handoff 模型
│   ├── runner.py              #   Agent 运行循环
│   ├── executor.py            #   工具执行器（审批·并发·超时）
│   └── approval.py            #   审批规则引擎
├── channel/                   # 交互通道
│   └── channel.py             #   Channel Protocol + CLI 实现
├── tools/                     # 工具系统
│   ├── tool.py                #   Tool 模型 + ContextVar
│   ├── registry.py            #   工具注册表
│   ├── mcp.py                 #   MCP 客户端
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
│   ├── channel.py             #   通道事件
│   └── usage.py               #   Token 用量
├── infra/                     # 基础设施
│   ├── logger.py              #   日志（含 Trace 注入）
│   ├── telemetry.py           #   OpenTelemetry 初始化
│   ├── metrics.py             #   指标定义
│   └── provider.py            #   LLM Provider
├── conf/                      # 配置
│   └── config.py              #   Pydantic 配置模型
├── tests/                     # 测试
│   ├── conftest.py            #   公共 fixture 与 mock
│   ├── test_approval.py       #   审批规则测试
│   ├── test_executor.py       #   工具执行器测试
│   ├── test_runner.py         #   Agent 循环测试
│   ├── test_tools.py          #   工具注册测试
│   ├── test_session_memory.py #   内存会话测试
│   ├── test_session_sqlite.py #   SQLite 持久化测试
│   ├── test_session_compressible.py  # 压缩会话测试
│   └── test_policies.py       #   压缩策略测试
├── observability/             # 可观测性栈
│   ├── docker-compose.yaml    #   OTel + Tempo + Mimir + Grafana
│   └── *.yaml                 #   各服务配置
├── .agent/skills/             # Agent 技能
│   └── grilling/SKILL.md      #   逼问技能
└── scripts/
    └── run.sh                 # 一键启动脚本
```

## 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.11+ |
| LLM SDK | OpenAI Python SDK（兼容任意 OpenAI API 格式的模型） |
| 数据模型 | Pydantic v2 |
| 工具协议 | MCP (Model Context Protocol) |
| 可观测性 | OpenTelemetry + Grafana + Tempo + Mimir |
| 持久化 | SQLite |
| 配置 | YAML + Pydantic |
| 异步 | asyncio |
