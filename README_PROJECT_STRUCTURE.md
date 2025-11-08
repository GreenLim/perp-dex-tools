# Perp DEX Tools - 项目结构说明

本项目已重构为前后端分离架构，分别位于独立的目录中。

## 目录结构

```
perp-dex-tools/
├── backend/              # 后端 Python 服务
│   ├── exchanges/        # 交易所集成
│   ├── hedge/           # 对冲模式
│   ├── helpers/         # 工具函数
│   ├── bot/             # 机器人相关
│   ├── tests/           # 测试文件
│   ├── api_server.py    # FastAPI 服务器
│   ├── trading_bot.py   # 交易机器人主程序
│   ├── hedge_mode.py    # 对冲模式主程序
│   ├── requirements.txt # Python 依赖
│   ├── Dockerfile       # 后端 Docker 配置
│   ├── docker-compose.yml
│   └── .env             # 后端环境变量
│
├── frontend/            # 前端 Next.js 应用
│   ├── app/            # Next.js App Router
│   ├── components/     # React 组件
│   ├── lib/            # 工具库
│   ├── public/         # 静态资源
│   ├── package.json    # Node.js 依赖
│   ├── Dockerfile      # 前端 Docker 配置
│   ├── docker-compose.yml
│   └── next.config.ts  # Next.js 配置
│
├── docs/               # 文档
├── .gitignore
└── README.md
```

## 技术栈

### 后端
- **Python 3.10**
- **FastAPI** - 高性能 Web 框架
- **Uvicorn** - ASGI 服务器
- **CCXT** - 加密货币交易库

### 前端
- **TypeScript**
- **Next.js 16** - React 框架 (App Router)
- **Tailwind CSS** - CSS 框架
- **Shadcn/ui** - UI 组件库

## 快速开始

### 后端服务

#### 开发环境
```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn api_server:app --reload
```

后端服务将运行在 http://localhost:8000

#### Docker 部署
```bash
cd backend
cp .env.example .env
# 编辑 .env 文件配置环境变量
docker-compose up -d
```

### 前端应用

#### 开发环境
```bash
cd frontend
npm install
npm run dev
```

前端应用将运行在 http://localhost:3000

#### Docker 部署
```bash
cd frontend
cp .env.example .env
# 编辑 .env 文件配置 API 地址
docker-compose up -d
```

## API 集成

前端通过环境变量 `NEXT_PUBLIC_API_URL` 连接后端 API。

默认配置：
- 开发环境：`http://localhost:8000`
- 生产环境：根据实际部署配置

## Docker 部署说明

前后端使用独立的 Docker Compose 配置，可以单独部署和管理。

### 启动所有服务
```bash
# 启动后端
cd backend && docker-compose up -d

# 启动前端
cd ../frontend && docker-compose up -d
```

### 停止所有服务
```bash
cd backend && docker-compose down
cd ../frontend && docker-compose down
```

## 环境变量配置

### 后端 (.env)
参考 `backend/.env.example` 配置交易所 API 密钥等信息。

### 前端 (.env)
```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## 开发指南

### 添加新的 UI 组件
使用 Shadcn/ui CLI：
```bash
cd frontend
npx shadcn@latest add button
npx shadcn@latest add card
# ... 其他组件
```

### 后端 API 文档
启动后端服务后访问：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## 项目维护

- 原有 README 文档保持不变，位于根目录
- 后端相关文档位于 `docs/` 目录
- 前端组件文档可在 Storybook 中查看（待配置）

## 版本历史

- **2025-11-08**: 重构为前后端分离架构
  - 后端代码迁移至 `backend/` 目录
  - 新增 Next.js + Shadcn/ui 前端项目
  - 独立的 Docker 配置
