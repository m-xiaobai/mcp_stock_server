# MCP Tasks V1 Design

## Summary

将当前 `mcp_stock_server` 的部分长耗时工具扩展为支持 **MCP Tasks** 的异步执行模式。

这次 V1 不从零实现 Tasks 协议，而是直接复用本地 `mcp` SDK 已提供的 experimental task support，包括：

- `CreateTaskResult`
- `tasks/get`
- `tasks/result`
- `tasks/list`
- `tasks/cancel`
- `experimental.enable_tasks()`
- `request_context.run_task(...)`

在服务端握手里，还需要通过 `server/discover` 的 `capabilities.extensions`
声明 `io.modelcontextprotocol/tasks`，让客户端知道这个实例支持 Tasks 扩展。

同时，项目自己的 `get_capability_manifest` 也会输出 `tasks.enabled`
和 `tasks.task_aware_tools`，用于应用层能力摘要。两者不重复：

- `server/discover` 是 MCP 协议层声明
- `get_capability_manifest` 是项目内的机器可读摘要

设计目标是先跑通官方 Tasks 扩展链路，验证 server 侧 task-aware 工具接入方式，而不是一次性完成生产级任务系统。

V1 明确限制为：

- 试点两个工具：`insert_stock_daily_bars_after_close` 与 `get_technical_snapshot`
- 使用 SDK 默认 `InMemoryTaskStore`
- 不做任务持久化
- 暂不继续扩大 task-aware 工具范围
- 不做任务通知增强的专项设计

## 背景与目标

当前 `mcp_stock_server` 已具备以下能力：

- `stdio` 与 `streamable-http` 双入口
- 基于 `FastMCP` 的工具注册
- 统一的 `ToolDispatcher` / `PolicyEngine` / `AuthContext`
- destructive 工具的 `elicitation` 确认流程

但当前所有工具仍然采用 **同步执行、同步返回结果** 的模式。对于可能耗时较长、涉及外部抓取或批量落库的工具，这种模式存在以下局限：

- 客户端需要持续等待，容易受到 tool timeout 影响
- 无法用协议原生方式查询长任务进度
- 无法通过 `tasks/get` / `tasks/result` / `tasks/cancel` 获取一致体验

因此，这次设计的目标是：

- 让 `mcp_stock_server` 支持官方 Tasks 扩展
- 保持现有工具同步能力不回归
- 在最小改动下让少量长工具具备 task-aware 能力

## 当前现状

当前仓库中的关键执行链路如下：

- [main.py](D:/nanobot%20notebook/mcp_stock_server/main.py)
  - 负责装配 service 并启动 `FastMCP` server
- [server.py](D:/nanobot%20notebook/mcp_stock_server/server.py)
  - 通过 `FastMCP` 注册工具
  - 工具入口直接调用 `dispatch_tool(...)`
- [protocol/dispatcher.py](D:/nanobot%20notebook/mcp_stock_server/protocol/dispatcher.py)
  - 做参数校验、授权、审计、执行与错误转换
- [auth/elicitation.py](D:/nanobot%20notebook/mcp_stock_server/auth/elicitation.py)
  - destructive 工具确认流程已经基于 `elicitation` 能力实现

当前模型的关键特点：

- 工具调用是请求内同步完成
- `ToolDispatcher` 不负责任务生命周期管理
- `AuthContext` 是请求态上下文，不是任务态上下文
- 长任务没有 protocol-level handle

另一方面，本地 `.venv` 中的 `mcp` SDK 已经提供 Tasks 相关能力：

- `mcp.types.CreateTaskResult`
- `mcp.server.lowlevel.experimental.enable_tasks(...)`
- `mcp.server.experimental.request_context.run_task(...)`
- `mcp.server.experimental.task_context.ServerTaskContext`

因此，V1 的重点不是设计 Tasks 协议本身，而是把这些 SDK 能力接到当前 server 架构中。

## 设计决策

### 1. 使用 SDK experimental task support

V1 直接启用 SDK 自带的 experimental tasks，而不是自定义底层 JSON-RPC handler。

原因：

- 本地 SDK 已内置 `tasks/get` / `tasks/result` / `tasks/list` / `tasks/cancel`
- SDK 已提供 `CreateTaskResult` 与任务执行上下文
- 可以显著降低自实现协议面的复杂度
- 能让实现更贴近官方扩展语义

### 2. V1 task 化两个长工具

V1 当前选择两个长工具进行试点：

- `insert_stock_daily_bars_after_close`
- `get_technical_snapshot`

不在 V1 中 task 化以下工具：

- `get_stock_daily_bars`
- `list_stock_codes`
- `screen_b1_stocks`

原因：

- `insert_stock_daily_bars_after_close` 已具备明显长任务特征
- `get_technical_snapshot` 也可能因多标的计算与日线查询成为长请求
- 涉及抓取与写入，比普通查询更适合 Tasks
- 可以验证异步执行、状态查询和结果提取的完整链路
- 将改动面控制在少量工具范围内

### 3. 使用 `InMemoryTaskStore`

V1 使用 SDK 默认 `InMemoryTaskStore`。

原因：

- 先验证协议链路与 server 接入方式
- 避免在第一版就引入额外的任务表设计和持久化迁移
- 适合单进程开发与本地调试阶段

限制：

- 服务重启后任务会丢失
- 不支持跨实例共享任务状态
- 不适合作为最终生产方案

### 4. 同步工具与 task-aware 工具并存

V1 不将所有工具统一为 task 模式，而是采用双模式：

- 普通调用：保持当前同步返回
- task-augmented 调用：返回 `CreateTaskResult`

这样可以保证：

- 不支持 Tasks 的客户端不受影响
- 支持 Tasks 的客户端可对长任务使用异步能力
- 不强迫所有工具承担任务生命周期语义

## 运行时设计

### 1. 启用 task support

在 server 初始化阶段启用 SDK task support：

- 调用 `experimental.enable_tasks()`
- 使用默认 `InMemoryTaskStore`
- 使用默认 queue / handler

启用后，server 将自动具备：

- `tasks/get`
- `tasks/result`
- `tasks/list`
- `tasks/cancel`

同时 `ServerCapabilities.tasks` 中将自动声明 tasks 支持。

### 2. task-aware 调用分流

V1 中，以下工具需要支持两条执行路径：

- `insert_stock_daily_bars_after_close`
- `get_technical_snapshot`

#### A. 普通同步调用

客户端未使用 task-augmented request 时：

- 工具沿用当前同步执行逻辑
- 直接返回已有 payload

#### B. task-augmented 调用

当 SDK 在当前请求上下文中识别到 task request，并且该工具被标记为 task-aware 时：

- 工具不立即执行到底并返回 payload
- 使用 `ctx.request_context.experimental.run_task(...)`
- 立即返回 `CreateTaskResult`

如果是手写初始化报文，原始协议层面的声明可以写成：

```json
{
  "capabilities": {
    "extensions": {
      "io.modelcontextprotocol/tasks": {}
    }
  }
}
```

在当前实现里，服务端实际依赖的是 SDK 暴露的 task request 上下文信号，例如：

- `experimental.is_task`
- `experimental.validate_task_mode(...)`
- `experimental.task_metadata`

也就是说，task 路径的实际进入条件是“当前请求已被 SDK 识别为 task request 且带有 task metadata”，而不是在业务代码里单独解析客户端 capability 字段。

后台 `work` 函数负责：

- 调用现有 service 逻辑
- 最终返回与同步工具一致的结果结构

### 3. 任务状态更新

当前实现尚未在 `work` 函数中使用 `ServerTaskContext` 做细粒度状态更新。

当前已落地的行为是：

- 通过 SDK task store 托管任务生命周期
- 在服务端日志中记录 task start / finish
- 最终通过 `tasks/result` 返回结构化结果

尚未落地的能力包括：

- `正在拉取股票列表`
- `正在抓取当日日线`
- `正在写入数据库`
- `任务完成`

这些更细的进度文案和基于 `ServerTaskContext` 的状态推送，仍属于后续可增强项，而不是当前 V1 已实现内容。

### 4. 结果结构保持兼容

`tasks/result` 返回的结果结构应与当前同步工具返回值一致。

也就是说：

- 同步调用得到的 payload
- 任务完成后通过 `tasks/result` 拿到的 payload

应尽量保持同一业务结构，避免调用方为相同工具维护两套结果解析逻辑。

## 与现有鉴权、审批、审计的结合

### 鉴权

V1 不引入新的任务级权限模型。

任务提交前仍然沿用当前工具调用前的鉴权逻辑：

- 参数校验
- `PolicyEngine`
- 当前 HTTP 入口认证与内部授权上下文

即：

- 是否允许提交任务
- 仍由当前工具鉴权模型决定

### destructive approval

V1 不把 destructive approval 改造成 task 内的 `input_required` 流程。

原因：

- 当前 destructive 工具确认逻辑已经可用
- task 内输入交互会显著扩大实现范围
- 先验证基础 Tasks 能力更重要

后续阶段可考虑：

- 将 `elicitation` 与 Tasks 的 `input_required` 模型融合
- 使用 `ServerTaskContext.elicit(...)` 实现任务中断式等待用户输入

### 审计

V1 先沿用现有请求级审计链路。

这意味着：

- 任务提交请求本身仍会进入现有审计
- 任务生命周期级审计不是 V1 重点

后续如需增强，可补：

- 任务创建审计
- 状态更新审计
- 任务完成/失败审计

## 接口与行为约束

V1 的公共行为变化当前发生在：

- `insert_stock_daily_bars_after_close`
- `get_technical_snapshot`

同时，`get_capability_manifest` 会补充本地摘要字段：

- `tasks.enabled`
- `tasks.task_aware_tools`

其中 `tasks.enabled` 只是应用层状态摘要，不替代 `server/discover` 里的
`capabilities.extensions["io.modelcontextprotocol/tasks"]` 声明。

客户端约束：

- 能被 SDK 识别为 task request 的调用，会在 task-aware 工具上走 task-augmented 路径
- 其他调用继续走同步工具模式

V1 明确约束：

- task store 为内存版
- 服务重启后 task 不保留
- 不支持跨进程或跨实例任务恢复

## 测试与验收

V1 需要至少覆盖以下场景：

### 1. capability 暴露

- 启用 tasks 后，`ServerCapabilities.tasks` 能正确暴露 tasks 支持

### 2. 少量工具 task 化

- `insert_stock_daily_bars_after_close` 在普通调用下仍同步返回结果
- 同一工具在 task-augmented 调用下返回 `CreateTaskResult`
- `get_technical_snapshot` 作为 task-aware 工具出现在 capability / tool metadata 中

### 3. 任务生命周期

- 启用 tasks 后，SDK 提供 `tasks/get` / `tasks/result` / `tasks/list` / `tasks/cancel`
- 当前仓库测试已覆盖 capability 暴露、task result 返回和 task-aware tool metadata
- 当前仓库内尚未补齐对 `tasks/get` / `tasks/result` / `tasks/list` / `tasks/cancel` 的端到端显式测试

### 4. 回归验证

- 普通同步工具调用不回归
- 当前 HTTP / OAuth / destructive approval 不被破坏

## 非目标

V1 明确不包含以下内容：

- MySQL 持久化任务表
- 自定义 `TaskStore`
- 更多工具的 task 化
- 专项实现 `notifications/tasks/status` 增强链路
- 任务跨重启恢复
- 任务级审计体系
- 基于 task 的 `input_required` destructive flow

## 后续演进方向

如果 V1 跑通，下一阶段可以继续补齐：

1. 使用 MySQL 或其他持久化存储实现自定义 `TaskStore`
2. 将更多长工具迁移为 task-aware
3. 将 destructive approval 融合进 task `input_required`
4. 补充任务级审计与任务清理策略
5. 为多实例运行场景设计共享任务存储和任务路由

## 总结

这次 MCP Tasks V1 设计的核心，不是重写协议层，而是：

- 复用 SDK 已有的 experimental tasks 能力
- 在当前 `FastMCP + ToolDispatcher` 架构上增加 task-aware 工具路径
- 用少量工具、内存 store 的最小方案先跑通官方扩展链路

这样可以在不显著扩大改动面的前提下，让 `mcp_stock_server` 具备第一版 MCP Tasks 支持能力，并为后续任务持久化、更多工具迁移和输入中断式交互打下基础。
