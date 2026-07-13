# WHU MCP Server

武汉大学校园服务 MCP 服务器，提供图书馆座位、课表、成绩、考试、天气等 12 个工具，通过 SSE 协议供 Claude Code 远程调用。

## 架构

```
用户浏览器                  Claude Code (你的电脑)       武大服务器
─────────                 ──────────────────         ────────
                                 │
Streamlit :8501           SSE over HTTPS             CAS / 教务 / 图书馆
    │                         │                         ▲
    │  登录收割凭证              │  调用工具                 │
    │                         │                         │
    ▼                         ▼                         │
┌──────────────────────────────────────────────┐       │
│                  Redis                       │       │
│          whu:session:<session_id>            │       │
│     {castgc, token, hmac_key, educational}   │       │
│              TTL: 4 小时                      │       │
└──────────────────────────────────────────────┘       │
                         │                              │
                     MCP Server :8000 ──────────────────┘
                    (本目录, lib/ 自包含)
```

## 快速开始

### 前置条件

- Python 3.10+
- Redis
- Playwright

### 安装

```bash
cd mcp_http
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium
```

### 配置

```bash
# 复制环境变量模板
cp .env.example .env

# 如果 Redis 有密码，修改 .env:
# REDIS_URL=redis://:你的密码@localhost:6379
```

### 启动

```bash
python server.py --host 127.0.0.1 --port 8000
```


### 配置 Claude Code

`~/.mcp.json`:

```json
{
  "mcpServers": {
    "whu-lib": {
      "type": "sse",
      "url": "https://你的域名或IP/sse"
    }
  }
}
```

重启 Claude Code 生效。

## 工具列表

| 工具 | 需要登录 | 说明 |
|---|---|---|
| `login_password` | — | 学号+密码登录 CAS，获取所有凭证 |
| `get_seats` | ✅ | 查询分馆座位大盘 |
| `get_seat_map` | ✅ | 查询区域内座位排布 |
| `reserve_seat` | ✅ | 预约座位 (自动破解验证码) |
| `get_current_usage` | ✅ | 当前在坐座位 |
| `get_reservations` | ✅ | 预约记录 (含历史) |
| `cancel_reservation` | ✅ | 取消预约 |
| `stop_usage` | ✅ | 签退释放座位 |
| `query_schedule` | ✅ | 课表查询 |
| `query_exam_schedule` | ✅ | 考试安排 |
| `query_grades` | ✅ | 成绩查询 (含 GPA) |
| `get_weather` | ✅ | 珞珈山天气 (免登) |

## 使用流程

### 方式一：Streamlit 登录

1. 浏览器打开 Streamlit → 扫码/密码登录
2. 侧边栏复制 `session_id`
3. Claude Code 中：`session_id 是 xxx，帮我查总馆座位`

### 方式二：Claude Code 直接登录

在 Claude Code 中说：

```
用学号 2025302114221 密码 xxxxxx 登录武大，然后查工学分馆今天下午的座位
```

Claude 会自动调用 `login_password` → 拿到 session_id → 查询座位。

## 项目结构

```
mcp_http/
├── server.py              # MCP Server 入口 (12 个工具)
├── redis_store.py         # Redis 凭证存取层
├── streamlit_redis.py     # Streamlit ↔ Redis 集成
├── test_mcp.py            # 本地 MCP 客户端测试
├── requirements.txt       # Python 依赖
├── start.bat              # Windows 一键启动
├── .env.example           # 环境变量模板
└── lib/                   # 自包含依赖 (无需外部文件)
    ├── library_api.py     # 图书馆 HMAC 签名 + API
    ├── cas_login.py       # CAS 多种登录方式
    ├── cas_encrypt.py     # 密码 AES-CBC 加密
    ├── login_helper.py    # Playwright 凭证收割
    ├── captcha_solver.py  # TAC 滑块验证码破解
    ├── captcha_refs.npz   # 验证码模式参考库
    ├── courses_tool.py    # 课表查询
    ├── exam_tool.py       # 考试安排
    ├── grades_tool.py     # 成绩查询
    └── weather_tool.py    # 珞珈山天气
```

## 本地测试

```bash
python test_mcp.py
```

## 技术要点

- **ASGI 原生实现** — 无框架依赖，直接使用 `uvicorn` + ASGI 协议
- **`asyncio.to_thread`** — 所有阻塞操作 (Playwright, requests) 运行在线程池，不阻塞事件循环
- **Redis TTL 自动过期** — 凭证 4 小时自动清理，无需维护脚本
- **三层 fallback** — 缓存优先 → 过期 harvest → 提示重新登录
- **HMAC-SHA256 签名** — 纯 Python 复现图书馆 API 签名，~0.5s 响应
- **TAC 验证码自动破解** — 模式参考图差分法，成功率 100%

## 部署清单

- [ ] Python 依赖安装完成
- [ ] Playwright 浏览器安装完成
- [ ] Redis 正在运行
- [ ] Supervisor 保活已配置
- [ ] 反向代理已配置 (可选)
- [ ] Claude Code `~/.mcp.json` 已更新
- [ ] Claude Code 已重启
