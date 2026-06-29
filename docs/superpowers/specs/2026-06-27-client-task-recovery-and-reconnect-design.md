# Client Task Recovery And Reconnect Design

## Summary

目标是在 **只修改 `nanobot-stock` 客户端** 的前提下，让 MCP 长任务在 `mcp_stock_server` 重启或客户端重启后仍可自动续查，并在任务完成后把结果补回原会话。

默认选择：

- 不改服务端协议和数据结构
- 客户端自动重连，不要求用户手动重启
- 客户端自动恢复未完成任务
- 恢复成功后自动向原会话补一条结果消息
- 本次方案不包含测试实现或测试执行安排

## Key Changes

### 1. 客户端持久化 `task_id`

在 `nanobot/agent/tools/mcp.py` 的 `MCPToolWrapper` 中，为 task 调用增加会话级持久化能力。

当 `_call_tool_via_task_backend()` 拿到 `task_id` 后，先把一条 pending task 记录写入当前 `Session.metadata`，再进入轮询。建议在 metadata 中新增：

- `pending_mcp_tasks: list[PendingMcpTask]`

每条记录只保留恢复所需的最小字段：

- `task_id`
- `server_name`
- `tool_name`
- `backend_kind`
- `created_at`
- `status`
- `session_key`
- `result_delivered`

不要重复存整份工具参数；避免 metadata 持续膨胀，也避免把与恢复无关的大对象写进会话文件。

任务进入终态后，更新对应记录：

- 成功：`completed`
- 失败：`failed`
- 取消：`cancelled`
- 无法恢复或服务端已无该任务：`orphaned`

同时补充：

- `completed_at`
- `status_message`
- `result_preview`

### 2. 为 `MCPToolWrapper` 注入会话与运行时能力

当前 `MCPToolWrapper` 只通过 `set_context()` 拿到 `RequestContext`。为了支持恢复，需要额外注入：

- `SessionManager`
- MCP 运行时 state

保留现有 `set_context()` 逻辑，继续通过它获取 `session_key`。  
在 wrapper 创建路径里额外传入 `SessionManager`，使其能够在这些时机直接保存会话：

- 创建 task 后
- task 状态变化时
- 恢复成功后补写消息时

同时传入 MCP 运行时 state，用于在连接失效后执行单 server 清理与重连。

如果当前请求没有 `session_key`，则不启用 task 恢复持久化，保持现有行为不变。

### 3. 新增客户端侧“自动续查器”

客户端需要增加一个专门的恢复入口，用于读取会话 metadata 中：

- `status in {queued, running}`
- `result_delivered = false`

的 pending task。

恢复逻辑只做：

- `tasks/get`
- `tasks/result`

不重新发起原工具调用，不重复创建 task，也不依赖客户端重新拼装原始执行上下文。

恢复后的行为固定为：

- 如果任务仍在运行：
  - 只更新 metadata 中的状态
  - 刷新最近检查时间
- 如果任务已完成：
  - 拉取最终结果
  - 渲染为可读文本
  - 自动追加到原会话
  - 标记 `result_delivered = true`
- 如果服务端返回该任务不存在或无法恢复：
  - 标记为 `orphaned`
  - 停止重复恢复

### 4. 固定恢复触发时机

本方案不引入全局常驻后台轮询器，避免把问题扩大成任务调度系统。恢复只在明确时机触发：

- 某个 MCP server 连接成功建立或重新建立之后
- 某个带 `session_key` 的会话再次收到消息，且该会话存在 pending MCP task 时

这样可以在不增加太多复杂度的前提下，覆盖最重要的两类场景：

- server 重启后 client 仍在运行
- client 重启后用户重新进入原会话

### 5. 处理“连接对象还在，但底层已经失效”的情况

当前 `connect_missing_servers()` 只会处理“不在 `_mcp_stacks`”里的 server。  
这意味着只要旧连接对象还留在 `_mcp_stacks` 里，客户端就会把它当作“已连接”，即使底层 transport 实际已经因为 server 重启而失效。

新方案要补上的就是这个场景：

- `_mcp_stacks` 里有该 server
- 但旧 session / transport 已断开
- 下一次真正调用 tool 时才暴露为 EOF、transport closed、connection reset 等错误

处理方式固定为“自动重连后重试一次”：

1. 在 `MCPToolWrapper.execute()` 中识别 stale connection 异常
2. 调用 `_close_server(state, server_name)` 清理旧连接
3. 仅重连当前出问题的单个 server
4. 重连成功后，仅重试一次当前 MCP 调用

普通业务异常维持原样，不触发重连。  
不默认调用全量 `reload_servers()`，避免对其他正常 MCP server 造成副作用。

### 6. 固定消息补写策略为“单次补写、幂等”

恢复成功后，客户端自动向原会话追加一条合成消息，例如：

- `恢复的 MCP 任务结果`

这条消息的职责是把服务端保住但客户端当时没来得及展示的最终结果补回用户视角。

幂等规则固定为：

- 同一个 `task_id` 只允许补写一次
- 通过 `result_delivered` 控制是否已经投递
- 即使客户端多次重启，只要已经补写过，就不再重复插入消息

## Public Interfaces / Types

不改 `mcp_stock_server` 的外部接口，不新增服务端 API。

客户端内部需要新增或调整的内容：

- `Session.metadata` 新增：
  - `pending_mcp_tasks: list[PendingMcpTask]`
- `MCPToolWrapper` 的内部构造依赖增加：
  - `SessionManager`
  - MCP 运行时 state
- 聊天历史中新增一类客户端合成消息：
  - 恢复完成后自动补写的 MCP 任务结果消息

## Assumptions

- `mcp_stock_server` 已开启 task 持久化，并且 `tasks/get`、`tasks/result` 在 server 重启后仍能基于原 `task_id` 返回状态和结果
- 客户端会话文件已可靠落盘，`Session.metadata` 适合作为 pending task 的唯一持久化位置
- 当前阶段不做跨会话任务中心或全局任务面板，恢复范围仅限原会话
- 当前阶段不重新执行原工具调用；恢复仅依赖已存在的 `task_id` 与 MCP task 生命周期接口
- 当前阶段不包含测试计划、测试用例编写或测试执行
