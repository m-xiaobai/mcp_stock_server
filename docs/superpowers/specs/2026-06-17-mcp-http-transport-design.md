# MCP Server HTTP Transport Design

> 为 `mcp_stock_server` 增加 `streamable-http` 传输支持的设计说明。目标是在保留现有 `stdio` 行为的前提下，为本机或受控内网客户端提供标准 HTTP MCP 入口。

## 目标

当前 `mcp_stock_server` 只支持 `stdio` 传输。为了让外部 MCP 客户端能够通过 HTTP 方式访问同一套股票工具能力，需要在不破坏现有工具名、业务行为和本地调试方式的前提下，引入 `streamable-http` 入口。

本阶段目标如下：

- 保留现有 `stdio` 启动路径
- 新增 `streamable-http` 启动路径
- 复用同一个 `FastMCP` app 和同一套工具注册逻辑
- 让 capability manifest 能反映当前实例的真实传输方式
- 仅面向 `localhost / 127.0.0.1` 场景，不设计公网认证

## 当前现状

当前仓库中：

- [main.py](D:/nanobot%20notebook/mcp_stock_server/main.py) 负责装配 MySQL service 并启动 server
- [server.py](D:/nanobot%20notebook/mcp_stock_server/server.py) 通过 `FastMCP` 注册工具
- manifest 中的 `transport` 固定写成 `"stdio"`
- 审计、治理、registry、dispatcher 已经收敛到统一 app 装配中

所以这次扩展的重点不是重写协议层，而是把现有 app 从“单传输入口”扩展成“可选择的多传输入口”。

## 设计原则

这次 HTTP 传输设计遵循以下原则：

- 单一 app，多入口
- 优先使用 `FastMCP` 原生能力
- 不引入自定义 HTTP-to-MCP 转发层
- 不破坏现有 `stdio` 用法
- 第一阶段不做公网认证和生产暴露

## 目标架构

设计后的结构保持 `create_mcp_server(...)` 作为唯一 app 装配入口。

```text
config.json / CLI
        |
        v
     main.py
        |
        +-----------------------------+
        |                             |
        v                             v
run_stdio_server()         run_streamable_http_server()
        |                             |
        +-------------+---------------+
                      |
                      v
            create_mcp_server(...)
                      |
      +---------------+----------------+
      |               |                |
      v               v                v
   registry       dispatcher       manifest
      |               |                |
      +---------------+----------------+
                      |
                      v
               FastMCP tool app
```

说明：

- `stdio` 和 `streamable-http` 都复用同一个 `FastMCP` app
- 治理、审计、工具定义、dispatcher 都不按传输分叉
- 传输差异只体现在运行入口和 manifest transport 字段

## 运行入口设计

### create_mcp_server(...)

`create_mcp_server(...)` 仍然负责：

- 创建 `FastMCP` app
- 创建 `ToolRegistry`
- 创建 `ToolDispatcher`
- 注册所有工具
- 生成 capability manifest

为了支持 HTTP，它增加以下运行时参数：

- `transport`
- `host`
- `port`
- `streamable_http_path`

这些参数不会改变工具本身的行为，只影响：

- app 初始化参数
- manifest 中的 transport
- 审计文件路径标识

### run_stdio_server(...)

保留现有行为：

- 构造 app
- 以 `transport="stdio"` 运行
- 作为默认启动方式

### run_streamable_http_server(...)

新增 HTTP 启动函数：

- 构造 app
- 以 `transport="streamable-http"` 运行
- 使用 `host / port / streamable_http_path` 绑定地址

默认访问地址形如：

```text
http://127.0.0.1:8000/mcp
```

## 配置设计

为了不破坏现有 MySQL 配置格式，在 `config.json` 中新增 `mcp` 段。

建议结构：

```json
{
  "mysql": {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "xiaobai",
    "password": "123456",
    "database": "stocks"
  },
  "mcp": {
    "transport": "streamable-http",
    "host": "127.0.0.1",
    "port": 8000,
    "streamable_http_path": "/mcp"
  }
}
```

### 默认值

如果没有 `mcp` 段，默认值如下：

- `transport = "stdio"`
- `host = "127.0.0.1"`
- `port = 8000`
- `streamable_http_path = "/mcp"`

### CLI 覆盖

第一阶段允许通过命令行参数覆盖 transport，例如：

```powershell
python main.py streamable-http
```

CLI 只覆盖 `transport`，`host / port / path` 仍来自配置文件。

## Manifest 设计

`get_capability_manifest` 工具继续保留，但不能再把 transport 固定写死成 `"stdio"`。

原则是：

- `stdio` 运行时返回 `"stdio"`
- `streamable-http` 运行时返回 `"streamable-http"`

manifest 示例：

```json
{
  "server": "mcp-stock-server",
  "version": "1.0.0",
  "transport": "streamable-http",
  "tools": [
    {
      "name": "get_stock_daily_bars",
      "required_scopes": ["stock:daily:read"],
      "destructive": false,
      "owner": "stock-platform",
      "version": "1.0.0"
    }
  ]
}
```

## 审计与治理边界

HTTP 模式不单独复制治理逻辑，仍复用当前统一调用链：

- registry
- dispatcher
- policy
- redaction
- audit

### AuthContext

第一阶段继续沿用当前本地开发态 `AuthContext` 默认构造方式：

- 适合 `localhost / 127.0.0.1`
- 不适合公网暴露

这意味着第一阶段的 HTTP 入口是：

- 本机内网入口
- 开发 / 调试入口
- 非公网生产入口

### 审计文件

为了避免不同传输入口混写同一份审计日志，审计文件路径应加入传输标识，例如：

- `docs/audit/mcp-audit-stdio.jsonl`
- `docs/audit/mcp-audit-streamable-http.jsonl`

## 客户端接入方式

在当前设计中，HTTP 入口采用 `streamable-http`，不是 SSE。

因此客户端配置应写成：

```json
{
  "tools": {
    "mcpServers": {
      "my-api": {
        "type": "streamableHttp",
        "url": "http://127.0.0.1:8000/mcp",
        "toolTimeout": 120
      }
    }
  }
}
```

如果客户端会根据 URL 自动判断 transport，则不显式写 `type` 也通常可以：

```json
{
  "tools": {
    "mcpServers": {
      "my-api": {
        "url": "http://127.0.0.1:8000/mcp",
        "toolTimeout": 120
      }
    }
  }
}
```

### Header 是否需要

当前第一阶段设计中：

- 不需要 `Authorization` header
- 不启用 bearer auth
- 不启用 OAuth

只有未来接入网关或服务端认证后，才需要在客户端配置 `headers`。

## 日志输出规则

由于 `stdio` 模式下 `stdout` 是 MCP 协议通道，因此所有非协议输出都必须避免写到 `stdout`。

规则如下：

- 不使用普通 `print(...)` 向 `stdout` 输出启动提示或调试信息
- 启动日志统一使用 `logging`
- `logging` 统一输出到 `stderr`

这条规则同时适用于：

- `main.py`
- `server.py`
- 未来所有可能在入口阶段输出状态信息的模块

## 测试策略

建议覆盖以下内容：

### 配置测试

- `mcp` 段缺失时，默认值正确
- `mcp` 段存在时，HTTP 配置能正确读取

### 入口测试

- `run_stdio_server(...)` 使用 `stdio`
- `run_streamable_http_server(...)` 使用 `streamable-http`

### Manifest 测试

- `stdio` 模式下返回 `transport: "stdio"`
- `streamable-http` 模式下返回 `transport: "streamable-http"`

### 回归测试

- `create_mcp_server(...)` 仍注册原有工具
- `get_stock_daily_bars`
- `upsert_stock_daily_bars`
- `insert_stock_daily_bars_after_close`
- `get_technical_snapshot`
- `screen_b1_stocks`

## 非目标

本阶段明确不包含：

- 公网认证
- bearer token / OAuth
- 反向代理接入
- SSE 兼容性设计
- 单进程同时对外监听 stdio 与 HTTP

## 后续演进方向

如果未来要把 HTTP 入口从“本机开发入口”升级为“生产入口”，下一阶段建议补齐：

1. 请求态认证上下文，而不是开发态默认 `AuthContext`
2. 网关或 Bearer Auth 接入
3. 更明确的 host / CORS / TLS 部署方案
4. 按实例维度区分 manifest / audit / owner 信息
5. 对 Async Tasks 的 HTTP 客户端适配

## 总结

这次 HTTP 传输设计的核心，不是引入第二套 MCP server，而是在现有统一 app 上增加第二个运行入口。

它保留了：

- 现有工具定义
- 现有治理和审计链路
- 现有 stdio 调试方式

同时新增了：

- `streamable-http` 运行能力
- 传输感知的 manifest
- 可配置的 MCP 运行参数
- 面向本机客户端的标准 HTTP 访问路径

这样 `mcp_stock_server` 就从一个纯 `stdio` MCP server，演进为一个可按场景选择 `stdio` 或 `HTTP` 入口的 MCP 服务。
