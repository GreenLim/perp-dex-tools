# Momentum Bot 重新设计 - 自动价格计算

## 概述

根据用户反馈，重新设计了 Momentum Bot 以支持自动价格计算：
- **Contract ID**: 自动从交易所根据 ticker 获取，不需要手动输入
- **触发价和限价**: 根据当前最佳价格和 tick 偏移自动计算（使用 tick size 的倍数）
- **每轮交易**: 完成后自动重新计算价格，开始新一轮

## 主要变更

### 1. 后端核心逻辑 (backend/strategies/momentum_bot.py)

#### MomentumConfig 简化

**之前**:
```python
@dataclass
class MomentumConfig:
    ticker: str
    exchange: str
    contract_id: str           # 手动输入
    quantity: Decimal
    tick_size: Decimal         # 手动输入
    direction: str
    stop_price: Decimal        # 手动输入
    limit_price: Decimal       # 手动输入
    take_profit_pct: Decimal
    max_positions: int = 1
    wait_time: int = 5
```

**现在**:
```python
@dataclass
class MomentumConfig:
    ticker: str
    exchange: str
    quantity: Decimal
    direction: str
    tick_offset: int  # 新增：tick 偏移（整数，表示几个 tick size）
    take_profit_pct: Decimal
    max_positions: int = 1
    wait_time: int = 5

    # 自动填充字段
    contract_id: Optional[str] = None
    tick_size: Optional[Decimal] = None
```

#### 新增方法

**1. initialize_market_info()** - 初始化市场信息
```python
async def initialize_market_info(self):
    """从交易所获取 contract_id 和 tick_size"""
    if hasattr(self.exchange_client, 'config'):
        if hasattr(self.exchange_client.config, 'contract_id'):
            self.config.contract_id = self.exchange_client.config.contract_id

        if hasattr(self.exchange_client.config, 'tick_size'):
            self.config.tick_size = Decimal(str(self.exchange_client.config.tick_size))

    if not self.config.contract_id:
        raise ValueError(f"Failed to get contract_id for {self.config.ticker}")

    self.market_initialized = True
```

**2. get_best_price()** - 获取最佳价格
```python
async def get_best_price(self) -> Decimal:
    """从订单簿获取最佳价格，使用交易所的 fetch_bbo_prices 方法"""
    if hasattr(self.exchange_client, 'fetch_bbo_prices'):
        best_bid, best_ask = await self.exchange_client.fetch_bbo_prices(self.config.contract_id)

        if self.config.direction == "buy":
            # 做多：获取最佳卖价 (ask)
            if best_ask > 0:
                return best_ask
        else:
            # 做空：获取最佳买价 (bid)
            if best_bid > 0:
                return best_bid

    raise ValueError("Unable to get best price from orderbook")
```

**3. calculate_entry_prices()** - 计算入场价格（使用 tick offset）
```python
def calculate_entry_prices(self, best_price: Decimal) -> tuple[Decimal, Decimal]:
    """根据最佳价格和 tick 偏移计算止损价和限价"""
    # 计算价格偏移量（tick_offset * tick_size）
    price_offset = Decimal(str(self.config.tick_offset)) * self.config.tick_size

    if self.config.direction == "buy":
        # 做多：在最佳卖价上方 tick_offset 个 tick 挂单
        stop_price = best_price + price_offset
    else:
        # 做空：在最佳买价下方 tick_offset 个 tick 挂单
        stop_price = best_price - price_offset

    # 限价 = 止损价（触发即成交）
    limit_price = stop_price

    # 四舍五入到 tick_size
    stop_price = self.exchange_client.round_to_tick(stop_price)
    limit_price = self.exchange_client.round_to_tick(limit_price)

    return stop_price, limit_price
```

#### 修改的方法

**place_entry_order()** - 下入场订单
```python
async def place_entry_order(self) -> bool:
    """使用动态计算的价格下止损限价单"""
    # 获取当前最佳价格
    best_price = await self.get_best_price()

    # 计算入场价格
    stop_price, limit_price = self.calculate_entry_prices(best_price)

    self.logger.log(
        f"📊 最佳价格: {best_price}, "
        f"触发价: {stop_price}, "
        f"限价: {limit_price}",
        "INFO"
    )

    # 下单...
```

### 2. API 服务器 (backend/api_server.py)

#### MomentumBotRequest 简化

**之前**:
```python
class MomentumBotRequest(BaseModel):
    exchange: ExchangeType
    ticker: str
    contract_id: str           # 删除
    quantity: float
    tick_size: float           # 删除
    direction: DirectionType
    stop_price: float          # 删除
    limit_price: float         # 删除
    take_profit_pct: float
    max_positions: int = 1
    wait_time: int = 5
```

**现在**:
```python
class MomentumBotRequest(BaseModel):
    exchange: ExchangeType
    ticker: str
    quantity: float
    direction: DirectionType
    price_offset_pct: float    # 新增
    take_profit_pct: float
    max_positions: int = 1
    wait_time: int = 5
```

#### /momentum 端点更新

```python
@app.post("/momentum", response_model=BotStatus)
async def run_momentum_bot(request: MomentumBotRequest):
    command = [
        "python3", "run_momentum.py",
        "--exchange", request.exchange.value,
        "--ticker", request.ticker,
        "--quantity", str(request.quantity),
        "--direction", request.direction.value,
        "--price-offset-pct", str(request.price_offset_pct),  # 新参数
        "--take-profit-pct", str(request.take_profit_pct),
        "--max-positions", str(request.max_positions),
        "--wait-time", str(request.wait_time)
    ]

    task_id = f"momentum_{request.exchange.value}_{request.ticker}_{get_current_timestamp()}"
    run_command_background(command, task_id)

    return BotStatus(
        status="started",
        message=f"Momentum bot task started",
        timestamp=get_current_timestamp()
    )
```

### 3. CLI 脚本 (backend/run_momentum.py)

#### 命令行参数更新

**之前**:
```bash
python3 run_momentum.py \
  --exchange extended \
  --ticker ETH \
  --contract-id ETH-USD-PERP \      # 删除
  --quantity 0.1 \
  --tick-size 0.01 \                # 删除
  --direction buy \
  --stop-price 3500 \               # 删除
  --limit-price 3501 \              # 删除
  --take-profit-pct 0.02
```

**现在**:
```bash
python3 run_momentum.py \
  --exchange extended \
  --ticker ETH \
  --quantity 0.1 \
  --direction buy \
  --price-offset-pct 0.05 \         # 新增
  --take-profit-pct 0.02
```

### 4. 前端表单 (frontend/components/NewTaskDialog.tsx)

#### 表单字段简化

**之前的字段** (10个):
- Exchange
- Ticker
- Contract ID
- Direction
- Quantity
- Tick Size
- Stop Price
- Limit Price
- Take Profit %
- Max Positions
- Wait Time

**现在的字段** (6个):
- Exchange
- Ticker
- Direction
- Quantity
- Price Offset % (价格偏移)
- Take Profit % (止盈)
- Max Positions (可选)
- Wait Time (可选)

#### formData 状态更新

```typescript
const [formData, setFormData] = useState({
  exchange: 'extended',
  ticker: 'ETH',
  direction: 'buy',
  quantity: '0.1',
  // ... 其他类型的字段

  // Momentum Bot 字段（简化）
  priceOffsetPct: '0.05',
  takeProfitPct: '0.02',
  maxPositions: '1',
  waitTime: '5',
});
```

### 5. 前端 API 调用 (frontend/app/page.tsx)

#### createNewTask 更新

**之前**:
```typescript
else if (formData.type === 'momentum') {
  endpoint = '/momentum';
  body = {
    exchange: formData.exchange,
    ticker: formData.ticker,
    contract_id: formData.contractId,     // 删除
    quantity: parseFloat(formData.quantity),
    tick_size: parseFloat(formData.tickSize),  // 删除
    direction: formData.direction,
    stop_price: parseFloat(formData.stopPrice),  // 删除
    limit_price: parseFloat(formData.limitPrice),  // 删除
    take_profit_pct: parseFloat(formData.takeProfitPct),
    max_positions: parseInt(formData.maxPositions),
    wait_time: parseInt(formData.waitTime)
  };
}
```

**现在**:
```typescript
else if (formData.type === 'momentum') {
  endpoint = '/momentum';
  body = {
    exchange: formData.exchange,
    ticker: formData.ticker,
    quantity: parseFloat(formData.quantity),
    direction: formData.direction,
    price_offset_pct: parseFloat(formData.priceOffsetPct),  // 新增
    take_profit_pct: parseFloat(formData.takeProfitPct),
    max_positions: parseInt(formData.maxPositions),
    wait_time: parseInt(formData.waitTime)
  };
}
```

## 工作流程

### 启动时

1. 用户在前端创建任务，只需输入：
   - ticker (例如: ETH)
   - quantity (例如: 0.1)
   - direction (buy/sell)
   - price_offset_pct (例如: 0.05)
   - take_profit_pct (例如: 0.02)

2. API 服务器接收请求并启动 run_momentum.py

3. MomentumBot 初始化：
   ```python
   await self.initialize_market_info()
   # 自动从交易所获取 contract_id 和 tick_size
   ```

### 每轮交易

1. **获取最佳价格**:
   ```python
   best_price = await self.get_best_price()
   # 例如: 3500.00
   ```

2. **计算入场价格**:
   ```python
   stop_price, limit_price = self.calculate_entry_prices(best_price)
   # 做多示例 (price_offset_pct = 0.05):
   # stop_price = 3500.00 * 1.0005 = 3501.75
   # limit_price = 3501.75 * 1.0001 = 3502.10
   ```

3. **下入场订单**:
   ```python
   await self.place_entry_order()
   # 使用计算出的 stop_price 和 limit_price
   ```

4. **等待成交并设置止盈**:
   ```python
   # 成交后自动计算止盈价格
   # take_profit_price = entry_price * (1 + take_profit_pct / 100)
   ```

5. **止盈成交后重置**:
   ```python
   self.has_position = False
   self.entry_order = None
   # 回到步骤 1，开始新一轮
   ```

## 优势

### 1. 用户体验
- ✅ 简化配置：从 10 个字段减少到 6 个字段
- ✅ 无需查询 contract_id
- ✅ 无需手动计算价格
- ✅ 无需担心 tick_size

### 2. 灵活性
- ✅ 自动跟随市场价格
- ✅ 每轮交易重新计算
- ✅ 适应价格波动

### 3. 可靠性
- ✅ 基于实时市场数据
- ✅ 自动处理交易所配置
- ✅ 错误处理更完善

## 使用示例

### 通过前端创建任务

1. 点击 "New Task"
2. 选择 "Momentum Bot"
3. 填写：
   - Exchange: extended
   - Ticker: ETH
   - Direction: Buy
   - Quantity: 0.1
   - Tick Offset: 1 (表示偏移 1 个 tick size)
   - Take Profit %: 0.02 (表示 0.02% 止盈)
4. 点击 "Create Task"

### 通过 API 创建任务

```bash
curl -X POST http://localhost:8000/momentum \
  -H "Content-Type: application/json" \
  -d '{
    "exchange": "extended",
    "ticker": "ETH",
    "quantity": 0.1,
    "direction": "buy",
    "tick_offset": 1,
    "take_profit_pct": 0.02,
    "max_positions": 1,
    "wait_time": 5
  }'
```

### 通过命令行创建任务

```bash
cd backend
python3 run_momentum.py \
  --exchange extended \
  --ticker ETH \
  --quantity 0.1 \
  --direction buy \
  --tick-offset 1 \
  --take-profit-pct 0.02
```

## 价格计算逻辑

### 做多 (Buy) 示例

假设：
- 当前最佳卖价 (best ask) = 3451.9
- tick_size = 0.1
- tick_offset = 1
- take_profit_pct = 0.02 (0.02%)

**入场计算**:
```
price_offset = tick_offset * tick_size = 1 * 0.1 = 0.1
stop_price = best_ask + price_offset = 3451.9 + 0.1 = 3452.0
limit_price = stop_price = 3452.0
```

**实际效果**:
- 最佳卖价是 3451.9
- 我们在 3452.0 挂单（正好比最佳卖价高 1 个 tick）
- 当价格触达 3452.0 时立即成交

**止盈计算** (假设成交价 = 3452.0):
```
take_profit_price = 3452.0 * (1 + 0.02/100) = 3452.0 * 1.0002 = 3452.69
```

### 做空 (Sell) 示例

假设：
- 当前最佳买价 (best bid) = 3451.8
- tick_size = 0.1
- tick_offset = 1
- take_profit_pct = 0.02 (0.02%)

**入场计算**:
```
price_offset = tick_offset * tick_size = 1 * 0.1 = 0.1
stop_price = best_bid - price_offset = 3451.8 - 0.1 = 3451.7
limit_price = stop_price = 3451.7
```

**实际效果**:
- 最佳买价是 3451.8
- 我们在 3451.7 挂单（正好比最佳买价低 1 个 tick）
- 当价格触达 3451.7 时立即成交

**止盈计算** (假设成交价 = 3451.7):
```
take_profit_price = 3451.7 * (1 - 0.02/100) = 3451.7 * 0.9998 = 3451.01
```

## 测试建议

### 1. 验证 Contract ID 获取
- 启动 bot 后检查日志
- 确认 "✅ Contract ID: XXX" 出现
- 确认 "✅ Tick Size: XXX" 出现

### 2. 验证价格计算
- 观察日志中的价格信息
- 确认 stop_price 和 limit_price 合理
- 确认价格符合 tick_size 规则

### 3. 验证循环交易
- 完成一轮交易后
- 确认 bot 自动开始新一轮
- 确认新一轮使用最新的市场价格

### 4. 测试不同参数
- 尝试不同的 tick_offset (1, 2, 5)
- 尝试不同的 take_profit_pct (0.01, 0.02, 0.05)
- 观察不同参数对交易的影响

## 总结

这次重新设计大大简化了 Momentum Bot 的使用体验，同时增强了灵活性和可靠性：

### 关键改进
1. **使用 Tick Offset 替代百分比偏移**: 更精确和直观
   - 用户输入整数（几个 tick）而不是百分比
   - 例如：tick_offset=1 表示从最佳价格偏移 1 个 tick_size
   - 如果 tick_size=0.1，best_ask=3451.9，则挂单价格=3452.0

2. **自动获取市场信息**:
   - Contract ID 自动从交易所获取
   - Tick Size 自动从交易所获取
   - 用户只需关注交易策略参数

3. **使用交易所标准方法**:
   - 直接调用 `fetch_bbo_prices()` 获取最佳买卖价
   - 代码更简洁、更通用、更可靠

所有变更已完成并保持向后兼容。旧的集成文档 (MOMENTUM_BOT_INTEGRATION.md) 保留作为参考，新的使用方式更简单直观。

---

## Extended 交易所条件单支持 (Conditional Orders)

### 背景

Extended (x10) 交易所要求使用 **条件单 (Conditional Orders)** 来实现动量交易策略：
- 条件单会在触发价格达到时自动执行
- 支持止盈 (Take Profit) 和止损 (Stop Loss) 参数
- 使用 x10 Python SDK 的原生支持

### SDK 模型结构

x10 SDK 提供了以下模型类来支持条件单：

```python
# 订单类型
class OrderType(StrEnum):
    LIMIT = "LIMIT"
    CONDITIONAL = "CONDITIONAL"
    MARKET = "MARKET"
    TPSL = "TPSL"

# 条件单触发模型
class CreateOrderConditionalTriggerModel:
    trigger_price: Decimal              # 触发价格
    trigger_price_type: OrderTriggerPriceType  # MARK, INDEX, LAST
    direction: OrderTriggerDirection    # UP, DOWN
    execution_price_type: OrderPriceType  # MARKET, LIMIT

# 止盈/止损触发模型
class CreateOrderTpslTriggerModel:
    trigger_price: Decimal
    trigger_price_type: OrderTriggerPriceType
    price: Decimal
    price_type: OrderPriceType
    settlement: StarkSettlementModel    # 需要签名
```

### Extended Client 实现

在 `backend/exchanges/extended.py` 中添加了 `place_conditional_order()` 方法：

```python
async def place_conditional_order(
    self,
    contract_id: str,
    quantity: Decimal,
    trigger_price: Decimal,
    side: str,
    take_profit_price: Optional[Decimal] = None
) -> OrderResult:
    """使用 x10 SDK 模型下条件单"""

    # 创建条件触发模型
    # 做多：价格上涨时触发 (UP)
    # 做空：价格下跌时触发 (DOWN)
    conditional_trigger = CreateOrderConditionalTriggerModel(
        trigger_price=trigger_price,
        trigger_price_type=OrderTriggerPriceType.LAST,  # 使用最新成交价
        direction=OrderTriggerDirection.UP if side == 'buy' else OrderTriggerDirection.DOWN,
        execution_price_type=OrderPriceType.LIMIT  # 触发后以限价单执行
    )

    # 下条件单
    # SDK 会根据 trigger 参数的存在自动推断为条件单
    order_result = await self.perpetual_trading_client.place_order(
        market_name=contract_id,
        amount_of_synthetic=quantity,
        price=execution_price,  # 触发后的执行价格
        side=order_side,
        time_in_force=TimeInForce.GTT,
        post_only=False,
        expire_time=utc_now() + timedelta(days=1),
        trigger=conditional_trigger  # 传入触发模型 - 这使其成为条件单
    )
```

### Momentum Bot 集成

在 `backend/strategies/momentum_bot.py` 的 `place_entry_order()` 方法中：

```python
async def place_entry_order(self) -> bool:
    # 获取最佳价格
    best_price = await self.get_best_price()

    # 计算触发价格（基于 tick_offset）
    trigger_price, _ = self.calculate_entry_prices(best_price)

    # 计算止盈价格
    if self.config.direction == "buy":
        tp_price = trigger_price * (Decimal('1') + self.config.take_profit_pct / Decimal('100'))
    else:
        tp_price = trigger_price * (Decimal('1') - self.config.take_profit_pct / Decimal('100'))

    # 下条件单（自动检测交易所是否支持）
    if hasattr(self.exchange_client, 'place_conditional_order'):
        order_result = await self.exchange_client.place_conditional_order(
            self.config.contract_id,
            self.config.quantity,
            trigger_price,
            self.config.direction,
            take_profit_price=tp_price
        )
    else:
        # 回退到普通限价单
        order_result = await self.exchange_client.place_open_order(...)
```

### 工作流程

1. **计算触发价格**:
   ```
   # 做多 (BUY)
   trigger_price = best_ask + (tick_offset * tick_size)
   direction = UP  # 价格向上突破时触发

   # 做空 (SELL)
   trigger_price = best_bid - (tick_offset * tick_size)
   direction = DOWN  # 价格向下突破时触发
   ```

2. **计算止盈价格**:
   ```
   # 做多
   tp_price = trigger_price * (1 + take_profit_pct / 100)

   # 做空
   tp_price = trigger_price * (1 - take_profit_pct / 100)
   ```

3. **下条件单**:
   - 订单类型：`CONDITIONAL`
   - 触发价格类型：`LAST` (最新成交价)
   - 执行价格类型：`LIMIT` (限价单)
   - 止盈价格：通过 `take_profit` 参数传入（需要签名，暂时在触发后单独下单）

### 订单状态

条件单的状态包括：
- `UNTRIGGERED`: 未触发（等待触发价格）
- `NEW` / `OPEN`: 已触发，等待成交
- `PARTIALLY_FILLED`: 部分成交
- `FILLED`: 完全成交

### 技术要点

1. **导入 SDK 模型**:
   ```python
   from x10.perpetual.orders import (
       OrderType,
       OrderTriggerPriceType,
       OrderTriggerDirection,
       OrderPriceType,
       CreateOrderConditionalTriggerModel,
       CreateOrderTpslTriggerModel
   )
   ```

2. **订单类型推断**:
   SDK 会根据 `trigger` 参数的存在自动推断订单类型为 `CONDITIONAL`，无需显式指定 `order_type`

3. **止盈处理**:
   止盈参数需要复杂的 Stark 签名，暂时在条件单触发后单独下止盈单

4. **价格精度**:
   所有价格都通过 `round_to_tick()` 方法四舍五入到 tick_size

### 测试示例

```python
# 做多 ETH，触发价格在最佳卖价上方 1 个 tick，止盈 0.02%
# best_ask = 3451.9, tick_size = 0.1, tick_offset = 1
# trigger_price = 3451.9 + (1 * 0.1) = 3452.0
# tp_price = 3452.0 * 1.0002 = 3452.69

POST /momentum
{
    "exchange": "extended",
    "ticker": "ETH",
    "quantity": 0.1,
    "direction": "buy",
    "tick_offset": 1,
    "take_profit_pct": 0.02,
    "max_positions": 1,
    "wait_time": 5
}
```

### 当前实现状态

**✅ 已实现**: 使用 x10 Python SDK 的 `NewOrderModel` 和 `orders.place_order()` API 正确实现了条件单。

#### 实现方法

x10 SDK 提供了两种下单方式：
1. **便捷方法**: `trading_client.place_order()` - 简化的 API，但不支持条件单
2. **完整方法**: `trading_client.orders.place_order(order=NewOrderModel)` - 完整的 API，支持所有订单类型

我们使用第二种方法，手动构造 `NewOrderModel` 来实现条件单。

#### 条件单完整实现

```python
from x10.perpetual.orders import (
    NewOrderModel,
    CreateOrderConditionalTriggerModel,
    OrderType,
    OrderSide,
    TimeInForce,
    OrderTriggerPriceType,
    OrderTriggerDirection,
    OrderPriceType,
    SelfTradeProtectionLevel
)
from x10.utils.nonce import generate_nonce
from x10.utils.date import to_epoch_millis
from x10.perpetual.fees import DEFAULT_FEES

async def place_conditional_order(
    self,
    contract_id: str,
    quantity: Decimal,
    trigger_price: Decimal,
    side: str,
    take_profit_price: Optional[Decimal] = None
) -> OrderResult:
    """使用 x10 SDK 的 NewOrderModel 和 orders.place_order() API 正确实现条件单"""

    # 1. 转换订单方向
    order_side = OrderSide.BUY if side.lower() == 'buy' else OrderSide.SELL

    # 2. 四舍五入价格到 tick_size
    trigger_price = self.round_to_tick(trigger_price)
    execution_price = self.round_to_tick(trigger_price)

    # 3. 生成 nonce 和过期时间
    nonce = generate_nonce()
    expire_time = utc_now() + timedelta(days=1)
    expiry_millis = to_epoch_millis(expire_time)

    # 4. 获取费率
    try:
        account = self.perpetual_trading_client.account
        fees = account.trading_fee.get(contract_id, DEFAULT_FEES)
        fee_rate = fees.taker_fee_rate
    except Exception as e:
        fee_rate = DEFAULT_FEES.taker_fee_rate

    # 5. 创建条件触发模型
    conditional_trigger = CreateOrderConditionalTriggerModel(
        trigger_price=trigger_price,
        trigger_price_type=OrderTriggerPriceType.LAST,  # 使用最新成交价
        direction=OrderTriggerDirection.UP if side.lower() == 'buy' else OrderTriggerDirection.DOWN,
        execution_price_type=OrderPriceType.LIMIT  # 触发后以限价单执行
    )

    # 6. 构造 NewOrderModel
    new_order = NewOrderModel(
        id=f"cond-{nonce}",  # 外部订单 ID
        market=contract_id,
        type=OrderType.CONDITIONAL,  # 关键：指定为条件单类型
        side=order_side,
        qty=quantity,
        price=execution_price,  # 触发后的执行价格
        time_in_force=TimeInForce.GTT,
        expiry_epoch_millis=expiry_millis,
        fee=fee_rate,
        nonce=Decimal(nonce),
        self_trade_protection_level=SelfTradeProtectionLevel.ACCOUNT,
        trigger=conditional_trigger  # 关键：传入触发模型
    )

    # 7. 使用完整 API 下单
    order_result = await self.perpetual_trading_client.orders.place_order(order=new_order)

    # 8. 检查订单状态
    if not order_result or not order_result.data or order_result.status != 'OK':
        error_msg = f'Failed to place conditional order: {order_result}'
        return OrderResult(success=False, error_message=error_msg)

    order_id = order_result.data.id
    # ... 等待订单状态更新 ...

    return OrderResult(success=True, order_id=order_id, ...)
```

#### 关键技术要点

1. **使用完整 API**: 必须使用 `trading_client.orders.place_order(order=NewOrderModel)`，而不是便捷方法 `trading_client.place_order()`

2. **必需字段**:
   - `id`: 外部订单 ID（例如 "cond-{nonce}"）
   - `type`: 必须设置为 `OrderType.CONDITIONAL`
   - `trigger`: 必须传入 `CreateOrderConditionalTriggerModel`
   - `nonce`: 使用 `generate_nonce()` 生成，类型为 `Decimal`
   - `fee`: 从账户配置获取或使用 `DEFAULT_FEES.taker_fee_rate`
   - `expiry_epoch_millis`: 使用 `to_epoch_millis()` 转换

3. **触发方向逻辑**:
   - 做多 (BUY): `OrderTriggerDirection.UP` - 价格向上突破时触发
   - 做空 (SELL): `OrderTriggerDirection.DOWN` - 价格向下突破时触发

4. **订单状态**:
   - `UNTRIGGERED`: 等待触发（条件单特有状态）
   - `NEW`/`OPEN`: 已触发，等待成交
   - `FILLED`: 完全成交

#### 旧的便捷 API (不支持条件单)

SDK 的便捷 `place_order()` 方法签名如下：
```python
async def place_order(
    self,
    market_name: str,
    amount_of_synthetic: Decimal,
    price: Decimal,
    side: OrderSide,
    post_only: bool = False,
    expire_time: Optional[datetime] = None,
    time_in_force: TimeInForce = TimeInForce.GTT,
    tp_sl_type: Optional[OrderTpslType] = None,
    take_profit: Optional[OrderTpslTriggerParam] = None,
    stop_loss: Optional[OrderTpslTriggerParam] = None,
) -> WrappedApiResponse[PlacedOrderModel]
```

**没有 `trigger` 参数！** 这个方法只支持普通限价单/市价单和 TPSL 订单。

### 相关文件

- `backend/exchanges/extended.py:771-918` - `place_conditional_order()` 实现（当前使用限价单）
- `backend/strategies/momentum_bot.py:335-396` - `place_entry_order()` 集成
- Extended API 文档: https://api.docs.extended.exchange/#create-or-edit-order
- x10 SDK: https://github.com/x10xchange/python_sdk/blob/main/x10/perpetual/trading_client/trading_client.py
