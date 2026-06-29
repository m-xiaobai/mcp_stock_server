# MCP Server Redis 限流与并发租约设计文档

## 文档信息
- 作者：Codex
- 版本：v1.0
- 更新日期：2026-06-27
- 状态：Draft

## 1. 背景与目标

### 1.1 背景
`mcp_stock_server` 当前已经具备较清晰的分层结构，包括工具注册、统一分发、鉴权、审计、任务持久化和恢复能力。但在多实例部署场景下，服务还缺少一套共享的入口保护机制，尤其是：

- 某个租户短时间高频调用工具时，缺少统一限流
- 高成本工具同时运行过多时，缺少全局并发保护
- 多实例同时提供服务时，单机内存级控制无法表达全局真实占用

如果不补齐这部分能力，系统在生产中容易出现两类问题：

- 流量型问题：短时间请求量暴增，持续冲击入口、Redis、数据库或外部依赖
- 占用型问题：慢请求或重工具同时运行过多，耗尽数据库连接、CPU、第三方配额或任务执行资源

### 1.2 目标
本设计只聚焦两块共享可靠性能力：

1. Redis 限流
2. Redis 并发租约

设计目标如下：

- 支持多实例部署下的共享限流和共享并发控制
- 尽量少改现有 `server.py -> ToolDispatcher -> services/repositories` 主链路
- 保持 `protocol/dispatcher.py` 继续专注于分发、鉴权、审计和错误转换
- 让 Redis 只承载短期共享控制状态，不侵入 MySQL 任务与业务持久化链路

### 1.3 非目标
本次设计明确不包含以下内容：

- 超时、重试、熔断、降级策略的完整实现
- 测试方案和测试代码
- Redis 高可用部署细节
- capability manifest 对外暴露限流/并发配置
- 排队等待、优先级抢占、续租 heartbeat 机制

## 2. 限流与并发控制的区别

限流和并发控制都属于入口保护能力，但解决的问题不同，不能混在一起设计。

### 2.1 限流解决什么问题
限流控制的是：`单位时间内允许多少请求进入系统`。

它主要用于防止：

- 某个租户短时间高频调用
- 流量洪峰冲击入口
- 某个工具被轮询刷爆
- 上游错误重试把流量进一步放大

它的特征是：

- 关注“频率”
- 单位通常是 `N 次 / 60 秒`
- 即使单个请求都很快，只要来得太密，也会触发限流

所以限流的本质是：**控制请求进入速度**。

### 2.2 并发控制解决什么问题
并发控制控制的是：`同一时刻最多允许多少个请求正在执行`。

它主要用于防止：

- 重工具同时运行太多
- 多实例把数据库连接池打满
- 外部依赖同时被占满
- 长任务把执行槽位全部占住

它的特征是：

- 关注“占用”
- 单位通常是 `同时最多 N 个`
- 即使一分钟请求总数不多，只要每个请求执行很慢，也会触发并发上限

所以并发控制的本质是：**控制当前正在消耗资源的请求数量**。

### 2.3 为什么两者都要有
只做限流不够，因为：

- 请求总量不高，但每个请求都很慢时，系统仍然会被拖死

只做并发控制也不够，因为：

- 某个租户可以持续高频打请求，不断冲击入口和共享状态存储

因此推荐顺序是：

1. 先做限流，拦住“来得太多”
2. 再做并发控制，拦住“同时跑太多”

在执行链路里，限流通常先于并发控制，因为它更便宜，也更适合作为第一道闸门。

## 3. 总体架构

### 3.1 接入位置
请求进入 [server.py](/D:/nanobot%20notebook/mcp_stock_server/server.py) 的 `dispatch_tool(...)` 后，执行顺序调整为：

1. 根据 `ToolDefinition` 读取限流和并发配置
2. 计算当前请求的限流 scope 和并发 scope
3. 执行 Redis 限流判定
4. 执行 Redis 并发租约申请
5. 申请成功后进入 `dispatcher.dispatch(...)`
6. 请求结束时在 `finally` 中释放租约

### 3.2 分层职责
- `server.py`
  - 负责“能不能执行”
  - 做限流和并发控制的统一接入
- `protocol/dispatcher.py`
  - 负责“如何执行”
  - 继续处理 schema 校验、鉴权、审计和错误响应
- Redis 仓储层
  - 负责原子计数与原子租约裁决
- MySQL
  - 继续承载任务和业务数据持久化，不参与本次共享控制

### 3.3 设计原则
- 尽量不把 Redis 逻辑塞进具体 tool handler
- 尽量不把共享控制逻辑塞进 `ToolDispatcher`
- 限流和并发控制应通过 `ToolDefinition` 元数据驱动
- Redis 中只保存短期、可过期、可重建的控制状态

## 4. Redis 限流设计

### 4.1 目标
限制单位时间内某个范围内允许进入执行链路的请求次数，支持多实例共享计数。

### 4.2 模型选择
首版使用固定窗口限流，不做令牌桶或滑动窗口。

原因：

- 实现最简单
- Redis 原子计数天然适配
- 足够覆盖当前 MCP server 首版生产需求

### 4.3 ToolDefinition 配置
在 [tooling/definitions.py](/D:/nanobot%20notebook/mcp_stock_server/tooling/definitions.py) 的 `ToolDefinition` 中新增：

- `rate_limit_scope: str = "none"`
- `rate_limit_capacity: int | None = None`
- `rate_limit_window_seconds: int | None = None`
- `rate_limit_fail_mode: str = "closed"`

支持的 scope：

- `none`
- `global`
- `tenant`
- `tenant_tool`

支持的 fail mode：

- `closed`
- `open`

### 4.4 scope_key 规则
根据请求上下文和工具名生成 Redis 限流作用域：

- `global` -> `rl:global:{tool_name}`
- `tenant` -> `rl:tenant:{tenant_id}`
- `tenant_tool` -> `rl:tenant_tool:{tenant_id}:{tool_name}`

如果后续要区分前台和后台，可增加前缀：

- `rl:interactive:...`
- `rl:background:...`

本次设计先不强制引入这个维度。

### 4.5 Redis 结构
每个固定时间窗口对应一个计数 key：

- `rl:{scope}:{...}:{window_start}`

其中 `window_start` 按 `window_seconds` 对当前时间向下取整。

示例：

- `rl:tenant_tool:tenant-a:get_stock_daily_bars:1719471600`

key 的值是整数计数器，TTL 设置为：

- `window_seconds + 少量缓冲`

### 4.6 判定流程
每次请求执行如下步骤：

1. 根据工具配置判断是否启用限流
2. 计算窗口 key
3. 对该 key 执行原子递增
4. 若结果 `<= capacity`，放行
5. 若结果 `> capacity`，拒绝并返回 `rate_limited`

### 4.7 原子性要求
推荐用 Lua 脚本实现，不使用裸 `INCR + EXPIRE`。

Lua 脚本逻辑：

1. `INCR key`
2. 若结果为 `1`，设置 `EXPIRE`
3. 返回当前计数

这样可以避免首次请求时 TTL 未正确设置的窗口状态不一致问题。

### 4.8 Redis 不可用时的处理
Redis 限流失败时需要区分工具类型：

- 轻量读工具：允许降级为放行
- 高成本或写工具：建议拒绝执行

因此把 Redis 不可用时的处理作为工具级配置：

- `rate_limit_fail_mode = "closed"`：Redis 不可用时拒绝
- `rate_limit_fail_mode = "open"`：Redis 不可用时放行

默认建议：

- 写工具、高成本工具：`closed`
- 轻量读工具：`open`

## 5. Redis 并发租约设计

### 5.1 目标
限制同一时刻某个范围内正在执行的请求数，支持多实例共享占用，并在实例崩溃时自动释放。

### 5.2 模型选择
采用“租约 lease”模型，而不是简单计数器。

原因：

- 请求执行中崩溃时，简单计数器容易泄漏
- 租约带 TTL，天然适合自动回收
- 可以追踪是谁占用了并发槽位

### 5.3 ToolDefinition 配置
在 [tooling/definitions.py](/D:/nanobot%20notebook/mcp_stock_server/tooling/definitions.py) 的 `ToolDefinition` 中新增：

- `concurrency_scope: str = "none"`
- `max_concurrency: int | None = None`
- `lease_ttl_seconds: int | None = None`
- `concurrency_fail_mode: str = "closed"`

支持的 scope：

- `none`
- `global`
- `tenant`
- `tenant_tool`

支持的 fail mode：

- `closed`
- `open`

默认建议统一使用 `closed`。

### 5.4 并发作用域 key
根据工具和租户生成：

- `global` -> `cc:global:{tool_name}`
- `tenant` -> `cc:tenant:{tenant_id}`
- `tenant_tool` -> `cc:tenant_tool:{tenant_id}:{tool_name}`

### 5.5 Redis 结构设计
并发租约不是“把所有实例 ID 放到集合里数一数”这么简单。真正需要统计的是：

- 某个 `scope_key` 下
- 当前有多少个**正在执行的请求或任务**
- 而不是有多少个实例参与执行

所以集合里存的不能只是 `instance_id`，而应该是 **`lease_id`**。

原因如下：

- 同一实例上可能同时跑多个请求，单个 `instance_id` 无法表示占了几个并发槽位
- 只存实例 ID 无法精确释放某个请求占用
- 无法区分同一实例上的多个 request 或 task

### 5.6 推荐 Redis 结构
推荐使用两类 key。

#### 5.6.1 scope 占用集合
- key：`cc:{scope_key}`
- 类型：`ZSET`
- member：`lease_id`
- score：`expires_at`

示例：

- `cc:tenant_tool:tenant-a:get_technical_snapshot`

members：

- `lease:ins-1:req-101`
- `lease:ins-2:req-889`

#### 5.6.2 lease 明细
- key：`cc:lease:{lease_id}`
- 类型：`HASH` 或 JSON 字符串
- 内容：
  - `instance_id`
  - `request_id`
  - `task_id`
  - `tool_name`
  - `scope_key`
  - `expires_at`

示例：

- `cc:lease:lease:ins-1:req-101`

value：

```json
{
  "instance_id": "ins-1",
  "request_id": "req-101",
  "task_id": null,
  "tool_name": "get_technical_snapshot",
  "scope_key": "tenant_tool:tenant-a:get_technical_snapshot",
  "expires_at": 1719471999
}
```

### 5.7 为什么不用实例 ID 做集合成员
如果集合里只放 `instance_id`，统计会失真。

例如：

- `instance-a` 同时执行 3 个请求
- `instance-b` 同时执行 1 个请求

如果集合成员只有：

- `instance-a`
- `instance-b`

那集合大小是 `2`，但真实并发占用是 `4`。这样并发控制会失效。

因此必须满足这个映射关系：

- 一个正在运行的 request 或 task
- 对应一条独立 lease

最终集合里数的是 **活跃 lease 数**，不是实例数。

### 5.8 申请流程
并发申请必须原子完成，推荐使用 Lua 脚本。

逻辑如下：

1. 先按 `now` 清理当前 `cc:{scope_key}` 中的过期 lease
   - `ZREMRANGEBYSCORE cc:{scope_key} -inf now`
2. 统计当前有效 lease 数
   - `ZCARD cc:{scope_key}`
3. 若数量 `< max_concurrency`
   - 生成新的 `lease_id`
   - `ZADD cc:{scope_key} expires_at lease_id`
   - 写入 `cc:lease:{lease_id}`
   - 设置租约 TTL
   - 返回成功
4. 否则返回失败，错误码 `concurrency_exhausted`

因此，并发判断本质上确实是“判断个数是否超过阈值”，但判断的是：

- 某个 scope 下当前有效 lease 的数量
- 不是实例数量
- 也不是所有机器的请求总数

### 5.9 释放流程
请求执行结束时，无论成功还是失败，都必须执行释放：

1. 删除 `cc:lease:{lease_id}`
2. 从 `cc:{scope_key}` 中移除该 `lease_id`

这部分必须放在 [server.py](/D:/nanobot%20notebook/mcp_stock_server/server.py) 的 `finally` 中，不能依赖业务代码自己释放。

### 5.10 TTL 设计
租约 TTL 用来处理实例崩溃、进程退出、异常中断。

建议：

- 默认 `lease_ttl_seconds` 固定配置
- TTL 必须大于工具正常执行时间
- 首版不做续租
- 如果后续发现长任务经常超过 TTL，再增加 heartbeat 或 renew 机制

因此首版默认策略是：

- 短任务：靠足够大的 TTL 覆盖
- 异常退出：靠 TTL 自动回收
- 长任务续租：暂不实现

### 5.11 Redis 不可用时的处理
并发控制比限流更敏感，因为它直接保护执行资源。

建议增加字段：

- `concurrency_fail_mode: str = "closed"`

支持：

- `closed`：Redis 不可用时拒绝执行
- `open`：Redis 不可用时跳过共享并发控制

默认建议统一使用：

- `closed`

原因：

- 并发控制失效比限流失效更危险
- 多实例下如果跳过共享并发控制，最容易导致下游被打爆

## 6. 代码结构调整

### 6.1 扩展 ToolDefinition
修改 [tooling/definitions.py](/D:/nanobot%20notebook/mcp_stock_server/tooling/definitions.py)，增加：

- 限流字段
- 并发字段
- 失败策略字段

不改 `ToolRegistry` 结构，也不改变 tool 注册方式。

### 6.2 在 tooling/stock_tools.py 为工具配置策略
在 [tooling/stock_tools.py](/D:/nanobot%20notebook/mcp_stock_server/tooling/stock_tools.py) 中为每个已注册工具补充：

- `rate_limit_scope`
- `rate_limit_capacity`
- `rate_limit_window_seconds`
- `concurrency_scope`
- `max_concurrency`
- `lease_ttl_seconds`

首版建议只给当前真正暴露的工具配置，不处理已注释的旧工具。

### 6.3 新增 Redis 仓储
新增两个文件：

- `repositories/redis_rate_limit_store.py`
- `repositories/redis_concurrency_store.py`

职责：

`RedisRateLimitStore`
- 计算窗口 key
- 执行限流 Lua 脚本
- 返回当前计数和是否放行

`RedisConcurrencyLeaseStore`
- 申请租约
- 释放租约
- 封装 Lua 脚本调用
- 管理 `cc:{scope_key}` 和 `cc:lease:{lease_id}` 两类 key

### 6.4 新增控制器层
新增一个集中控制文件，建议放在：

- `services/reliability.py`
或
- `governance/reliability.py`

包含两个小组件：

- `RateLimiter`
- `ConcurrencyController`

职责：

- 从 `ToolDefinition` 和 `AuthContext` 计算作用域
- 调用 Redis store
- 把仓储返回结果转换成统一业务语义

### 6.5 在 server.py 接入
修改 [server.py](/D:/nanobot%20notebook/mcp_stock_server/server.py)：

在 `create_mcp_server(...)` 中：

- 装配 Redis client
- 装配 `RateLimiter`
- 装配 `ConcurrencyController`

在 `dispatch_tool(...)` 中：

1. 查 tool definition
2. 先跑限流
3. 再申请并发租约
4. 进入 `dispatcher.dispatch(...)`
5. `finally` 中释放租约

如果并发申请失败：

- 返回 `error_response("concurrency_exhausted", "...")`

如果限流失败：

- 返回 `error_response("rate_limited", "...")`

### 6.6 dispatcher.py 保持轻量
[protocol/dispatcher.py](/D:/nanobot%20notebook/mcp_stock_server/protocol/dispatcher.py) 不接入 Redis，不增加异步资源控制职责。

只需要保证：

- 对入口层抛出的 `ToolDispatchError` 正常转成标准响应
- 审计拒绝请求时能记录正确错误码

## 7. 默认策略建议
只针对当前 active tools 配置：

### 7.1 `get_stock_daily_bars`
- `rate_limit_scope = tenant_tool`
- `rate_limit_capacity = 120`
- `rate_limit_window_seconds = 60`
- `concurrency_scope = tenant_tool`
- `max_concurrency = 20`

### 7.2 `get_technical_snapshot`
- `rate_limit_scope = tenant_tool`
- `rate_limit_capacity = 60`
- `rate_limit_window_seconds = 60`
- `concurrency_scope = tenant_tool`
- `max_concurrency = 8`

### 7.3 `screen_b1_stocks`
- `rate_limit_scope = tenant_tool`
- `rate_limit_capacity = 20`
- `rate_limit_window_seconds = 60`
- `concurrency_scope = tenant_tool`
- `max_concurrency = 2`

### 7.4 `upsert_stock_daily_bars`
- `rate_limit_scope = tenant_tool`
- `rate_limit_capacity = 12`
- `rate_limit_window_seconds = 60`
- `concurrency_scope = tenant_tool`
- `max_concurrency = 2`

### 7.5 `insert_stock_daily_bars_after_close`
- `rate_limit_scope = tenant_tool`
- `rate_limit_capacity = 6`
- `rate_limit_window_seconds = 60`
- `concurrency_scope = tenant_tool`
- `max_concurrency = 1`

这些只是首版默认值，重点是先把机制和接线做对。

## 8. 假设与取舍
- 假设可以引入 Redis，并且 Redis 可作为共享短期控制状态源
- 首版只实现固定窗口限流，不做滑动窗口
- 首版使用 `ZSET + lease 明细` 的租约结构，不用“实例 ID 集合”这种过粗模型
- 首版只实现“租约申请 + 释放 + TTL 回收”，不做续租
- 首版不实现排队等待，只实现立即拒绝
- 首版不把限流并发配置暴露到 capability manifest
- 首版不调整 MySQL task store 和 recovery 主体结构，只在入口层增加控制

## 9. 实施顺序建议
如果后续进入实现阶段，推荐顺序如下：

1. 先做 Redis 限流
2. 再做 Redis 并发租约
3. 最后在 `server.py` 完成统一接线
