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
通过以上结构，您可以快速定位对冲模式中"Backpack 挂单 + Lighter 对冲"的关键实现，便于学习与二次开发。

## 平仓逻辑详解

对冲交易中的"平仓"并非传统意义上的单边平仓，而是通过**对冲**来实现风险中性。理解平仓时机和逻辑对于正确运行对冲机器人至关重要。

### 核心概念：什么是"平仓"？

在对冲模式中，"平仓"指的是**通过反向操作使净仓位归零**：

- **开仓**：在 Backpack 买入 0.1 ETH（做多）
- **对冲**：在 Lighter 卖出 0.1 ETH（做空）
- **净仓位**：+0.1 - 0.1 = 0（风险中性）

这不是真正的"平仓"，而是通过两个交易所的反向持仓来对冲风险。

### 三步交易循环中的平仓时机

每轮完整的交易循环包含 3 个步骤，每个步骤都有不同的平仓逻辑：

#### STEP 1：开多单（买入）

**代码位置：** `hedge/hedge_mode_bp.py:1066-1105`

```python
# STEP 1: 开仓
side = 'buy'
await self.place_backpack_post_only_order(side, self.order_quantity)

# 等待 Backpack 成交
while not self.order_execution_complete:
    # 当 Backpack 买单成交后，触发对冲
    if self.waiting_for_lighter_fill:
        await self.place_lighter_market_order(
            self.current_lighter_side,  # 'sell'
            self.current_lighter_quantity,  # 成交数量
            self.current_lighter_price
        )
```

**执行流程：**

1. **Backpack 挂买单**：在 Backpack 以略低于最优卖价（best ask）挂 POST-ONLY 买单
   - 例如：最优卖价 2500.5，挂单价格 2500.0
   - 数量：`order_quantity`（如 0.1 ETH）

2. **等待成交**：WebSocket 监听订单状态

3. **成交触发对冲**：当 Backpack 买单成交时
   - WebSocket 回调 `handle_backpack_order_update()` (代码行 721-744)
   - 设置 `current_lighter_side = 'sell'`（反向操作）
   - 设置 `waiting_for_lighter_fill = True`

4. **Lighter 市价卖出**：立即在 Lighter 以市价卖出相同数量
   - 价格：`best_bid * 0.998`（略低于最优买价，确保成交）
   - 数量：与 Backpack 成交数量完全一致

**仓位状态：**
```
Backpack: +0.1 ETH (做多)
Lighter:  -0.1 ETH (做空)
净仓位:    0 ETH (风险中性) ✅
```

**平仓时机：** STEP 1 本身不涉及平仓，而是建立对冲仓位。

---

#### STEP 2：开空单（卖出）

**代码位置：** `hedge/hedge_mode_bp.py:1107-1138`

```python
# STEP 2: 反向开仓（同时平掉 STEP 1 的仓位）
side = 'sell'
await self.place_backpack_post_only_order(side, self.order_quantity)

# 等待成交并对冲
if self.waiting_for_lighter_fill:
    await self.place_lighter_market_order(
        self.current_lighter_side,  # 'buy'
        self.current_lighter_quantity,
        self.current_lighter_price
    )
```

**执行流程：**

1. **Backpack 挂卖单**：在 Backpack 以略高于最优买价（best bid）挂卖单
   - 例如：最优买价 2499.5，挂单价格 2500.0
   - 数量：`order_quantity`（0.1 ETH）

2. **成交触发对冲**：Backpack 卖单成交后
   - 设置 `current_lighter_side = 'buy'`
   - Lighter 市价买入相同数量

**仓位变化：**
```
STEP 1 后:
  Backpack: +0.1 ETH
  Lighter:  -0.1 ETH

STEP 2 Backpack 成交后:
  Backpack: +0.1 - 0.1 = 0 ETH ✅
  Lighter:  -0.1 ETH (待对冲)

STEP 2 Lighter 对冲后:
  Backpack: 0 ETH
  Lighter:  -0.1 + 0.1 = 0 ETH ✅
净仓位:    0 ETH ✅
```

**平仓时机：** STEP 2 实际上**平掉了 Backpack 的仓位**（从 +0.1 变成 0），同时也**平掉了 Lighter 的仓位**（从 -0.1 变成 0）。

**关键理解：**
- Backpack 的卖单 = 平掉之前的买单仓位
- Lighter 的买单 = 平掉之前的卖单仓位
- 两边同时归零 = 完整的平仓周期

---

#### STEP 3：修正残余仓位（兜底平仓）

**代码位置：** `hedge/hedge_mode_bp.py:1140-1173`

```python
# STEP 3: 检查并平掉残余仓位
if self.backpack_position == 0:
    continue  # 仓位已平，跳过
elif self.backpack_position > 0:
    side = 'sell'  # 多头仓位，需要卖出平仓
else:
    side = 'buy'   # 空头仓位，需要买入平仓

# 平掉剩余仓位
await self.place_backpack_post_only_order(side, abs(self.backpack_position))
```

**执行流程：**

1. **检查仓位**：读取 `self.backpack_position` 的当前值
   - 如果 = 0：跳过 STEP 3，进入下一轮循环
   - 如果 > 0：持有多头，需要卖出平仓
   - 如果 < 0：持有空头，需要买入平仓

2. **动态确定方向**：根据仓位自动选择买/卖方向

3. **平仓数量**：`abs(self.backpack_position)`（仓位的绝对值）

4. **对冲操作**：与 STEP 1/2 相同，Backpack 成交后立即在 Lighter 反向操作

**典型场景：**

**场景 A：完美对冲（理想情况）**
```python
# STEP 2 结束后
self.backpack_position = 0  # 已完全平仓
# → STEP 3 跳过，直接进入下一轮
```

**场景 B：部分成交导致残余仓位**
```python
# STEP 2 预期卖出 0.1 ETH，但只成交了 0.08 ETH
self.backpack_position = 0.1 - 0.08 = 0.02 ETH  # 残余多头

# STEP 3 执行
side = 'sell'  # 卖出平仓
quantity = 0.02  # 平掉剩余的 0.02 ETH
```

**场景 C：对冲失败导致不平衡**
```python
# STEP 1: Backpack 买入 0.1 ETH，Lighter 卖出超时（fallback）
self.backpack_position = +0.1 ETH
self.lighter_position = 0 ETH  # 对冲失败！

# STEP 2: Backpack 卖出 0.1 ETH，Lighter 买入 0.1 ETH
self.backpack_position = 0 ETH
self.lighter_position = +0.1 ETH  # 仓位反向！

# STEP 3 只检查 Backpack 仓位（问题所在！）
self.backpack_position == 0 → 跳过
# ❌ Lighter 仓位 0.1 ETH 未被处理！
```

**STEP 3 的局限性：**
⚠️ **STEP 3 只修正 Backpack 仓位，不修正 Lighter 仓位！** 这是代码设计上的一个问题点。

---

### 平仓的触发时机总结

| 步骤 | Backpack 操作 | Lighter 对冲 | 平仓效果 | 触发条件 |
|------|---------------|--------------|---------|----------|
| STEP 1 | 买入 0.1 ETH | 卖出 0.1 ETH | 建立对冲仓位 | Backpack 买单成交 |
| STEP 2 | 卖出 0.1 ETH | 买入 0.1 ETH | **平掉 STEP 1 的仓位** | Backpack 卖单成交 |
| STEP 3 | 买/卖（动态） | 买/卖（反向） | **修正残余仓位** | `backpack_position != 0` |

### 平仓时机的代码实现

#### 1. **自动对冲触发**

当 Backpack 订单成交时，WebSocket 自动触发对冲：

```python
# hedge/hedge_mode_bp.py:860-920
def order_update_handler(order_data):
    status = order_data.get('status')

    if status == 'FILLED':
        # 更新 Backpack 仓位
        if side == 'buy':
            self.backpack_position += filled_size  # 增加多头
        else:
            self.backpack_position -= filled_size  # 减少多头（增加空头）

        # 准备 Lighter 对冲参数
        self.handle_backpack_order_update({
            'side': side,
            'filled_size': filled_size,
            'price': price
        })
        self.backpack_order_status = 'FILLED'
```

#### 2. **Lighter 反向操作**

```python
# hedge/hedge_mode_bp.py:721-744
def handle_backpack_order_update(self, order_data):
    side = order_data.get('side')

    # 确定反向操作
    if side == 'buy':
        lighter_side = 'sell'  # Backpack 买 → Lighter 卖
    else:
        lighter_side = 'buy'   # Backpack 卖 → Lighter 买

    self.current_lighter_side = lighter_side
    self.current_lighter_quantity = filled_size
    self.waiting_for_lighter_fill = True  # 通知主循环执行对冲
```

#### 3. **仓位更新**

```python
# Backpack 成交时更新仓位
if side == 'buy':
    self.backpack_position += filled_size
else:
    self.backpack_position -= filled_size

# Lighter 成交时更新仓位
if order_data["is_ask"]:  # 卖单
    self.lighter_position -= filled_base_amount
else:  # 买单
    self.lighter_position += filled_base_amount
```

### 平仓失败的常见原因

1. **Lighter 对冲超时**
   - Backpack 成交了，但 Lighter 30 秒内未确认
   - Fallback 机制直接标记完成，但仓位未真正对冲

2. **部分成交**
   - Backpack 订单部分成交（如 0.08/0.1 ETH）
   - Lighter 对冲了部分数量，但残余仓位未处理

3. **WebSocket 延迟**
   - 成交消息延迟到达
   - 程序已进入下一步，错过对冲时机

4. **网络故障**
   - Lighter 下单请求失败
   - 但 Backpack 仓位已建立

### 改进建议：完善平仓逻辑

#### 建议 1：STEP 3 同时检查两个交易所仓位

```python
# 改进的 STEP 3
net_position = self.backpack_position + self.lighter_position

if abs(net_position) < self.order_quantity * 0.01:  # 允许 1% 误差
    continue  # 仓位基本平衡，跳过
else:
    # 检查哪个交易所有残余仓位
    if abs(self.backpack_position) > abs(self.lighter_position):
        # Backpack 仓位更大，在 Backpack 平仓
        side = 'sell' if self.backpack_position > 0 else 'buy'
        await self.place_backpack_post_only_order(side, abs(self.backpack_position))
    else:
        # Lighter 仓位更大，在 Lighter 平仓
        side = 'sell' if self.lighter_position > 0 else 'buy'
        await self.place_lighter_market_order(side, abs(self.lighter_position), None)
```

#### 建议 2：增加紧急平仓功能

```python
async def emergency_close_all_positions(self):
    """紧急平掉所有仓位，不对冲"""
    self.logger.warning("⚠️ Emergency position closure initiated")

    # 平掉 Backpack 仓位
    if self.backpack_position != 0:
        side = 'sell' if self.backpack_position > 0 else 'buy'
        await self.place_backpack_market_order(side, abs(self.backpack_position))

    # 平掉 Lighter 仓位
    if self.lighter_position != 0:
        side = 'sell' if self.lighter_position > 0 else 'buy'
        await self.place_lighter_market_order(side, abs(self.lighter_position), None)
```

#### 建议 3：定时仓位校准

```python
async def verify_positions(self):
    """每 N 轮循环后查询实际仓位并校准"""
    if iterations % 10 == 0:  # 每 10 轮
        # 通过 API 查询实际仓位
        actual_bp = await self.query_backpack_actual_position()
        actual_lt = await self.query_lighter_actual_position()

        # 校准内部状态
        if abs(actual_bp - self.backpack_position) > 0.001:
            self.logger.warning(f"⚠️ Backpack position mismatch: tracked={self.backpack_position}, actual={actual_bp}")
            self.backpack_position = actual_bp

        if abs(actual_lt - self.lighter_position) > 0.001:
            self.logger.warning(f"⚠️ Lighter position mismatch: tracked={self.lighter_position}, actual={actual_lt}")
            self.lighter_position = actual_lt
```

### 平仓状态监控

**关键日志模式：**

```bash
# 正常平仓流程
[STEP 1] Backpack position: 0 | Lighter position: 0
✅ Backpack order filled: BUY 0.1 @ 2500
🚀 Lighter limit order sent: sell 0.1
📊 Lighter order filled: SHORT 0.1 @ 2499.8

[STEP 2] Backpack position: 0.1 | Lighter position: -0.1  # 仓位对冲中
✅ Backpack order filled: SELL 0.1 @ 2500.5
🚀 Lighter limit order sent: buy 0.1
📊 Lighter order filled: LONG 0.1 @ 2500.2

[STEP 3] Backpack position: 0 | Lighter position: 0  # ✅ 平仓完成！
```

**异常平仓模式：**

```bash
[STEP 1] Backpack position: 0 | Lighter position: 0
✅ Backpack order filled: BUY 0.1 @ 2500
🚀 Lighter limit order sent: sell 0.1
❌ Timeout waiting for Lighter order fill  # ⚠️ 对冲失败
⚠️ Using fallback - marking order as filled

[STEP 2] Backpack position: 0.1 | Lighter position: 0  # ❌ 仓位未对冲！
✅ Backpack order filled: SELL 0.1 @ 2500.5
📊 Lighter order filled: LONG 0.1 @ 2500.2

[STEP 3] Backpack position: 0 | Lighter position: 0.1  # ❌ Lighter 有残余仓位
# STEP 3 只检查 Backpack，跳过！仓位累积！
```

## 常见问题与故障排查

### 问题1：仓位持续累积（0.1 → 0.2 → 0.3 ETH）

**现象描述：**
运行对冲机器人后，发现某个交易所的仓位不断累积，从最初的 0.1 ETH 变成 0.2、0.3、0.4 ETH，而不是在每轮交易后归零。

**期望行为：**
每轮完整的对冲循环（STEP 1 → STEP 2 → STEP 3）结束后，两个交易所的仓位都应该归零或接近零。

**根本原因分析：**

仓位累积问题主要由以下几个原因导致：

#### 1. **Lighter 订单超时 Fallback 机制**

查看代码 `hedge/hedge_mode_bp.py:801-818`：

```python
async def monitor_lighter_order(self, client_order_index: int):
    start_time = time.time()
    while not self.lighter_order_filled and not self.stop_flag:
        if time.time() - start_time > 30:
            self.logger.error(f"❌ Timeout waiting for Lighter order fill")

            # ⚠️ 问题关键：Fallback 直接标记为完成
            self.logger.warning("⚠️ Using fallback - marking order as filled to continue trading")
            self.lighter_order_filled = True
            self.waiting_for_lighter_fill = False
            self.order_execution_complete = True  # ❌ 即使未成交也标记完成
            break
```

**问题：** 当 Lighter 订单在 30 秒内未收到成交确认（可能是 WebSocket 延迟、网络问题或订单实际未成交），代码会强制标记订单为"已完成"，但实际仓位并未对冲！

**后果：**
- Backpack 成交了 +0.1 ETH (做多)
- Lighter 订单超时，fallback 标记完成但实际未成交
- 净仓位：+0.1 ETH（未对冲）
- 下一轮继续交易，仓位累积到 +0.2 ETH

#### 2. **STEP 1 和 STEP 2 的等待循环提前退出**

查看代码 `hedge/hedge_mode_bp.py:1084-1097`：

```python
start_time = time.time()  # ⚠️ start_time 在 STEP 1 之前设置
while not self.order_execution_complete and not self.stop_flag:
    if self.waiting_for_lighter_fill:
        await self.place_lighter_market_order(...)
        break  # ❌ 调用后立即退出，不等待 order_execution_complete

    await asyncio.sleep(0.01)
    if time.time() - start_time > 180:  # 180秒超时
        self.logger.error("❌ Timeout waiting for trade completion")
        break  # ❌ 超时直接退出，不检查是否真的完成
```

**问题：**
1. `break` 语句在调用 `place_lighter_market_order()` 后立即退出循环
2. 没有等待 `order_execution_complete` 标志被设置为 `True`
3. 如果 Lighter 订单在 WebSocket 中延迟确认，程序已经进入下一步

#### 3. **STEP 2 使用 STEP 1 的 start_time**

```python
# STEP 1
start_time = time.time()  # 第1082行设置
await self.place_backpack_post_only_order('buy', ...)
while not self.order_execution_complete:
    ...

# STEP 2
self.logger.info("[STEP 2] ...")
# ⚠️ 没有重置 start_time！
while not self.order_execution_complete:
    ...
    if time.time() - start_time > 180:  # 使用的是 STEP 1 的时间
        break
```

**问题：** STEP 2 的超时检查使用的是 STEP 1 的 `start_time`，导致 STEP 2 可能几乎立即超时。

#### 4. **部分成交处理不完善**

Backpack 订单可能部分成交（PARTIALLY_FILLED），但代码会对每次部分成交都触发 Lighter 对冲订单。如果 WebSocket 消息重复或延迟，可能导致：
- 一个 Backpack 订单成交 0.1 ETH
- 收到多次成交通知
- 触发多次 Lighter 对冲订单
- 过度对冲，仓位反向累积

### 解决方案

#### 方案 1：修复等待逻辑（推荐）

```python
# STEP 1
self.order_execution_complete = False
self.waiting_for_lighter_fill = False
await self.place_backpack_post_only_order('buy', self.order_quantity)

step_start_time = time.time()  # 每个 STEP 独立计时
while not self.order_execution_complete and not self.stop_flag:
    if self.waiting_for_lighter_fill:
        await self.place_lighter_market_order(...)
        # ❌ 不要立即 break！继续等待 order_execution_complete

    await asyncio.sleep(0.01)
    if time.time() - step_start_time > 180:
        if not self.order_execution_complete:
            self.logger.error("❌ STEP 1 timeout - order not completed")
            # 检查仓位，决定是否继续
            break
```

#### 方案 2：移除 Lighter Fallback 或改进重试

```python
async def monitor_lighter_order(self, client_order_index: int):
    start_time = time.time()
    retry_count = 0
    max_retries = 3

    while not self.lighter_order_filled and not self.stop_flag:
        if time.time() - start_time > 30:
            retry_count += 1
            if retry_count >= max_retries:
                # 查询订单状态而不是直接 fallback
                order_status = await self.query_lighter_order_status(client_order_index)
                if order_status == "FILLED":
                    self.lighter_order_filled = True
                else:
                    self.logger.error("❌ Lighter order not filled after retries")
                    raise Exception("Lighter order timeout - position mismatch risk")
            else:
                self.logger.warning(f"⚠️ Retry {retry_count}/{max_retries}")
                start_time = time.time()

        await asyncio.sleep(0.1)
```

#### 方案 3：添加仓位核对与自动修正

```python
async def verify_and_correct_positions(self):
    """在每个循环迭代后验证仓位"""
    net_position = self.backpack_position + self.lighter_position

    if abs(net_position) > self.order_quantity * 0.1:  # 允许 10% 误差
        self.logger.warning(f"⚠️ Position mismatch detected: {net_position}")

        # 查询实际仓位
        actual_backpack = await self.query_backpack_position()
        actual_lighter = await self.query_lighter_position()

        # 更新内部状态
        self.backpack_position = actual_backpack
        self.lighter_position = actual_lighter

        # 如果仓位确实不平衡，主动平仓
        if abs(actual_backpack + actual_lighter) > self.order_quantity * 0.1:
            await self.emergency_hedge()
```

#### 方案 4：改进日志与监控

```python
# 在每个关键点记录详细日志
self.logger.info(f"[CHECKPOINT] After STEP 1: BP={self.backpack_position}, LT={self.lighter_position}, NET={self.backpack_position + self.lighter_position}")
self.logger.info(f"[FLAGS] execution_complete={self.order_execution_complete}, waiting_lighter={self.waiting_for_lighter_fill}, lighter_filled={self.lighter_order_filled}")

# 写入 CSV 时同时记录仓位
self.log_trade_to_csv(exchange, side, price, quantity,
                      backpack_position=self.backpack_position,
                      lighter_position=self.lighter_position)
```

### 最佳实践建议

1. **严格的状态机管理**
   - 确保每个订单都有明确的生命周期：`PENDING → PLACED → FILLED → HEDGED → COMPLETE`
   - 在状态转换时记录详细日志

2. **超时与重试策略**
   - 不要使用强制 fallback，而是查询实际订单状态
   - 实现指数退避重试机制
   - 为不同的失败场景设置不同的处理逻辑

3. **仓位核对机制**
   - 每轮循环结束后验证仓位
   - 定期（如每 10 轮）通过 API 查询实际仓位并校准
   - 设置仓位差异告警阈值

4. **幂等性保证**
   - 使用唯一的 `client_order_id` 防止重复下单
   - 对 WebSocket 消息去重
   - 记录已处理的订单 ID

5. **错误处理与恢复**
   - 遇到严重仓位偏差时暂停交易，等待人工介入
   - 实现紧急平仓功能
   - 保存完整的订单历史用于事后分析

6. **监控指标**
   - 实时监控净仓位（Backpack + Lighter）
   - 监控订单成交率（Backpack 成交但 Lighter 未成交的比例）
   - 记录 WebSocket 延迟和超时频率

### 调试技巧

查看日志文件 `logs/{exchange}_{ticker}_hedge_mode_log.txt`，寻找以下模式：

```
✅ Backpack order filled: BUY 0.1 @ 2500
🚀 Lighter limit order sent: sell 0.1
❌ Timeout waiting for Lighter order fill after 30.1s  # ⚠️ 危险信号
⚠️ Using fallback - marking order as filled  # ❌ 仓位可能未对冲
[STEP 2] Backpack position: 0.1 | Lighter position: 0.0  # ❌ 仓位不平衡
```

如果发现这种模式，说明 Lighter 对冲失败但被 fallback 掩盖了。

查看 CSV 交易记录 `logs/{exchange}_{ticker}_hedge_mode_trades.csv`：

```csv
exchange,timestamp,side,price,quantity
Backpack,2025-01-07T10:00:00,buy,2500,0.1
# ⚠️ 缺少对应的 Lighter sell 记录！
Backpack,2025-01-07T10:01:00,sell,2501,0.1
Lighter,2025-01-07T10:01:05,buy,2500.5,0.1
# ⚠️ Backpack 有 2 笔，Lighter 只有 1 笔
```

如果 Backpack 和 Lighter 的交易笔数不匹配，说明存在对冲失败的情况。

---

## Extended 交易所的平仓逻辑

当使用 `python hedge_mode.py --exchange extended --ticker ETH --size 0.03 --iter 20` 时，Extended 交易所的平仓机制与 Backpack 基本相同，但有一些细节差异。

### Extended 平仓机制概述

**核心流程：**
1. Extended 挂 **POST-ONLY Maker 单**（等待成交）
2. 成交后 Lighter 立即挂 **Market Taker 单**（对冲）
3. 通过 STEP 1 → STEP 2 → STEP 3 实现完整的开仓-平仓周期

### Extended 与 Backpack 的差异对比

| 特性 | Backpack | Extended |
|------|----------|----------|
| **挂单方式** | POST-ONLY Maker | POST-ONLY Maker |
| **价格策略** | BBO（最优价） | BBO（最优价） |
| **订单取消** | 10秒后超时取消 | 10秒后超时取消，但有价格检查 |
| **平仓触发** | STEP 2 卖单成交 | STEP 2 卖单成交 |
| **STEP 3 检查** | 只检查 Backpack 仓位 | 只检查 Extended 仓位 |
| **对冲交易所** | Lighter | Lighter |

### Extended POST-ONLY 订单逻辑

**代码位置：** `hedge/hedge_mode_ext.py:634-691`

```python
async def place_extended_post_only_order(self, side: str, quantity: Decimal):
    # 1. 挂单
    order_id, order_price = await self.place_bbo_order(side, quantity)
    start_time = time.time()

    while not self.stop_flag:
        # 2. 订单被取消 → 重新挂单
        if self.extended_order_status in ['CANCELED', 'CANCELLED']:
            order_id, order_price = await self.place_bbo_order(side, quantity)
            start_time = time.time()

        # 3. 订单等待成交
        elif self.extended_order_status in ['NEW', 'OPEN', 'PENDING', 'PARTIALLY_FILLED']:
            # 检查是否需要取消订单
            should_cancel = False
            if side == 'buy':
                if order_price < self.extended_best_bid:  # 买单价格低于最优买价
                    should_cancel = True
            else:
                if order_price > self.extended_best_ask:  # 卖单价格高于最优卖价
                    should_cancel = True

            # 超过 10 秒且价格不再是最优价 → 取消重挂
            if time.time() - start_time > 10:
                if should_cancel:
                    await self.extended_client.cancel_order(order_id)

        # 4. 订单成交 → 退出循环
        elif self.extended_order_status == 'FILLED':
            break
```

### Extended 的价格检查机制（关键差异）

Extended 有一个 Backpack 没有的**智能价格检查机制**：

```python
# 代码行 657-664
should_cancel = False
if side == 'buy':
    if order_price < self.extended_best_bid:  # 挂单价不再是最优
        should_cancel = True
else:
    if order_price > self.extended_best_ask:  # 挂单价不再是最优
        should_cancel = True
```

**含义：**
- **买单**：如果你的买单价格 **低于** 当前最优买价（best bid），说明市场价格上涨了，你的订单不再有竞争力 → 取消重挂
- **卖单**：如果你的卖单价格 **高于** 当前最优卖价（best ask），说明市场价格下跌了，你的订单不再有竞争力 → 取消重挂

**示例：**
```
STEP 1: 买单
- 挂单时：best_bid = 2499, best_ask = 2500 → 挂买单 @ 2499
- 10秒后：best_bid = 2501, best_ask = 2502 (市场价上涨)
- 检查：order_price (2499) < extended_best_bid (2501) → should_cancel = True
- 操作：取消旧单，以新价格 2501 重新挂单

STEP 2: 卖单
- 挂单时：best_bid = 2501, best_ask = 2502 → 挂卖单 @ 2502
- 10秒后：best_bid = 2499, best_ask = 2500 (市场价下跌)
- 检查：order_price (2502) > extended_best_ask (2500) → should_cancel = True
- 操作：取消旧单，以新价格 2500 重新挂单
```

### Extended 平仓时机详解

#### STEP 1：开多单（买入）

**流程：**
1. Extended 挂买单 @ best_bid（如 2499）
2. 等待成交（最多等待，持续检查价格）
3. 成交后 WebSocket 触发 → `waiting_for_lighter_fill = True`
4. Lighter 市价卖出 0.03 ETH 对冲

**平仓时机：** 无，这是建立对冲仓位

**仓位状态：**
```
Extended: +0.03 ETH
Lighter:  -0.03 ETH
净仓位:    0 ETH (风险中性)
```

---

#### STEP 2：开空单（卖出）- **真正的平仓发生在这里**

**流程：**
1. Extended 挂卖单 @ best_ask（如 2502）
2. 等待成交（最多等待，持续检查价格）
3. 成交后 → Extended 仓位归零
4. Lighter 市价买入 0.03 ETH → Lighter 仓位归零

**平仓时机：** STEP 2 的 Extended 卖单成交时

**仓位变化：**
```
STEP 1 后:
  Extended: +0.03 ETH
  Lighter:  -0.03 ETH

STEP 2 Extended 成交后:
  Extended: +0.03 - 0.03 = 0 ETH ✅
  Lighter:  -0.03 ETH (待对冲)

STEP 2 Lighter 对冲后:
  Extended: 0 ETH
  Lighter:  -0.03 + 0.03 = 0 ETH ✅
净仓位:    0 ETH ✅
```

**关键点：**
- Extended 的卖单 = **平掉 STEP 1 的多头仓位**
- Lighter 的买单 = **平掉 STEP 1 的空头仓位**
- **不是市价平仓，而是继续挂 POST-ONLY 单等待成交**

---

#### STEP 3：修正残余仓位

**代码位置：** `hedge/hedge_mode_ext.py:1190-1223`

```python
if self.extended_position == 0:
    continue  # 仓位已平，跳过
elif self.extended_position > 0:
    side = 'sell'  # 多头残余 → 卖出平仓
else:
    side = 'buy'   # 空头残余 → 买入平仓

await self.place_extended_post_only_order(side, abs(self.extended_position))
```

**平仓时机：** 当 `extended_position != 0` 时触发

**局限性：** ⚠️ 和 Backpack 相同，**只检查 Extended 仓位，不检查 Lighter 仓位**

---

### 关键问题：Extended 什么时候市价平仓？

**答案：Extended 永远不会主动市价平仓！**

Extended 始终使用 **POST-ONLY Maker 单**，即使在 STEP 3 修正仓位时也是如此。这意味着：

✅ **优点：**
- 始终赚取 Maker 手续费返佣
- 不会因为滑点损失

❌ **缺点：**
- 如果市场流动性差，订单可能长时间不成交
- 在快速波动的市场中，订单可能被反复取消重挂
- 平仓可能延迟，增加风险敞口时间

### Extended 订单取消与重挂机制

**触发条件（代码行 668-681）：**
1. **超过 10 秒未成交**
2. **且价格不再是最优**（`should_cancel = True`）
3. **且距离上次取消超过 5 秒**（防止频繁取消）

**重挂流程：**
```python
# 1. 取消旧订单
await self.extended_client.cancel_order(order_id)

# 2. WebSocket 收到 CANCELED 状态
self.extended_order_status = 'CANCELED'

# 3. 触发重挂（代码行 647-653）
order_id, order_price = await self.place_bbo_order(side, quantity)
start_time = time.time()  # 重置计时器
```

### 实际运行示例

**正常流程：**
```bash
[STEP 1] Extended position: 0 | Lighter position: 0
[OPEN] [Extended] [buy] Placing Extended POST-ONLY order
✅ Extended order filled: BUY 0.03 @ 2500
🚀 Lighter limit order sent: sell 0.03
📊 Lighter order filled: SHORT 0.03 @ 2499.8

[STEP 2] Extended position: 0.03 | Lighter position: -0.03
[OPEN] [Extended] [sell] Placing Extended POST-ONLY order
✅ Extended order filled: SELL 0.03 @ 2502
🚀 Lighter limit order sent: buy 0.03
📊 Lighter order filled: LONG 0.03 @ 2502.2

[STEP 3] Extended position: 0 | Lighter position: 0  # ✅ 平仓完成
```

**价格波动导致取消重挂：**
```bash
[STEP 1] Extended position: 0 | Lighter position: 0
[OPEN] [Extended] [buy] Placing Extended POST-ONLY order @ 2500
# ... 10秒后，市场价涨到 2505
Canceling order 12345 due to timeout/price mismatch
Order 12345 was canceled, placing new order
[OPEN] [Extended] [buy] Placing Extended POST-ONLY order @ 2505
# ... 继续等待成交
```

### 改进建议：添加市价平仓选项

如果你希望 Extended 在某些情况下使用市价平仓（而不是一直等待 POST-ONLY 成交），可以考虑：

#### 建议 1：超时后切换为市价单

```python
async def place_extended_post_only_order(self, side: str, quantity: Decimal):
    order_id, order_price = await self.place_bbo_order(side, quantity)
    start_time = time.time()
    cancel_count = 0

    while not self.stop_flag:
        # 如果取消重挂超过 3 次，切换为市价单
        if cancel_count >= 3:
            self.logger.warning("⚠️ POST-ONLY order canceled 3 times, switching to market order")
            await self.place_extended_market_order(side, quantity)
            break

        # ... 原有逻辑
        if self.extended_order_status in ['CANCELED', 'CANCELLED']:
            cancel_count += 1
            order_id, order_price = await self.place_bbo_order(side, quantity)
```

#### 建议 2：STEP 3 使用市价平仓

```python
# STEP 3: 修正残余仓位
if self.extended_position != 0:
    side = 'sell' if self.extended_position > 0 else 'buy'

    # 残余仓位用市价单快速平仓
    self.logger.info(f"[STEP 3] Using MARKET order to close remaining position")
    await self.place_extended_market_order(side, abs(self.extended_position))
```

#### 建议 3：增加最大等待时间

```python
# 主循环等待逻辑
start_time = time.time()
while not self.order_execution_complete and not self.stop_flag:
    if self.waiting_for_lighter_fill:
        await self.place_lighter_market_order(...)
        break

    # 如果超过 180 秒仍未完成，强制市价平仓
    if time.time() - start_time > 180:
        self.logger.error("❌ Timeout - forcing market order closure")
        if self.extended_position != 0:
            side = 'sell' if self.extended_position > 0 else 'buy'
            await self.place_extended_market_order(side, abs(self.extended_position))
        break
```

### 总结

**Extended 的平仓逻辑：**
1. ✅ **永远使用 POST-ONLY Maker 单**，不会主动市价平仓
2. ✅ **STEP 2 是真正的平仓时机**（平掉 STEP 1 的仓位）
3. ✅ **有智能价格检查**，会取消并重挂不再是最优价的订单
4. ❌ **不是等待 XX 时间后市价卖出**，而是一直等待 Maker 单成交
5. ⚠️ **风险**：在低流动性市场，可能长时间无法成交，导致仓位敞口

**与 Backpack 的核心差异：**
- Extended 有价格检查机制（10秒后检查是否还是最优价）
- Backpack 没有价格检查，只是简单的 10 秒超时取消
- 两者都不会自动切换为市价单
