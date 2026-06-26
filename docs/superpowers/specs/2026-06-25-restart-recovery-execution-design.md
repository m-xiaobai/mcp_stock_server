# Restart Recovery Execution Design

## Summary

目标是让 **task-augmented 工具在服务重启后自动恢复执行**，并且 **保留原 `taskId`**。  
恢复能力应当 **对客户端透明**：客户端不需要感知服务端重启，不需要调用恢复接口，也不需要为了恢复机制增加新的状态判断逻辑。

当前 `TaskStore` 持久化只保住了任务记录，**没有保住可恢复执行所需的任务定义**；因此这次设计要补一层 **replayable task metadata + recovery coordinator**，把“任务状态持久化”升级为“任务可自动重放”。

默认选择：

- 恢复模式：**服务启动后自动重放**
- `taskId`：**保留原值**
- 范围：**框架支持所有未来 task-aware 工具**
- 实际恢复白名单：第一版先显式标记工具是否 `replayable`
- 当前两个工具默认可恢复：
  - `insert_stock_daily_bars_after_close`
  - `get_technical_snapshot`
- `statusMessage`：**不作为恢复协议的一部分，不作为客户端依赖字段**

## Key Changes

### 1. 服务端新增“可恢复任务定义”层

在现有 `mcp_tasks` 之外，新增一层恢复执行元数据，建议直接扩展同一张表或新增紧邻的恢复表，但实现上必须持久化这些字段：

- `task_id`
- `tool_name`
- `tool_args_json`
- `execution_state`
  - 建议：`queued` / `running` / `completed` / `failed` / `cancelled`
- `replayable`
- `attempt_count`
- `last_error`
- `recovery_started_at`
- `lease_owner` / `lease_expires_at`
  - 即使当前是单实例，也建议把字段留好，避免以后恢复协调重构
- 可选：`idempotency_key`

`TaskStore` 继续负责协议层状态读写；新增一个更高层组件，例如 `TaskRecoveryCoordinator`，职责是：

- 在任务创建时持久化 `tool_name + args`
- 在启动时扫描可恢复任务
- 把任务重新投递到执行器
- 更新 `execution_state`
- 在需要时同步 task `status`

恢复判断只基于持久化的恢复元数据，不依赖 `statusMessage`。

### 2. `run_task()` 路径改成“创建任务 + 注册恢复元数据 + 执行”

当前 `server.py` 里是直接 `experimental.run_task(work)`。要支持恢复，工具进入 task 模式时需要多做一步：

- 创建 task 前或创建后，记录：
  - 任务对应的工具名
  - 原始入参
  - 是否 `replayable`
- `execution_state` 初始置为 `queued`
- 真正开始执行前更新为 `running`
- 正常完成：
  - `TaskStore.store_result(...)`
  - `TaskStore.update_task(..., completed)`
  - `execution_state=completed`
- 异常失败：
  - `TaskStore.update_task(..., failed)`
  - `execution_state=failed`
  - `last_error` 持久化

为了保持原 `taskId`，恢复执行不能通过“新建一个恢复任务”来实现，而必须是：

- 恢复时读取既有任务记录
- 基于已有 `task_id` 重建执行上下文
- 直接推动原任务进入 `running -> completed/failed`

### 3. 启动恢复流程

服务启动时，`build_task_store()` 之后新增 recovery 启动步骤：

- 先做 schema 初始化
- 再扫描 orphaned task
- 不再简单根据旧逻辑一律改成 `failed`
- 改成：
  - 如果 `execution_state in ('queued', 'running')` 且 `replayable=true`
    - 重新投递执行
  - 如果 `replayable=false`
    - 标记为 `failed`
    - `last_error=task cannot be replayed after server restart`

恢复选择规则：

- 默认只恢复 `task.status='working'` 且 `execution_state in ('queued', 'running')`
- terminal 状态任务不参与恢复
- 如果恢复投递失败，立即写回 `failed`
- 是否附带 `statusMessage` 仅作为调试增强，不参与协议判断

### 4. 工具级 replayability 规则

第一版需要显式给 task-aware 工具打 replayability 标记，而不是默认所有工具都能恢复。  
推荐在现有 task-aware 配置旁边增加类似：

- `task_aware`
- `replayable`

第一版默认规则：

- `get_technical_snapshot`：`replayable=true`
  - 查询型，只要参数相同即可重放
- `insert_stock_daily_bars_after_close`：`replayable=true`
  - 依赖底层 upsert，天然比 append-only 写入更适合恢复重放

未来新增 task-aware 工具时，必须显式声明是否支持恢复；不要隐式继承。

### 5. 客户端行为

客户端应当可以对恢复机制**完全无感**。恢复不是客户端协议职责，而是服务端内部执行职责。

客户端只需要保留当前 task 模式下原本就有的最小能力：

- 保存 `taskId`
- 继续调用已有的 `tasks/get`
- 继续调用已有的 `tasks/result`

客户端不需要：

- 感知服务端是否重启
- 调用额外恢复接口
- 识别任务是否正在恢复
- 解析 `statusMessage`
- 为恢复机制增加新的状态分支
- 主动重新提交任务

如果服务端恢复成功，客户端最终照常拿到原 `taskId` 的结果。  
如果服务端恢复失败，客户端看到的也只是普通失败任务，不需要知道失败是否发生在恢复阶段。

可选增强但非必需项：

- 客户端支持幂等键，用于避免超时重试时重复创建等价任务

这属于“创建任务去重”能力，不属于“恢复执行协作”能力。

## Public Interfaces / Types

需要新增或调整的接口/类型：

- 任务恢复元数据持久化模型
  - 可以是新 repository，也可以是扩展 `MySQLTaskStore` 依赖的表访问层
- `TaskRecoveryCoordinator`
  - `register_task_definition(...)`
  - `schedule_recovery_on_startup(...)`
  - `replay_task(task_id)`
- task-aware 工具元数据增加 `replayable`
- `MCPRuntimeConfig` 增加恢复相关开关：
  - 例如 `mcp.tasks.recovery.enabled`
  - 默认建议 `true`
- 启动流程增加 recovery coordinator 初始化和启动恢复扫描

不建议新增客户端显式“恢复任务” MCP 接口作为第一版必需项。  
`statusMessage` 保留为可选调试字段，但不进入协议关键路径。

## Assumptions

- 恢复执行的实现方式是 **从头安全重放**，不是断点续跑
- 第一版保持 **单实例** 语义；虽然预留 lease 字段，但不承诺多实例协调恢复
- 自动恢复优先于客户端显式恢复接口
- 保留原 `taskId`，不创建恢复子任务
- 客户端对恢复机制完全无感，不需要增加任何额外行为或分支
- `statusMessage` 不作为恢复判断或客户端依赖字段
- 未来所有 task-aware 工具都可以接入恢复框架，但每个工具必须显式声明 `replayable`
- `TaskMessageQueue` 仍不做持久化；如果以后 task 内使用 `elicit()` / `create_message()`，需要单独设计“恢复中的交互消息”语义
