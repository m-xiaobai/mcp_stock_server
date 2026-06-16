# MCP Stock Server Refactor Design

> 基于 `work-mcp` 设计思路，面向当前股票域 `mcp_stock_server` 仓库的分层重构设计文档。

## 目标

本次重构的目标不是把当前仓库改造成 Jira / Slack / Notion 协作系统，而是复用 `work-mcp` 的分层、治理、审计与能力清单设计方法，重构现有股票域 MCP Server，使其具备以下特性：

- 稳定的 MCP 协议行为
- 清晰的工具定义与注册边界
- 可扩展的治理与审计链路
- 面向后续能力注册的 manifest 输出
- 尽量保持现有工具名与主要调用参数兼容

本轮采用分阶段对齐策略，优先建立架构骨架与统一调用链，不在第一步引入外部身份系统或大规模业务改造。

## 当前仓库现状

当前仓库核心结构包括：

- [server.py](D:/nanobot%20notebook/mcp_stock_server/server.py)：直接使用 `FastMCP` 注册工具
- [main.py](D:/nanobot%20notebook/mcp_stock_server/main.py)：装配 MySQL / demo service 并启动 server
- [tools](D:/nanobot%20notebook/mcp_stock_server/tools)：承载参数解析、服务调用、返回序列化，以及部分工具编排逻辑
- [services](D:/nanobot%20notebook/mcp_stock_server/services)：承载股票查询、写入、技术面快照等业务逻辑
- [repositories](D:/nanobot%20notebook/mcp_stock_server/repositories)：数据库访问与存储抽象
- [models](D:/nanobot%20notebook/mcp_stock_server/models)：请求、响应、数据库模型
- [tests/test_mcp_stock_server.py](D:/nanobot%20notebook/mcp_stock_server/tests/test_mcp_stock_server.py)：覆盖大部分现有工具与服务行为

当前实现已经具备以下优点：

- 服务层与仓储层已经有基础分离
- 大部分业务能力有测试覆盖
- `main.py` 与 `server.py` 的装配入口清晰

当前实现的主要问题：

- `server.py` 直接逐个注册函数工具，缺少统一工具注册抽象
- `tools` 同时承担输入解析、业务编排、序列化、部分流程控制，职责偏重
- 缺少统一 `ToolDefinition` 元数据模型
- 缺少 scope、approval、policy、audit、manifest 等治理能力
- 错误模型与审计模型未统一
- 当前 MCP 层更多是函数集合，不是有治理能力的服务层

## 目标架构

重构后的股票域 MCP Server 采用如下分层：

1. 协议层
2. 工具定义与注册层
3. 业务服务层
4. 数据与外部适配层
5. 治理层
6. 审计层
7. Manifest 层

推荐目录结构如下：

```text
mcp_stock_server/
  main.py
  server.py
  protocol/
    __init__.py
    dispatcher.py
    errors.py
    response.py
  tooling/
    __init__.py
    base.py
    definitions.py
    registry.py
    stock_tools.py
  auth/
    __init__.py
    context.py
    scopes.py
    approval.py
  governance/
    __init__.py
    policy.py
    redaction.py
  audit/
    __init__.py
    models.py
    writer.py
  manifest/
    __init__.py
    capabilities.py
  services/
    ...
  repositories/
    ...
  models/
    ...
  tests/
    ...
```

说明：

- `services`、`repositories`、`models` 尽量保留现有结构，避免本轮重构波及数据层
- `tools` 中现有函数式逻辑逐步迁移到 `tooling` 下的新工具定义体系
- `server.py` 调整为薄装配层，不再直接承载业务与治理逻辑

## 分层职责

### 协议层

协议层负责所有 MCP-facing 行为，但不理解股票业务细节。职责包括：

- MCP Server 初始化与工具暴露
- 统一的 `tools/list` 能力输出
- 统一工具 dispatch
- 输入错误归一化
- 成功 / 失败响应包装

协议层需要把调用流程标准化为：

1. 解析工具调用
2. 查询 registry
3. 验证输入 schema
4. 构造 auth context
5. 执行 policy 校验
6. 调用 tool execute
7. 执行 redaction
8. 记录 audit
9. 返回结果

### 工具定义与注册层

该层负责显式定义“模型可调用什么”，每个工具都要有稳定的元数据契约。

建议核心类型：

```python
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    required_scopes: set[str]
    destructive: bool
    owner: str
    version: str


class BaseTool:
    definition: ToolDefinition

    def execute(self, args: dict[str, Any], context: "AuthContext") -> dict[str, Any]:
        raise NotImplementedError
```

注册中心负责：

- register
- get
- list definitions
- 为 manifest 提供完整工具列表

### 业务服务层

业务服务层继续承载股票域逻辑，包括：

- 股票日线查询
- 股票日线写入 / 更新
- 技术指标计算编排
- 技术快照构建
- B1 选股逻辑

这层应当表达业务语义，而不是 MCP 语义。像当前 [services/stock_daily_service.py](D:/nanobot%20notebook/mcp_stock_server/services/stock_daily_service.py) 这样的服务对象应继续保留，并作为工具执行入口依赖。

### 数据与外部适配层

这一层主要对应现有 `repositories` 和 `init` 中的数据拉取能力，负责：

- MySQL 访问
- 内存仓储实现
- 数据抓取函数封装
- 外部数据源错误翻译

本轮不要求大改数据结构，但要避免协议层直接依赖这些细节。

### 治理层

治理层是本轮重构的重点新增能力，负责：

- scope 校验
- destructive 工具审批校验
- payload 基础限制
- 结果脱敏前置策略
- 调用决策输出

本轮先实现本地可运行骨架，不要求接入真实企业身份系统。

### 审计层

审计层负责记录每次工具调用的结果，包括：

- `allowed`
- `denied`
- `failed`

审计必须覆盖允许、拒绝和异常三类结果，而不只是成功调用。

### Manifest 层

Manifest 层负责输出当前 server 的能力清单，为未来接入 registry 或 gateway 做准备。

至少应包含：

- server name
- version
- transport
- tools
- required scopes
- destructive flags
- owner

## 工具兼容策略

本轮明确采用“外部兼容、内部重构”的策略。

### 兼容要求

- 保持当前 MCP 工具名不变
- 保持当前主要参数结构不变
- 保持现有核心返回 payload 结构尽量不变
- 不要求调用方在本轮切换新接口

### 当前工具映射

现有工具包括：

- `list_stock_codes`
- `get_stock_daily_bars`
- `upsert_stock_daily_bars`
- `insert_stock_daily_bars_after_close`
- `compute_short_trend`
- `compute_multi_trend`
- `compute_kdj`
- `compute_amplitude`
- `get_technical_snapshot`
- `screen_b1_stocks`

这些工具都应迁移为显式 `ToolDefinition + BaseTool` 实现。

### destructive 标记建议

以下工具应标记为 destructive：

- `upsert_stock_daily_bars`
- `insert_stock_daily_bars_after_close`

以下工具应标记为只读：

- `list_stock_codes`
- `get_stock_daily_bars`
- `compute_short_trend`
- `compute_multi_trend`
- `compute_kdj`
- `compute_amplitude`
- `get_technical_snapshot`
- `screen_b1_stocks`

## 鉴权 / 审批 / 审计设计

## 鉴权设计

建议新增本地 `AuthContext`：

```python
from dataclasses import dataclass, field


@dataclass(slots=True)
class AuthContext:
    user_id: str
    tenant_id: str
    scopes: set[str] = field(default_factory=set)
    approval_grants: set[str] = field(default_factory=set)
    request_id: str = ""
```

本轮默认实现策略：

- 没有外部身份输入时，使用本地默认上下文
- 支持通过配置或构造函数注入默认 scopes
- 每次请求都做 scope 校验，不在启动时一次性通过

建议 scope 分组：

- `stock:master:read`
- `stock:daily:read`
- `stock:daily:write`
- `stock:indicator:read`
- `stock:snapshot:read`
- `stock:screener:read`

### 工具 scope 建议映射

- `list_stock_codes` -> `stock:master:read`
- `get_stock_daily_bars` -> `stock:daily:read`
- `upsert_stock_daily_bars` -> `stock:daily:write`
- `insert_stock_daily_bars_after_close` -> `stock:daily:write`
- `compute_short_trend` -> `stock:indicator:read`
- `compute_multi_trend` -> `stock:indicator:read`
- `compute_kdj` -> `stock:indicator:read`
- `compute_amplitude` -> `stock:indicator:read`
- `get_technical_snapshot` -> `stock:snapshot:read`
- `screen_b1_stocks` -> `stock:screener:read`

## 审批设计

对 destructive 工具增加 approval gate。

建议接口：

```python
class ApprovalChecker:
    def has_valid_approval(self, context: AuthContext, tool_name: str) -> bool:
        ...
```

本轮默认实现：

- 使用内存或显式 grant 集合判定
- grant 与工具名绑定
- destructive 工具无 approval 时直接拒绝

## 审计设计

建议审计模型：

```python
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class AuditEntry:
    timestamp: float
    request_id: str
    tenant_id: str
    user_id: str
    tool_name: str
    args_redacted: dict[str, Any]
    response_redacted: dict[str, Any] | None
    outcome: str
    error_code: str | None
    latency_ms: int
```

建议审计落地策略：

- 默认写入本地 JSONL 文件
- 每次调用都写一条记录
- `allowed`、`denied`、`failed` 全覆盖

记录示例：

- 缺少 scope -> `denied`
- destructive 无 approval -> `denied`
- 仓储或抓取异常 -> `failed`
- 工具成功执行 -> `allowed`

## 脱敏设计

第一版实现轻量但强制的 redaction。

至少覆盖：

- 邮箱
- 手机号
- token / secret-like 字符串
- 审计中不应无限制记录的大文本内容

对当前股票域来说，重点不是 issue 描述，而是：

- 未来可能扩展的凭据字段
- payload 中潜在自由文本
- 异常对象中可能带出的敏感配置

## Manifest 设计

Manifest 从第一版开始就要可用。

建议输出结构：

```json
{
  "server": "mcp-stock-server",
  "version": "1.0.0",
  "transport": "stdio",
  "tools": [
    {
      "name": "get_stock_daily_bars",
      "required_scopes": ["stock:daily:read"],
      "destructive": false,
      "owner": "stock-platform"
    }
  ]
}
```

Manifest 应由 registry 生成，而不是手写静态清单。这样工具元数据与实际注册状态保持一致。

## 测试与迁移策略

本轮重构以“行为不破坏”为首要原则，测试策略分为五类。

### 1. 现有工具行为回归

保留并更新当前 [tests/test_mcp_stock_server.py](D:/nanobot%20notebook/mcp_stock_server/tests/test_mcp_stock_server.py) 中对以下行为的覆盖：

- 工具注册
- 参数透传
- 返回结构
- 技术指标计算
- B1 选股
- 技术快照

### 2. registry 与 metadata 测试

新增测试覆盖：

- 每个工具都成功注册
- 每个工具都有完整 metadata
- `list_tools()` 输出稳定

### 3. policy 测试

新增测试覆盖：

- 缺 scope 被拒绝
- destructive 工具无 approval 被拒绝
- destructive 工具有 approval 被放行

### 4. audit 测试

新增测试覆盖：

- `allowed` 事件写入
- `denied` 事件写入
- `failed` 事件写入
- 输出经过 redaction

### 5. manifest 测试

新增测试覆盖：

- manifest 输出基础字段正确
- tools 列表与 registry 一致
- destructive 与 scope 信息正确

## 实施边界

本轮重构包含：

- 新增协议、registry、治理、审计、manifest 基础骨架
- 将现有工具迁移到显式工具定义体系
- 保持现有工具外部兼容
- 新增本地可运行 auth / approval / audit 默认实现

本轮重构不包含：

- 修改数据库 schema
- 更换 MySQL / repository 模型
- 引入外部 IAM / SSO / 审批系统
- 重新设计所有返回 payload
- 把股票域改造成协作域示例模型

## 假设与非目标

### 假设

- 当前仓库仍以股票分析与日线数据能力为核心域
- 调用方短期内依赖现有工具名和参数
- 本地文件审计足以支撑第一阶段治理落地
- 现有服务层可以继续作为主要业务承载层

### 非目标

- 这份设计文档不直接实现代码重构
- 这份文档不定义未来所有企业级扩展细节
- 这份文档不要求一步完成完整生产化治理

## 后续计划建议

基于本设计文档，下一步应产出 implementation plan，拆解为可执行任务，建议顺序如下：

1. 定义 `ToolDefinition`、`BaseTool`、`ToolRegistry`
2. 建立统一 dispatch 与错误模型
3. 迁移只读工具
4. 接入 policy / audit / redaction
5. 迁移 destructive 工具并加入 approval 校验
6. 生成 capability manifest
7. 补齐测试并完成回归验证
