# TradingBot 核心流程速查

以下内容结合 `trading_bot.py` 源码，梳理单向刷量模式的真实执行逻辑，便于对照实盘行为理解代码。

## 1. 主循环骨架

- `TradingBot.run()` 是整体入口，连接交易所后进入 `while not self.shutdown_requested` 主循环（`trading_bot.py:490`、`trading_bot.py:520`）。
- 每轮循环都会：
  1. 拉取活跃订单并缓存所有平仓单（`trading_bot.py:522`-`trading_bot.py:533`）。
  2. 调用 `_log_status_periodically()`，每 60 秒输出持仓与挂单数量；若发现持仓与平仓挂单数量不匹配则触发停机和通知（`trading_bot.py:363`-`trading_bot.py:414`）。
  3. 调用 `_check_price_condition()` 判断是否触发用户设置的停机价/暂停价（`trading_bot.py:449`-`trading_bot.py:476`）。
  4. 在未停机且仓位正常时，根据冷却与网格条件决定是否继续开新单（见下文）。

## 2. 冷却节奏与最大挂单

- `_calculate_wait_time()` 根据当前平仓挂单数量与 `--max-orders`、`--wait-time` 计算冷却时间（`trading_bot.py:163`-`trading_bot.py:191`）。
  - 已有挂单数量降低 → 立即允许继续开单。
  - 当挂单数接近或超过 `max_orders` 时，会放大冷却时间或直接返回 `1`（表示继续等待）。
  - 一旦新开单成功会更新 `self.last_open_order_time`，下一次要等冷却周期过后才会返回 `0`。

## 3. 网格间隔（`--grid-step`）

- `_meet_grid_step_condition()` 限制下一笔开仓所对应的平仓单，与最近的平仓挂单必须相隔至少 `grid_step%`（`trading_bot.py:422`-`trading_bot.py:447`）。
  - 做多 (`direction=buy`) 时：取当前所有平仓单中的最低价 `next_close_price`，计算若此时开新仓、按止盈 `take_profit` 生成的平仓价 `new_order_close_price`。只有当 `next_close_price / new_order_close_price > 1 + grid_step/100` 时才允许开仓。
  - 做空 (`direction=sell`) 时对称处理，确保新的平仓价相比当前最高价至少相差 `grid_step%`。
  - 若当前没有任何平仓挂单，则不限制（直接返回 `True`）。
  - **实盘含义**：只有价格向不利方向移动到足够远（达到 `grid_step` 所设百分比差距），才会开启下一格网格并挂出对应的平仓单。

## 4. 开单与监控

- `_place_and_monitor_open_order()` 先向交易所发送限价单（`trading_bot.py:193`-`trading_bot.py:221`）。
  - 如果订单即刻成交或 WebSocket 在 10 秒内回报成交，会直接进入 `_handle_order_result()` 的“已成交”分支。
  - 否则进入追价/撤单逻辑。

### 4.1 追价与撤单

- `_handle_order_result()` 会周期性查询盘口价格与订单状态（`trading_bot.py:227`-`trading_bot.py:295`）。
  - 做多时，只要最新买价仍低于开单价，就等待成交；一旦价格变差或超时，主动撤单重新开始。
  - 撤单成功后，如果有部分成交，记录成交数量以便后续挂对应的平仓单。

### 4.2 平仓单生成

- 成交分支中，普通模式会按照 `take_profit` 计算目标平仓价，并挂出同等数量的反向限价单（`trading_bot.py:240`-`trading_bot.py:259`）。
- 若启用 `--boost`（仅对部分交易所开放），则直接调用 `place_market_order()` 反向市价成交，不挂限价平仓单（`trading_bot.py:232`-`trading_bot.py:238`）。
- 对部分成交场景，代码也会在撤单后为成交部分挂出对应的止盈单（`trading_bot.py:331`-`trading_bot.py:357`）。

## 5. 停机与暂停保护

- `stop_price` 达到时会立即停机并发送通知（`trading_bot.py:538`-`trading_bot.py:544`）。
- `pause_price` 达到时仅暂停 5 秒再重试，不退出脚本（`trading_bot.py:546`-`trading_bot.py:548`）。
- 仓位与活跃平仓单差异过大时会触发人工干预提示（`trading_bot.py:395`-`trading_bot.py:408`）。

## 6. 实盘现象与代码对应

- **单向网格**：设置 `--direction buy` 时脚本只会挂买入开仓 + 卖出平仓组合，`close_order_side` 属性自动求得（`trading_bot.py:36`）。
- **止盈即挂**：每次开仓成交后立即挂出按 `take_profit` 计算的平仓单（普通模式），与你观察到的 0.02% 止盈行为一致。
- **价格回撤再开新格**：只有当价格走反向、导致下一笔平仓价与最近一笔平仓价间隔超过 `grid_step%` 时才会触发下一次开仓，与 “回撤 0.5% 才开新仓” 的体验相符。
- **循环继续**：平仓单成交后，挂单数减少，`_calculate_wait_time()` 会返回 0，从而允许下一笔开仓进入循环，形成周而复始的刷量流程。

## 7. 进一步学习建议

- 通读 `exchanges/paradex.py` 了解 `place_open_order()` 与 `fetch_bbo_prices()` 的实现细节，确认盘口价格来源。
- 若需要调整策略，可从 `_calculate_wait_time()`（控制节奏）、`_meet_grid_step_condition()`（控制网格密度）着手。
- 结合 `docs/trading_flow.puml` 查看顺序流程图，与本指南互相印证。

通过上述要点，可以更准确地将实盘观测与代码逻辑对应起来，为后续改造或优化打下基础。
