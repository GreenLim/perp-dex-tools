# Backpack 对冲模式速查

本文整理 `python hedge_mode.py --exchange backpack --ticker BTC --size 0.05 --iter 20` 的执行流程，便于快速理解对冲模式的关键步骤与代码位置。

## 入口与参数

- `hedge_mode.py:20-112` 解析命令行参数、加载 `.env`，并根据 `--exchange` 选择对应 `HedgeBot` 实现。
- 当选择 `backpack` 时，实例化 `hedge/hedge_mode_bp.py:32` 中的 `HedgeBot`，传入 `order_quantity`、`fill_timeout`、`iterations` 等参数后调用 `run()`。

## 初始化阶段

| 步骤 | 代码位置 | 说明 |
| --- | --- | --- |
| 初始化客户端 | `hedge/hedge_mode_bp.py:1004-1014` | `initialize_lighter_client()` 与 `initialize_backpack_client()` 启动两个交易所的 SDK。 |
| 合约信息 | `hedge/hedge_mode_bp.py:1016-1018` | 获取 Backpack 合约 ID、tick size 以及 Lighter 的 market index、精度倍数。 |
| WebSocket 订阅（Backpack） | `hedge/hedge_mode_bp.py:1024-1055` | `setup_backpack_websocket()` 建立账户私有频道；内部 `order_update_handler` 负责同步订单状态（`hedge_mode_bp.py:768-845`），`handle_backpack_order_book_update()` 维护深度数据（`hedge_mode_bp.py:656-716`）。 |
| WebSocket 订阅（Lighter） | `hedge/hedge_mode_bp.py:1057-1074` | 启动 `handle_lighter_ws()` 任务，等待盘口快照，用于后续市价对冲。 |

当两个订单簿准备就绪后，进入对冲循环。

## 主交易循环（三步）

循环定义：`hedge/hedge_mode_bp.py:1069-1168`

1. **Step 1：开仓（刷量方向）**
   - 调用 `place_backpack_post_only_order('buy', order_quantity)` 在 Backpack 挂 Maker 单（`hedge_mode_bp.py:631-665`）。
   - Backpack WebSocket 成交回调 `handle_backpack_order_update()` 把成交数量赋值给 `current_lighter_side/quantity`，并设置 `waiting_for_lighter_fill=True`（`hedge_mode_bp.py:721-748`）。
   - 主循环检测到该标志后，调用 `place_lighter_market_order()` 在 Lighter 以市价对冲相同数量（`hedge_mode_bp.py:748-815`）。
   - `monitor_lighter_order()` 搭配 `handle_lighter_order_result()` 确认市价单成交并记录日志（`hedge_mode_bp.py:816-870`, `hedge_mode_bp.py:209-236`）。

2. **Step 2：反向开单**
   - 重复与 Step 1 类似的流程，方向换为 `sell`，实现另一侧的挂单与对冲，维持双边活跃度。

3. **Step 3：仓位归零**
   - 若两边仓位未平衡，按实际剩余头寸自动选择 `buy/sell` 再挂一次 Backpack 单，并用 Lighter 市价完成最终对冲（`hedge_mode_bp.py:1126-1168`）。

循环会执行 `--iter` 次或在发生错误/超时/手动停止时退出。

## 关键协程与状态变量

- `place_bbo_order()`：根据 Backpack 最优价挂 POST-ONLY 单（`hedge_mode_bp.py:582-629`）。
- `backpack_order_status`：由 WebSocket 更新，驱动 `place_backpack_post_only_order()` 的重试与撤单逻辑。
- `waiting_for_lighter_fill` / `order_execution_complete`：协调主循环与对冲下单的时序。
- `backpack_position`、`lighter_position`：持续核对净仓位差，若差值超过两倍下单量会触发错误并退出（`hedge_mode_bp.py:1079-1086`）。

## 异常与清理

- `monitor_lighter_order()` 超时 30 秒未成交会记入日志并采用 fallback，将订单标记为完成（`hedge_mode_bp.py:816-870`）。
- `HedgeBot.run()` 捕获 `KeyboardInterrupt` 并调用 `shutdown()` 关闭 WebSocket、写回日志（`hedge_mode_bp.py:1175-1187`）。

## 参考流程图

- PlantUML 文件：`docs/hedge_mode_backpack_flow.puml`
- 可使用支持 PlantUML 的 IDE 或运行 `plantuml docs/hedge_mode_backpack_flow.puml` 渲染。

## 核心代码解析

### 1. HedgeBot 构造与运行

```python
# hedge/hedge_mode_bp.py:32
class HedgeBot:
    def __init__(self, ticker: str, order_quantity: Decimal,
                 fill_timeout: int = 5, iterations: int = 20, sleep_time: int = 0):
        self.ticker = ticker
        self.order_quantity = order_quantity
        self.fill_timeout = fill_timeout
        self.iterations = iterations
        self.sleep_time = sleep_time
        ...

    async def run(self):
        self.setup_signal_handlers()
        try:
            await self.trading_loop()
        finally:
            self.shutdown()
```

`hedge_mode.py` 将命令行传入的 `ticker/size/iter` 转化为 `Decimal` 后实例化 `HedgeBot`，`run()` 会启动完整的对冲循环，并在退出时释放资源。

### 2. 初始化流程

```python
# hedge/hedge_mode_bp.py:1004-1055
self.initialize_lighter_client()
self.initialize_backpack_client()
self.backpack_contract_id, self.backpack_tick_size = await self.get_backpack_contract_info()
self.lighter_market_index, self.base_amount_multiplier, \
    self.price_multiplier, self.tick_size = self.get_lighter_market_config()

await self.setup_backpack_websocket()
self.lighter_ws_task = asyncio.create_task(self.handle_lighter_ws())
```

- `initialize_*` 方法读取 `.env` 中的 API Key/私钥，创建 SDK 客户端。
- `get_backpack_contract_info()` 与 `get_lighter_market_config()` 获取交易所合约信息，保存 `tick_size`、数量/价格精度。
- `setup_backpack_websocket()` 在私有频道监听订单更新，同时通过 `handle_backpack_order_book_update()` 维护盘口缓存；`handle_lighter_ws()` 则订阅 Lighter 行情与成交。

### 3. 主循环骨架

```python
# hedge/hedge_mode_bp.py:1069-1168
while iterations < self.iterations and not self.stop_flag:
    self.logger.info("[STEP 1] ...")
    await self.place_backpack_post_only_order('buy', self.order_quantity)
    ...
    self.logger.info("[STEP 2] ...")
    await self.place_backpack_post_only_order('sell', self.order_quantity)
    ...
    self.logger.info("[STEP 3] ...")
    if self.backpack_position != 0:
        side = 'sell' if self.backpack_position > 0 else 'buy'
        await self.place_backpack_post_only_order(side, abs(self.backpack_position))
```

三步结构循环执行 —— 先刷买单、再刷卖单、最后根据剩余仓位补齐。每一步都等待回调确认完成后才继续。

### 4. Backpack 挂单与状态机

```python
# hedge/hedge_mode_bp.py:582-665
async def place_backpack_post_only_order(self, side: str, quantity: Decimal):
    order_id = await self.place_bbo_order(side, quantity)  # 依据当前 BBO 选价
    start_time = time.time()
    while not self.stop_flag:
        if self.backpack_order_status == 'CANCELED':
            order_id = await self.place_bbo_order(side, quantity)  # 重挂
            start_time = time.time()
        elif self.backpack_order_status in [...]:
            if time.time() - start_time > 10:
                await self.backpack_client.cancel_order(order_id)
        elif self.backpack_order_status == 'FILLED':
            break
        await asyncio.sleep(0.5)
```

`place_bbo_order()` 使用本地订单簿计算出略优于对手盘的挂单价（POST-ONLY），随后 `place_backpack_post_only_order()` 通过 `backpack_order_status` 自循环监控：  
- `FILLED`：结束等待；  
- `CANCELED`：重新下单；  
- 超过 10 秒仍未成交则主动撤单。

状态由 WebSocket 回调 `handle_backpack_order_update()` 驱动：

```python
# hedge/hedge_mode_bp.py:768-845
def order_update_handler(order_data):
    ...
    if status == 'FILLED':
        if side == 'buy':
            self.backpack_position += filled_size
        else:
            self.backpack_position -= filled_size
        self.handle_backpack_order_update({...})
        self.backpack_order_status = 'FILLED'
```

### 5. Lighter 市价对冲

当 Backpack 订单成交时，`handle_backpack_order_update()` 会准备对冲参数：

```python
# hedge/hedge_mode_bp.py:721-748
self.current_lighter_side = 'sell' if side == 'buy' else 'buy'
self.current_lighter_quantity = filled_size
self.current_lighter_price = price
self.waiting_for_lighter_fill = True
```

主循环看到 `waiting_for_lighter_fill` 被置位后调用：

```python
# hedge/hedge_mode_bp.py:748-815
async def place_lighter_market_order(self, lighter_side, quantity, price):
    best_bid, best_ask = self.get_lighter_best_levels()
    if lighter_side == 'buy':
        price = best_ask[0] * Decimal('1.002')  # 向上滑点保证成交
    else:
        price = best_bid[0] * Decimal('0.998')

    tx_info, error = self.lighter_client.sign_create_order(...)
    tx_hash = await self.lighter_client.send_tx(...)
    await self.monitor_lighter_order(client_order_index)
```

- 根据 Lighter 盘口对市价做 0.2% 的滑点确保吃单。
- 借助官方 `SignerClient` 签名并发送交易。
- `monitor_lighter_order()` 轮询等待成交，超时 30 秒则触发 fallback。

成交结果由 `handle_lighter_order_result()` 处理：

```python
# hedge/hedge_mode_bp.py:209-236
if order_data["is_ask"]:
    self.lighter_position -= Decimal(order_data["filled_base_amount"])
else:
    self.lighter_position += Decimal(order_data["filled_base_amount"])
self.lighter_order_filled = True
self.order_execution_complete = True
self.log_trade_to_csv('Lighter', ...)
```

### 6. 仓位监控与退出

```python
# hedge/hedge_mode_bp.py:1079-1086
if abs(self.backpack_position + self.lighter_position) > self.order_quantity * 2:
    self.logger.error("❌ Position diff is too large")
    break
```

每轮交易都会核对 Backpack + Lighter 的净仓位，超过两倍下单量则认为风险过大，中断程序。循环结束后 `shutdown()` 停止 WebSocket、清理日志句柄。

---

通过阅读上述代码，可以更直观地理解对冲模式的执行细节：Backpack 负责挂单制造量，Lighter 负责迅速吃掉等量反向仓位，从而保持净风险中性。
通过以上结构，您可以快速定位对冲模式中“Backpack 挂单 + Lighter 对冲”的关键实现，便于学习与二次开发。
