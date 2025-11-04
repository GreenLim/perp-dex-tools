# 项目结构与实现说明

本文档面向需要快速上手 **perp-dex-tools** 的开发者，梳理代码库结构、核心流程与扩展思路。项目定位是针对多个去中心化永续合约交易所的量化刷量/做市脚本，支持自动化下单、风控以及多交易所对冲。

## 目录总览

```
.
├── trading_bot.py        # 主交易循环
├── runbot.py             # CLI 入口，解析参数并启动 TradingBot
├── exchanges/            # 各交易所适配层（EdgeX、Backpack、Paradex、Aster、Apex、GRVT、Extended、Lighter 等）
├── helpers/              # 日志与通知工具（Lark、Telegram 等）
├── hedge_mode.py         # 套保模式总入口
├── hedge/                # 针对不同交易所 + Lighter 的对冲实现
├── docs/                 # 附加文档（新增交易所指引、通知配置）
├── tests/                # 基础单测（tenacity 重试装饰器）
├── requirements*.txt     # 依赖列表（部分交易所有单独依赖）
└── env_example.txt       # 环境变量模板
```

## PlantUML 图示

- `docs/project_structure.puml`：系统模块之间的结构关系。
- `docs/trading_flow.puml`：`TradingBot` 执行主循环的流程图。
- 可在支持 PlantUML 的 IDE 或通过命令行工具渲染生成 PNG/SVG 以便查看。

## 核心业务流程

- `runbot.py` 负责解析命令行参数（交易所、Ticker、下单方向、数量、网格步长、止盈止损、Boost 模式等），读取 `.env`，构造 `TradingConfig` 并启动 `TradingBot`。
- `TradingBot`（`trading_bot.py`）是异步主循环，职责包括：
  - 通过 `ExchangeFactory` 根据 `config.exchange` 实例化具体的交易所客户端。
  - 建立私有 WebSocket 监听成交与订单状态，利用 `asyncio.Event` 在回调中同步撮合线程。
  - 在主循环中：
    1. 获取现有平仓单并缓存数量。
    2. 调用 `_log_status_periodically()` 输出持仓与活跃挂单，必要时触发 Lark/Telegram 通知并优雅关闭。
    3. `_check_price_condition()` 判断是否触发停机或暂停区间。
    4. `_calculate_wait_time()` 基于活跃单数量和配置的冷却时间设定下单节奏，并结合 `_meet_grid_step_condition()` 控制网格间距。
    5. `_place_and_monitor_open_order()` 统一落单逻辑：获取盘口价、限价下 Maker 单，等待成交或超时后撤单重挂；若成交则按照 `take_profit` 计算平仓单，或在 Boost 模式直接发送市价对手单。
  - 异常捕获后调用 `graceful_shutdown()`，统一执行断连与资源清理。

## TradingConfig 关键字段

| 字段 | 作用 |
| --- | --- |
| `ticker` | 交易标的，例如 `ETH` |
| `contract_id` | 交易所内部合约 ID，`get_contract_attributes()` 启动时回填 |
| `quantity` | 单笔下单数量（`Decimal`） |
| `take_profit` | 止盈百分比 |
| `direction` | 做多/做空，决定开仓方向与平仓方向 |
| `max_orders`／`wait_time` | 控制最大活跃平仓单数量与下单冷却时间 |
| `grid_step` | 平仓单之间的最小价差（百分比） |
| `stop_price`／`pause_price` | 触发停机或暂停的价格阈值 |
| `boost_mode` | 仅 Aster / Backpack 支持，成交后直接走市价对冲 |

## 交易所适配层（`exchanges/`）

### 通用接口

- `BaseExchangeClient` 定义了各交易所必须实现的核心接口（下单、撤单、查单、活跃订单、持仓、连接/断开、WebSocket 事件挂载等），并提供统一的 `OrderResult`、`OrderInfo` 数据结构。
- `query_retry` 装饰器封装了 `tenacity` 指数退避重试逻辑，所有网络调用可复用。
- `ExchangeFactory` 根据字符串名称动态加载对应客户端类，支持热插拔式扩展（参见 `docs/ADDING_EXCHANGES.md`）。

### 已实现的交易所

- `edgex.py`：基于官方 SDK，维持私有 WebSocket，自动重连；处理订单更新事件时过滤重复填单。
- `backpack.py`：使用自定义 WebSocket 管理器完成鉴权与订单回调，与 `bpx-py` SDK 联动；Boost 模式下提供市价单能力。
- `paradex.py`：集成官方 `paradex-py`，维护订单簿快照、指数退避重试。
- `aster.py`：调用 REST + WebSocket API，包含多处市场信息缓存、Boost 市价单。
- `grvt.py`：对接 GRVT API，处理 maker/taker 费用参数及订单重试。
- `apex.py`：基于 `apexomni`，自带 WebSocket 自动重连与签名流程。
- `lighter.py`：用于套保侧，依赖官方 SDK 与自定义 `lighter_custom_websocket`，维护盘口、订单缓存。
- `extended.py`：和 `x10-python-trading-starknet` 通信，启动多个流式任务，补齐官方接口延迟时的本地下单缓存。

> 提示：`TradingBot` 在运行过程中会额外调用 `fetch_bbo_prices()`、`get_contract_attributes()`、`get_order_price()`、`place_market_order()` 等方法，请在新增交易所时一并实现，保持与现有客户端的行为一致。

## 辅助模块（`helpers/`）

- `logger.py`：`TradingLogger` 将日志和成交流水分别写入 `logs/` 目录下的 `.log` 与 `.csv` 文件，支持通过 `ACCOUNT_NAME` 区分多账户，时区默认 `Asia/Shanghai`。
- `telegram_bot.py` 与 `lark_bot.py`：提供通知通道；`TradingBot.send_notification()` 会根据环境变量 `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、`LARK_TOKEN` 自动推送。
- `helpers/__init__.py`：导出常用 helper。

## 套保模式（`hedge_mode.py` 与 `hedge/`）

- `hedge_mode.py` 提供统一入口，按 `--exchange` 选择对应的 HedgeBot 实现（Backpack、Extended、Apex、GRVT、EdgeX 均与 Lighter 组合）。
- 各 `hedge_mode_*.py` 文件结构高度相似：同时连上线性交易所与 Lighter，订阅盘口与订单流，在主循环中：
  - 使用目标交易所挂出去市 Maker 单。
  - 监控是否在 `fill_timeout` 内成交；若成交或部分成交，则调用 Lighter 市价单对冲。
  - 记录成交日志到 `logs/`，并配合异步任务维护盘口快照。

## 配置与依赖

- 复制 `env_example.txt` 为 `.env` 并补全各交易所需要的密钥，未使用的交易所可以留空。
- `requirements.txt` 包含通用依赖，以及若干 Git 子模块（例如 EdgeX 与 Lighter 的官方/定制 SDK）。部分交易所有独立的 `*_requirements.txt`。根据使用场景选择安装：
  ```bash
  pip install -r requirements.txt
  # 或按需安装
  pip install -r para_requirements.txt
  pip install -r apex_requirements.txt
  ```
- 代码基于 `asyncio`，推荐使用 Python 3.10+。

## 运行步骤示例

1. `cp env_example.txt .env` 并填入所需环境变量。
2. `pip install -r requirements.txt`（若仅使用某些交易所，可按需安装对应 requirements）。
3. 运行刷量模式：
   ```bash
   python runbot.py \
     --exchange edgex \
     --ticker ETH \
     --quantity 0.1 \
     --take-profit 0.02 \
     --direction buy \
     --max-orders 40 \
     --wait-time 450 \
     --grid-step -100
   ```
4. 运行套保模式（示例）：
   ```bash
   python hedge_mode.py --exchange backpack --ticker BTC --size 0.002 --iter 10
   ```
5. 观察 `logs/` 下生成的日志与成交 CSV，必要时在 `Lark/Telegram` 频道确认通知。

## 扩展与二次开发建议

- 新增交易所时参考 `docs/ADDING_EXCHANGES.md`，继承 `BaseExchangeClient` 并在 `ExchangeFactory._registered_exchanges` 中登记模块路径。
- 若需要自定义风控，可在 `TradingBot` 中扩展 `_check_price_condition()`、`_calculate_wait_time()` 或补充新的 `asyncio.Event`。
- Boost 模式仅适用于具备 `place_market_order()` 的交易所，开启前确认实现完整。
- 可在 `helpers/logger.py` 内拓展日志存储（如接入数据库）或增加新的通知通道。

## 测试与调试

- `tests/test_query_retry.py` 展示了 `query_retry` 装饰器的行为，可作为集成远程 API 时的参考。
- 建议在真实资金前先通过模拟盘/低仓位验证：手动检查开平仓与活跃挂单是否匹配，必要时人工撤单后重启脚本。

## 学习路径建议

1. 先阅读 `trading_bot.py`（主循环与订单生命周期），理解事件驱动设计。
2. 按实际需求挑选一两个交易所文件研读（例如 `exchanges/backpack.py`、`exchanges/edgex.py`），掌握统一接口的具体实现。
3. 看 `hedge/` 目录下的实现，了解如何组合两个交易所进行实时对冲。
4. 根据 `docs/ADDING_EXCHANGES.md` 练习接入新的交易所或模拟接口，加深对框架的掌握。

通过上述步骤，即可在了解整体架构的基础上，定制属于自己的刷量/做市策略。
