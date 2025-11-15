# 动量交易机器人集成完成

本文档总结了动量交易机器人（Momentum Bot）的集成过程和使用方法。

## ✅ 完成的工作

### 1. 后端 API 集成 (backend/api_server.py)

#### 添加的模型
```python
class MomentumBotRequest(BaseModel):
    exchange: ExchangeType
    ticker: str
    contract_id: str
    quantity: float
    tick_size: float
    direction: DirectionType
    stop_price: float
    limit_price: float
    take_profit_pct: float
    max_positions: int = 1
    wait_time: int = 5
```

#### 添加的 API 端点
- **POST /momentum** - 启动动量交易机器人

**请求示例**:
```json
{
  "exchange": "extended",
  "ticker": "ETH",
  "contract_id": "ETH-USD-PERP",
  "quantity": 0.1,
  "tick_size": 0.01,
  "direction": "buy",
  "stop_price": 3500.0,
  "limit_price": 3501.0,
  "take_profit_pct": 0.02,
  "max_positions": 1,
  "wait_time": 5
}
```

### 2. 启动脚本 (backend/run_momentum.py)

创建了命令行启动脚本，支持所有配置参数：

```bash
python3 run_momentum.py \
  --exchange extended \
  --ticker ETH \
  --contract-id ETH-USD-PERP \
  --quantity 0.1 \
  --tick-size 0.01 \
  --direction buy \
  --stop-price 3500 \
  --limit-price 3501 \
  --take-profit-pct 0.02 \
  --max-positions 1 \
  --wait-time 5
```

### 3. 前端集成

#### 修改的组件

**frontend/components/NewTaskDialog.tsx**
- 添加了 `momentum` 任务类型选项
- 添加了所有动量机器人配置字段：
  - Contract ID (合约 ID)
  - Direction (方向: Buy/Sell)
  - Quantity (数量)
  - Tick Size (最小价格增量)
  - Stop Price (触发价格)
  - Limit Price (限价)
  - Take Profit % (止盈百分比)
  - Max Positions (最大持仓数)
  - Wait Time (等待时间)

**frontend/app/page.tsx**
- 添加了 momentum 类型的 API 调用逻辑
- 支持解析和显示 momentum 任务

---

## 🎯 系统架构

现在系统支持 **3 种交易机器人**：

### 1. RunBot (单次交易)
- 用途：执行单次买入或卖出交易
- 特点：简单、快速、一次性
- 适用场景：测试、手动交易

### 2. Hedge Mode (对冲模式)
- 用途：在两个交易所之间进行对冲交易
- 特点：多次迭代、风险对冲
- 适用场景：Extended ↔ Lighter 对冲

### 3. Momentum Bot (动量交易) ⭐ **新增**
- 用途：跟随价格动量进行交易
- 特点：止损限价单、自动止盈
- 适用场景：避免"接飞刀"，跟随趋势交易

---

## 🚀 使用方法

### 通过前端界面

1. 点击 "New Task" 按钮
2. 在 "Task Type" 下拉菜单中选择 "Momentum Bot"
3. 填写配置参数：
   - **Exchange**: 选择交易所 (extended/lighter)
   - **Ticker**: 交易对代号 (如 ETH)
   - **Contract ID**: 合约 ID (如 ETH-USD-PERP)
   - **Direction**: 交易方向
     - Buy (Long) - 做多
     - Sell (Short) - 做空
   - **Quantity**: 每次交易数量
   - **Tick Size**: 最小价格增量
   - **Stop Price**: 触发价格（当价格达到此价时触发订单）
   - **Limit Price**: 限价（触发后的执行价格）
   - **Take Profit %**: 止盈百分比（如 0.02 表示 0.02%）
   - **Max Positions**: 最大并发持仓数
   - **Wait Time**: 周期间隔（秒）

4. 点击 "Create Task" 创建任务

### 通过 API 直接调用

```bash
curl -X POST http://localhost:8000/momentum \
  -H "Content-Type: application/json" \
  -d '{
    "exchange": "extended",
    "ticker": "ETH",
    "contract_id": "ETH-USD-PERP",
    "quantity": 0.1,
    "tick_size": 0.01,
    "direction": "buy",
    "stop_price": 3500.0,
    "limit_price": 3501.0,
    "take_profit_pct": 0.02,
    "max_positions": 1,
    "wait_time": 5
  }'
```

### 通过命令行脚本

```bash
cd backend
python3 run_momentum.py \
  --exchange extended \
  --ticker ETH \
  --contract-id ETH-USD-PERP \
  --quantity 0.1 \
  --tick-size 0.01 \
  --direction buy \
  --stop-price 3500 \
  --limit-price 3501 \
  --take-profit-pct 0.02
```

---

## 📊 交易流程

### 做多 (Buy) 示例

**配置**:
- Stop Price: 3500
- Limit Price: 3501
- Take Profit: 0.02%

**流程**:
1. 挂出止损限价单，等待价格达到 3500
2. 当价格 ≥ 3500 时，订单触发
3. 以 3501 限价买入
4. 成交后立即计算止盈价格：3501 × (1 + 0.02/100) = 3501.70
5. 挂出 3501.70 的限价卖单
6. 等待止盈单成交
7. 重置并开始新周期

### 做空 (Sell) 示例

**配置**:
- Stop Price: 3500
- Limit Price: 3499
- Take Profit: 0.02%

**流程**:
1. 挂出止损限价单，等待价格达到 3500
2. 当价格 ≤ 3500 时，订单触发
3. 以 3499 限价卖出
4. 成交后立即计算止盈价格：3499 × (1 - 0.02/100) = 3498.30
5. 挂出 3498.30 的限价买单
6. 等待止盈单成交
7. 重置并开始新周期

---

## 🔔 Telegram 通知

动量机器人会发送以下 Telegram 通知：

### 启动通知
```
🤖 Momentum Bot Started

Exchange: extended
Ticker: ETH
Direction: BUY
Quantity: 0.1
Stop Price: 3500
Limit Price: 3501
Take Profit: 0.02%
```

### 仓位开启通知
```
🚀 Momentum Bot - Position Opened

Ticker: ETH
Direction: BUY
Entry Price: 3501.00
Exit Price: 3501.70
Quantity: 0.1
Take Profit: 0.02%
```

### 仓位关闭通知
```
✅ Momentum Bot - Position Closed

Ticker: ETH
Entry: 3501.00
Quantity: 0.1
```

### 错误通知
```
❌ Momentum Bot Error

Ticker: ETH
Error: [错误信息]
```

### 关闭通知
```
🛑 Momentum Bot Shutdown

Ticker: ETH
Completed: 5/10 (50%)
Runtime: 00:15:30
Position Open: false
```

---

## 📁 文件结构

```
backend/
├── api_server.py                      # 添加了 /momentum 端点
├── run_momentum.py                    # 新增：启动脚本
└── strategies/
    ├── __init__.py                    # 导出 MomentumBot
    ├── momentum_bot.py                # 动量机器人实现
    ├── README.md                      # 中文文档
    └── STOP_LIMIT_SUPPORT.md          # 止损限价单支持文档

frontend/
├── app/
│   └── page.tsx                       # 添加了 momentum API 调用
└── components/
    └── NewTaskDialog.tsx              # 添加了 momentum 表单
```

---

## ⚙️ 配置说明

### 必需的环境变量

```bash
# Extended 交易所
EXTENDED_VAULT=your_vault_address
EXTENDED_STARK_KEY_PRIVATE=your_private_key
EXTENDED_STARK_KEY_PUBLIC=your_public_key
EXTENDED_API_KEY=your_api_key

# Lighter 交易所
API_KEY_PRIVATE_KEY=your_private_key
LIGHTER_ACCOUNT_INDEX=0
LIGHTER_API_KEY_INDEX=0

# Telegram 通知（可选）
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

---

## 🎓 关键概念

### 止损限价单 (Stop-Limit Order)

**与普通限价单的区别**:
- **普通限价单**: 立即挂单，被动等待成交（容易接飞刀）
- **止损限价单**: 等待价格达到触发价，然后才挂出限价单（跟随动量）

**优势**:
- 只在价格朝有利方向移动时才入场
- 避免在下跌趋势中买入
- 跟随动量而非对抗动量

### 动量交易策略

**核心思想**: 价格在运动中倾向于继续运动

**适用场景**:
- 趋势明确的市场
- 想要跟随强势方向
- 避免抄底/猜顶

**风险控制**:
- 立即设置止盈点
- 小利润快速锁定
- 多次交易积累收益

---

## 🔍 监控和日志

### 任务监控

前端界面会显示：
- 任务运行时间
- 当前状态
- 实时日志

### 日志文件

日志保存在：
```
logs/
└── {exchange}/
    └── {ticker}/
        ├── trade.log           # 交易日志
        └── transaction.csv     # 交易记录
```

---

## ⚠️ 注意事项

### V1 限制

当前版本的限制：
1. 每次只运行一个仓位
2. 止损限价单目前使用普通限价单（等待交易所客户端更新）
3. 仅支持做多方向（可扩展）

### 未来计划

- [ ] 真正的止损限价单实现（需要交易所客户端支持）
- [ ] 做空方向支持
- [ ] 双向交易（同时做多做空）
- [ ] 多个并发仓位
- [ ] 高级风险管理
- [ ] 追踪止损
- [ ] 动态止盈调整

---

## 🎉 总结

动量交易机器人已成功集成到系统中！

**主要成就**:
- ✅ 完整的后端 API 支持
- ✅ 命令行启动脚本
- ✅ 前端 UI 集成
- ✅ Telegram 通知
- ✅ 详细文档

**下一步**:
1. 测试动量机器人功能
2. 验证止损限价单逻辑
3. 优化参数配置
4. 根据实际使用反馈调整

现在你可以通过前端界面创建动量交易任务了！🚀
