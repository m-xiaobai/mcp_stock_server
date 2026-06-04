# `mcp_stock_server` 分批版技术指标服务方案

## Summary

为 `mcp_stock_server` 设计一个**分批返回技术快照**的能力，供 `nanobot-stock` 的 `market-scoring` 阶段消费。  
MCP server 只负责：

- 获取原始日线
- 计算技术指标
- 输出 `technical_snapshot`
- 支持单只和分批请求

MCP server **不负责评分**、不过滤股票、不输出 `technical_score`。

整体定位：

```text
行情源
-> mcp_stock_server
-> bars + technical_snapshot
-> nanobot-stock market-scoring
```

## API Design

### 1. 推荐新增批量工具

新增 MCP 工具：

- `get_technical_snapshots`

输入建议：

```json
{
  "symbols": ["600001", "000001", "600519"],
  "trade_date": "2026-06-03",
  "lookback_days": 60,
  "include_bars": false
}
```

输出建议：

```json
{
  "trade_date": "2026-06-03",
  "items": [
    {
      "symbol": "600001",
      "bars_count": 60,
      "technical_snapshot": { ... }
    },
    {
      "symbol": "000001",
      "bars_count": 60,
      "technical_snapshot": { ... }
    }
  ],
  "partial_failures": [
    {
      "symbol": "300001",
      "reason": "insufficient_history"
    }
  ]
}
```

### 2. 保留单只工具

同时保留单只版本，方便调试与复用：

- `get_technical_snapshot`

输入：

```json
{
  "symbol": "600001",
  "trade_date": "2026-06-03",
  "lookback_days": 60,
  "include_bars": true
}
```

输出：

```json
{
  "symbol": "600001",
  "trade_date": "2026-06-03",
  "bars_count": 60,
  "bars": [...],
  "technical_snapshot": { ... }
}
```

批量接口内部可以复用单只逻辑。

## `technical_snapshot` Contract

### 3. 第一版字段

基础行情：

- `close`
- `prev_close`
- `high`
- `low`

均线：

- `ma5`
- `ma10`
- `ma20`
- `ma60`

区间位置：

- `high_20d`
- `low_20d`
- `range_position_20d`
- `close_position`

量能：

- `latest_volume`
- `avg_volume_5d`
- `volume_ratio`

动量：

- `close_3d_change_pct`

MACD：

- `macd_dif`
- `macd_dea`
- `macd_bar`
- `macd_signal`

RSI：

- `rsi_6`
- `rsi_12`
- `rsi_24`
- `rsi_state`

量价与风险辅助标签：

- `volume_price_pattern`
- `long_upper_shadow`
- `weak_close_after_intraday_strength`

数据质量：

- `data_sufficiency`

### 4. 字段定义

- `range_position_20d = (close - low_20d) / (high_20d - low_20d)`
- `close_position = (close - low) / (high - low)`
- `volume_ratio = latest_volume / avg_volume_5d`
- `close_3d_change_pct = (close[-1] - close[-4]) / close[-4] * 100`

枚举字段建议：

- `macd_signal`
  - `bullish_above_zero`
  - `bullish_recovery`
  - `bearish_above_zero`
  - `bearish_below_zero`
  - `neutral`

- `rsi_state`
  - `overbought`
  - `strong_not_overbought`
  - `neutral`
  - `weak`
  - `oversold`

- `volume_price_pattern`
  - `volume_up_price_up`
  - `volume_up_price_down`
  - `volume_shrink_price_up`
  - `volume_shrink_price_down`
  - `volume_up_weak_close`
  - `neutral`

- `data_sufficiency`
  - `ok`
  - `insufficient_history`
  - `invalid_price_range`
  - `invalid_volume`

## Indicator Computation

### 5. 数据窗口

默认 `lookback_days=60`。

原因：

- 足够计算 `ma60`
- 足够计算 MACD
- 足够计算 RSI
- 支持近 20 日区间与近 5 日均量

少于 60 根时仍尽量返回，但要标记 `data_sufficiency`。

### 6. 计算口径

均线：

- `maN = close rolling mean`

MACD：

- `EMA12 = close.ewm(span=12, adjust=False).mean()`
- `EMA26 = close.ewm(span=26, adjust=False).mean()`
- `DIF = EMA12 - EMA26`
- `DEA = DIF.ewm(span=9, adjust=False).mean()`
- `BAR = (DIF - DEA) * 2`

RSI：

- 使用 Wilder EMA / SMMA 口径
- `gain/loss -> ewm(alpha=1/period, adjust=False)`

量能：

- `avg_volume_5d = mean(volume[-6:-1])`
- `latest_volume = volume[-1]`

风险辅助：

- `long_upper_shadow`
  - 基于最新一根 K 线，上影线占总振幅比例超过阈值
- `weak_close_after_intraday_strength`
  - 最新收盘明显低于日内高点，且伴随强振幅或放量

## Batch Processing Design

### 7. 分批能力由调用方控制，MCP 负责批量消费

MCP server 不负责“切批策略”，但要支持一次处理多个 `symbols`。

调用方负责决定每批多少只；MCP 负责：

- 遍历 symbols
- 对每只股票取日线
- 计算 snapshot
- 汇总结果
- 记录失败项

也就是说：

- `nanobot-stock` 决定 `batch_size=5`
- `mcp_stock_server` 接收这一批并返回批量结果

### 8. 单只失败不影响整批

批量工具应采用“部分成功”模式：

- 成功的 symbol 正常返回
- 失败的 symbol 进入 `partial_failures`
- 不因为一只股票失败而让整个批次报错

仅在系统级故障时整批失败，例如：

- 行情源不可用
- 参数非法
- 服务内部异常

## Error Handling

### 9. 数据不足策略

- 少于 20 根 bars：
  - 仍返回 snapshot
  - 但 `data_sufficiency = insufficient_history`
  - 指标无法计算的字段可为 `null`

- `high == low`：
  - `close_position = null`
  - `data_sufficiency = invalid_price_range`

- 量能缺失或均量为 0：
  - `volume_ratio = null`
  - `data_sufficiency = invalid_volume`

### 10. 返回策略

推荐“尽量结构化返回”，不要轻易抛异常。  
异常仅用于：

- 输入参数无效
- 数据源调用完全失败
- 服务内部不可恢复错误

普通数据问题优先通过：

- `data_sufficiency`
- `partial_failures`

表达。

## Implementation Structure

### 11. 建议模块划分

MCP server 内部建议分为：

- `data_provider`
  - 获取原始日线
- `indicator_calculator`
  - 计算 MA / MACD / RSI / 量能 / 位置
- `snapshot_builder`
  - 组装 `technical_snapshot`
- `mcp_tools`
  - 暴露 `get_technical_snapshot(s)` 工具

每层只做一件事，不把取数、算指标、MCP 输出混在同一个大函数里。

### 12. 输出稳定性要求

字段名、枚举值、空值策略必须固定。  
后续 `nanobot-stock` 会依赖这些契约做评分，不能让 MCP 随实现变化频繁改字段。

## Test Plan

需要覆盖：

- 单只 snapshot 正常返回
- 批量 symbols 返回 `items + partial_failures`
- `MA/MACD/RSI` 计算结果对固定样本稳定
- `range_position_20d` / `close_position` 在合法区间内
- `macd_signal` / `rsi_state` / `volume_price_pattern` 枚举值正确
- 少于 20 根 bars 时 `data_sufficiency=insufficient_history`
- 单只失败不导致整批失败
- `include_bars=true/false` 时输出形态正确

## Assumptions

- `mcp_stock_server` 已具备稳定的日线数据获取能力
- 第一版只处理日线，不处理分钟级和实时盘口
- 第一版参考 `daily_stock_analysis` 的指标层口径，但不实现其评分层
- 调用方会按批传入 `symbols`，MCP 只负责处理批量请求，不决定批大小
- `nanobot-stock` 后续会基于本方案的 `technical_snapshot` 实现 `market-scoring`
