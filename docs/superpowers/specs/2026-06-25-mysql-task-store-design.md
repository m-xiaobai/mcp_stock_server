# Persistent MySQL `TaskStore` Design

## Summary

为当前 MCP Tasks 能力增加一个 **单实例、可重启后保留历史任务** 的 MySQL `TaskStore`，目标是替换 SDK 默认 `InMemoryTaskStore` 的“状态和结果存储”部分，同时 **保留 `TaskMessageQueue` 为内存实现**。

推荐范围是：

- 只做 `TaskStore` 持久化，不做持久化 `TaskMessageQueue`
- 只支持 **单实例** 语义
- 服务重启后保留任务历史，但 **不会恢复正在运行的后台 work**
- 启动时将历史 `working` 任务统一标记为 `failed`，避免僵尸任务长期停留在 `working`

## Key Changes

### 1. 任务存储架构

新增一个 `MySQLTaskStore`，完整实现 SDK `TaskStore` 接口：

- `create_task`
- `get_task`
- `update_task`
- `store_result`
- `get_result`
- `list_tasks`
- `delete_task`
- `wait_for_update`
- `notify_update`

设计边界保持和 SDK 一致：

- `TaskStore` 只负责状态、结果、TTL、等待通知
- 不负责真正执行任务
- 不引入独立 worker，不做重启后任务恢复执行

`wait_for_update()` / `notify_update()` 采用 **混合设计**：

- 任务状态和结果落 MySQL
- 等待通知继续用当前进程内 `anyio.Event`
- 因此 `tasks/result` 的阻塞等待在 **单实例进程内** 正常工作
- 但这不是跨实例通知机制，所以明确不支持多实例共享等待语义

### 2. 持久化表设计

新增一张任务表，建议单表即可：

`mcp_tasks`

建议字段：

- `task_id` `varchar(64)` 主键
- `status` `varchar(32)` 非空
- `status_message` `text` 可空
- `created_at` `datetime(6)` 非空
- `last_updated_at` `datetime(6)` 非空
- `ttl_ms` `int` 可空
- `poll_interval_ms` `int` 非空
- `expires_at` `datetime(6)` 可空
- `result_json` `json` 可空

建议索引：

- 主键 `task_id`
- 索引 `expires_at`
- 索引 `(created_at, task_id)`

行为约束：

- `result_json` 存完整 `Result` 结构，直接走 `model_dump()` / `model_validate()`
- 分页顺序固定为 `created_at ASC, task_id ASC`
- `cursor` 继续用 `task_id` 语义；如果游标任务不存在，返回 `ValueError`，与现有内存实现保持一致

### 3. 生命周期与兼容语义

`MySQLTaskStore` 尽量复刻 `InMemoryTaskStore` 的现有语义，而不是顺手“优化协议”：

- 新建任务时状态为 `working`
- `update_task` 禁止从 terminal 状态再迁移
- 仅当 `status` 发生变化时才触发 `notify_update`
- `store_result` 只保存结果，不主动触发更新通知
- TTL 语义保持一致：
  - 创建任务时计算 `expires_at`
  - 进入 terminal 状态时，如果有 TTL，则重置 `expires_at`
- 过期清理采用 **lazy cleanup**：
  - 在 `create_task` / `get_task` / `list_tasks` 前顺手执行一次 `DELETE ... WHERE expires_at <= UTC_TIMESTAMP(6)`

重启策略明确为：

- 服务启动时扫描 `status=working` 的任务
- 统一改为 `failed`
- `status_message` 写明类似 `server restarted before task completion`
- 不尝试重放历史任务，也不保留为 `working`

### 4. 接线方式与配置

现有 server 接线点已经支持自定义 store：

- `server.experimental.enable_tasks(store=..., queue=...)`

因此实现上建议只做一层应用内封装，不改 SDK：

- 在 `server.py` 的 `create_mcp_server(...)` 增加可选参数：
  - `task_store: TaskStore | None = None`
  - `task_queue: TaskMessageQueue | None = None`
- 如果未传，保持现在的默认 `enable_tasks()` 行为
- 如果传入 `task_store`，则调用 `enable_tasks(store=task_store, queue=task_queue or InMemoryTaskMessageQueue())`

配置建议最小化：

- 在 `MCPRuntimeConfig` 增加 `mcp.tasks.store_backend`
- 允许值：`memory` / `mysql`
- 默认值：`memory`
- 当取值为 `mysql` 时：
  - 复用现有顶层 `mysql` 连接配置
  - 继续沿用 `pymysql`
  - 不单独引入 async MySQL 驱动

不新增 queue 配置；本轮固定仍用内存 queue。

## Public Interfaces / Types

需要明确的对外接口变化：

- `create_mcp_server(...)` 增加可选 `task_store` / `task_queue` 注入能力
- `MCPRuntimeConfig` 增加 `mcp.tasks.store_backend`
- 新增应用内 `MySQLTaskStore` 类型，作为 SDK `TaskStore` 的实现
- 启动流程增加一次 orphaned `working` task reconciliation

不改变现有 MCP tool 形态，不改变 `tasks/get` / `tasks/result` / `tasks/list` / `tasks/cancel` 的协议面。

## Test Plan

### Store Contract Tests

对 `MySQLTaskStore` 做一组与 `InMemoryTaskStore` 对齐的契约测试：

- `create_task` 后可 `get_task`
- 重复 `task_id` 创建失败
- `update_task` 可更新状态和 `statusMessage`
- terminal 状态不能再迁移
- `store_result` 后 `get_result` 可读回
- `delete_task` 返回值正确
- `list_tasks` 分页和非法 cursor 行为正确
- TTL 过期后 `get_task` / `list_tasks` 不再返回任务

### Wait / Notify Tests

单实例等待语义需要单测验证：

- `wait_for_update` 在状态未变化时阻塞
- `update_task(status=...)` 会唤醒等待者
- 只改 `statusMessage` 不应触发“状态变化通知”

### Restart / Persistence Tests

围绕“持久化而不恢复执行”验证：

- 一个 store 实例创建任务，另一个新实例可读到同一任务
- 启动 reconciliation 后，历史 `working` 任务被标记为 `failed`
- 已经 `completed` / `failed` / `cancelled` 的任务不会被改写

### Server Integration Tests

围绕 server 接线做回归：

- `memory` backend 保持当前行为不变
- `mysql` backend 时 `enable_tasks(store=custom_store, queue=in_memory_queue)` 被正确接线
- task-aware 工具仍返回 `CreateTaskResult`
- `tasks/get` / `tasks/list` / `tasks/result` 能通过持久化 store 读取状态和结果

## Assumptions

- 目标是 **单实例持久化**，不是多实例共享执行
- 本轮只持久化 `TaskStore`，`TaskMessageQueue` 继续内存实现
- 数据库访问继续沿用现有 `pymysql` 风格，不新引入异步驱动
- 不解决 task work 的协作式取消；`tasks/cancel` 仍以状态更新为主，不承诺立刻中断后台逻辑
- 不做重启后自动重放任务；历史 `working` 任务在启动时统一转为 `failed`
