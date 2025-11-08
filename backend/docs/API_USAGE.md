# API 使用指南

本文档介绍如何使用 FastAPI 服务来运行对冲交易机器人。

## 目录

- [快速开始](#快速开始)
- [API 端点](#api-端点)
- [使用示例](#使用示例)
- [监控与日志](#监控与日志)

---

## 快速开始

### 1. 启动 API 服务

```bash
# 使用 Docker Compose 启动
docker-compose up -d

# 检查服务状态
docker-compose ps

# 查看日志
docker-compose logs -f perp-bot
```

服务启动后，API 将在 `http://localhost:8000` 上运行。

### 2. 访问 API 文档

浏览器访问：
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

---

## API 端点

### 健康检查

**GET /**

检查 API 服务是否正常运行。

**响应示例：**
```json
{
  "status": "ok",
  "message": "Perp DEX Trading Bot API is running",
  "timestamp": "2025-01-08T10:30:00.123456"
}
```

---

### 运行单次交易 (RunBot)

**POST /runbot**

使用 `runbot.py` 执行单次买卖交易。

**请求体：**
```json
{
  "exchange": "backpack",
  "ticker": "ETH",
  "direction": "buy",
  "quantity": 0.1,
  "boost": true
}
```

**参数说明：**
- `exchange`: 交易所名称（`backpack` 或 `extended`）
- `ticker`: 交易对（如 `ETH`、`BTC`）
- `direction`: 交易方向（`buy` 或 `sell`）
- `quantity`: 交易数量
- `boost`: 是否启用加速模式（可选，默认 `false`）

**响应示例：**
```json
{
  "status": "started",
  "message": "RunBot task started: python runbot.py --exchange backpack --ticker ETH --direction buy --quantity 0.1 --boost",
  "timestamp": "2025-01-08T10:30:00.123456"
}
```

---

### 运行对冲交易 (Hedge Mode)

**POST /hedge**

使用 `hedge_mode.py` 执行对冲交易。

**请求体：**
```json
{
  "exchange": "extended",
  "ticker": "ETH",
  "size": 0.1,
  "iter": 20,
  "sleep": 5,
  "fill_timeout": 5
}
```

**参数说明：**
- `exchange`: 交易所名称（`backpack` 或 `extended`）
- `ticker`: 交易对（如 `ETH`、`BTC`）
- `size`: 每轮交易的数量
- `iter`: 循环次数
- `sleep`: 每步之间的休眠时间（秒，默认 `0`）
- `fill_timeout`: Maker 订单成交超时时间（秒，默认 `5`）

**响应示例：**
```json
{
  "status": "started",
  "message": "Hedge mode task started: python hedge_mode.py --exchange extended --ticker ETH --size 0.1 --iter 20 --sleep 5 --fill-timeout 5",
  "timestamp": "2025-01-08T10:30:00.123456"
}
```

---

### 查看运行中的进程

**GET /processes**

查看当前所有正在运行的交易机器人进程。

**响应示例：**
```json
{
  "hedge_extended_ETH_2025-01-08T10:30:00.123456": {
    "pid": 12345,
    "command": "python hedge_mode.py --exchange extended --ticker ETH --size 0.1 --iter 20",
    "start_time": "2025-01-08T10:30:00.123456"
  }
}
```

---

### 查看日志

**GET /logs/{exchange}/{ticker}**

查看指定交易所和交易对的最近日志。

**路径参数：**
- `exchange`: 交易所名称（`backpack` 或 `extended`）
- `ticker`: 交易对（如 `ETH`、`BTC`）

**查询参数：**
- `lines`: 返回的行数（默认 `100`）

**示例请求：**
```bash
GET /logs/extended/ETH?lines=100
```

**响应示例：**
```json
{
  "exchange": "extended",
  "ticker": "ETH",
  "log_file": "logs/extended_ETH_hedge_mode_log.txt",
  "lines_returned": 100,
  "logs": "[2025-01-08 10:30:00] [STEP 1] Extended position: 0 | Lighter position: 0\n..."
}
```

---

### 查看交易记录

**GET /trades/{exchange}/{ticker}**

查看指定交易所和交易对的交易历史（CSV 格式）。

**路径参数：**
- `exchange`: 交易所名称（`backpack` 或 `extended`）
- `ticker`: 交易对（如 `ETH`、`BTC`）

**示例请求：**
```bash
GET /trades/extended/ETH
```

**响应示例：**
```json
{
  "exchange": "extended",
  "ticker": "ETH",
  "csv_file": "logs/extended_ETH_hedge_mode_trades.csv",
  "content": "exchange,timestamp,side,price,quantity\nExtended,2025-01-08T10:30:00,buy,3231.5,0.03\n..."
}
```

---

## 使用示例

### 示例 1：运行 Backpack 买入交易

```bash
curl -X POST "http://localhost:8000/runbot" \
  -H "Content-Type: application/json" \
  -d '{
    "exchange": "backpack",
    "ticker": "ETH",
    "direction": "buy",
    "quantity": 0.1,
    "boost": true
  }'
```

### 示例 2：运行 Extended 对冲交易

```bash
curl -X POST "http://localhost:8000/hedge" \
  -H "Content-Type: application/json" \
  -d '{
    "exchange": "extended",
    "ticker": "ETH",
    "size": 0.1,
    "iter": 20,
    "sleep": 5,
    "fill_timeout": 5
  }'
```

### 示例 3：查看运行中的进程

```bash
curl http://localhost:8000/processes
```

### 示例 4：查看 Extended ETH 最近 50 行日志

```bash
curl "http://localhost:8000/logs/extended/ETH?lines=50"
```

### 示例 5：下载交易记录

```bash
curl "http://localhost:8000/trades/extended/ETH" > trades.json
```

---

## 使用 Python requests

```python
import requests

# API 基础地址
BASE_URL = "http://localhost:8000"

# 1. 健康检查
response = requests.get(f"{BASE_URL}/")
print(response.json())

# 2. 运行对冲交易
hedge_request = {
    "exchange": "extended",
    "ticker": "ETH",
    "size": 0.1,
    "iter": 20,
    "sleep": 5,
    "fill_timeout": 5
}
response = requests.post(f"{BASE_URL}/hedge", json=hedge_request)
print(response.json())

# 3. 查看进程
response = requests.get(f"{BASE_URL}/processes")
print(response.json())

# 4. 查看日志
response = requests.get(f"{BASE_URL}/logs/extended/ETH", params={"lines": 100})
print(response.json())
```

---

## 使用 JavaScript/TypeScript

```typescript
// 使用 fetch API
const BASE_URL = "http://localhost:8000";

// 运行对冲交易
async function runHedgeMode() {
  const response = await fetch(`${BASE_URL}/hedge`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      exchange: "extended",
      ticker: "ETH",
      size: 0.1,
      iter: 20,
      sleep: 5,
      fill_timeout: 5,
    }),
  });

  const data = await response.json();
  console.log(data);
}

// 查看日志
async function getLogs() {
  const response = await fetch(`${BASE_URL}/logs/extended/ETH?lines=100`);
  const data = await response.json();
  console.log(data.logs);
}
```

---

## 监控与日志

### 容器日志

查看 Docker 容器输出日志：

```bash
# 实时查看
docker-compose logs -f perp-bot

# 查看最近 100 行
docker-compose logs --tail=100 perp-bot

# 查看特定时间段
docker-compose logs --since 2h perp-bot
```

### 应用日志

应用日志存储在 `./logs` 目录：

```bash
# 查看 Extended ETH 对冲日志
tail -f logs/extended_ETH_hedge_mode_log.txt

# 查看交易记录
cat logs/extended_ETH_hedge_mode_trades.csv
```

### API 访问日志

uvicorn 会输出所有 HTTP 请求日志，可通过 Docker 日志查看：

```bash
docker-compose logs -f perp-bot | grep "GET\|POST"
```

---

## 故障排查

### 问题 1：API 无法访问

**检查容器是否运行：**
```bash
docker-compose ps
```

**检查端口映射：**
```bash
docker-compose port perp-bot 8000
```

**检查防火墙：**
```bash
# macOS
sudo pfctl -s rules

# Linux
sudo ufw status
```

### 问题 2：任务启动失败

**查看详细错误日志：**
```bash
docker-compose logs --tail=200 perp-bot
```

**检查环境变量：**
```bash
docker-compose exec perp-bot env | grep -E 'EXTENDED|LIGHTER|BACKPACK'
```

### 问题 3：健康检查失败

**手动测试健康检查：**
```bash
curl http://localhost:8000/
```

**进入容器调试：**
```bash
docker-compose exec perp-bot /bin/bash
python -c "import requests; print(requests.get('http://localhost:8000/').json())"
```

---

## 生产环境建议

### 1. 添加认证

在生产环境中，建议添加 API Key 认证：

```python
from fastapi import Security, HTTPException
from fastapi.security import APIKeyHeader

API_KEY = "your-secret-api-key"
api_key_header = APIKeyHeader(name="X-API-Key")

def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return api_key

# 在端点中使用
@app.post("/hedge", dependencies=[Depends(verify_api_key)])
async def run_hedge_mode(request: HedgeModeRequest):
    ...
```

### 2. 使用 Nginx 反向代理

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 3. 启用 HTTPS

使用 Let's Encrypt 获取免费 SSL 证书：

```bash
sudo apt-get install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

### 4. 限流

使用 slowapi 限制 API 请求频率：

```bash
pip install slowapi
```

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.post("/hedge")
@limiter.limit("5/minute")
async def run_hedge_mode(request: Request, ...):
    ...
```

---

## 参考资料

- [FastAPI 官方文档](https://fastapi.tiangolo.com/)
- [Uvicorn 文档](https://www.uvicorn.org/)
- [Docker Compose 文档](https://docs.docker.com/compose/)
- [项目 Docker 部署指南](./DOCKER_DEPLOYMENT.md)
