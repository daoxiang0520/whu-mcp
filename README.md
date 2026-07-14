# WHU MCP Server

武汉大学校园服务 MCP 服务器。一个文件 (`server.py`) 搞定全部——图书馆座位、课表、成绩、考试、天气，14 个工具，零外部存储依赖。

## 快速开始

### 本地 stdio（推荐个人使用）

```bash
git clone https://github.com/daoxiang0520/LuojiaAgent.git
cd LuojiaAgent/mcp_http
pip install -r requirements.txt
playwright install chromium
```

`~/.mcp.json`：

```json
{
  "mcpServers": {
    "whu-lib": {
      "command": "python",
      "args": ["-X", "utf8", "/绝对路径/mcp_http/server.py", "--transport", "stdio"]
    }
  }
}
```

### 远程 SSE（零安装，直接连）

已部署服务器，在 `~/.mcp.json` 中加入以下配置即可使用：

```json
{
  "mcpServers": {
    "whu-lib": {
      "type": "sse",
      "url": "https://whu-mcp.daoxiang.xyz/sse"
    }
  }
}
```

> 当前服务器状态：可通过 `curl https://whu-mcp.daoxiang.xyz/health` 检查。

### 远程 SSE（自部署）

服务器上启动：

```bash
cd /www/wwwroot/luojiaagent/mcp_http
pip install -r requirements.txt
playwright install chromium && playwright install-deps chromium
python server.py --transport sse --host 127.0.0.1 --port 8000
```

`~/.mcp.json`：

```json
{
  "mcpServers": {
    "whu-lib": {
      "type": "sse",
      "url": "https://你的域名/sse"
    }
  }
}
```

### 安装 Skill（可选）

```bash
mkdir -p ~/.claude/skills/whu-lib
cp skill/SKILL.md ~/.claude/skills/whu-lib/SKILL.md
```

重启 Claude Code 后生效。

## 三种登录方式

| 方式 | 工具 | 说明 |
|---|---|---|
| 扫码登录 | `login_qr` → `login_qr_poll` | ASCII 二维码显示在对话中，手机扫码 |
| 密码登录 | `login_password` | 对话中直接输入学号密码 |
| 会话复用 | — | session_id 有效期内无需重新登录 |

## 使用

```
第一次:
  你: "帮我登录武大"
  Claude: [显示 ASCII QR 码]
  [手机扫码]
  你: "扫好了"
  Claude: "✅ session_id: abc123"

之后（4 小时内无需重新登录）:
  你: "工学分馆明天有座位吗"
  你: "我这学期 GPA 多少"
  你: "帮我预约 305 区 120 号，下午 2 点到 6 点"
  你: "明天带伞吗"
```

## 工具列表（14 个）

| 工具 | 登录 | 说明 |
|---|---|---|
| `login_password` | — | 学号+密码登录 |
| `login_qr` | — | ASCII 终端二维码，手机扫码 |
| `login_qr_poll` | — | 轮询扫码结果完成登录 |
| `get_seats` | ✅ | 分馆座位大盘 |
| `get_seat_map` | ✅ | 区域座位排布 |
| `reserve_seat` | ✅ | 预约座位（自动破解验证码） |
| `get_current_usage` | ✅ | 当前在坐座位 |
| `get_reservations` | ✅ | 预约记录 |
| `cancel_reservation` | ✅ | 取消预约 |
| `stop_usage` | ✅ | 签退释放 |
| `query_schedule` | ✅ | 课表 |
| `query_exam_schedule` | ✅ | 考试安排 |
| `query_grades` | ✅ | 成绩 + GPA |
| `get_weather` | ❌ | 珞珈山天气（免登） |

## 架构

```
用户
  │
Claude Code
  ├── Skill (whu-lib) ── 武大领域知识，按需注入
  └── MCP Client ─────── SSE/stdio 协议
  │
  ▼
MCP Server (server.py) ── 14 个工具，单文件
  │
  ├── lib/library_api.py   ──▶ 图书馆 (HMAC 签名 + API)
  ├── lib/courses_tool.py  ──▶ 教务 (课表)
  ├── lib/exam_tool.py     ──▶ 教务 (考试)
  ├── lib/grades_tool.py   ──▶ 教务 (成绩)
  ├── lib/weather_tool.py  ──▶ Open-Meteo (天气)
  ├── lib/cas_login.py     ──▶ CAS (认证)
  ├── lib/login_helper.py  ──▶ Playwright (凭证收割)
  ├── lib/captcha_solver.py──▶ 验证码破解
  └── lib/cas_encrypt.py   ──▶ AES 密码加密
```

### 存储

| 模式 | 存储方式 | 重启后 |
|---|---|---|
| SSE (远程) | 内存字典 `MemStore` | 丢失 |
| stdio (本地) | 文件 `.whu_session.json` | 保留 |

零外部依赖——不需要 Redis、数据库。

### 两级 Session

```
MCP Session                     Whu Session
(协议层，自动管理)                (业务层，login 后创建)
SSE 连接时分配                   12 位短 ID
仅用于消息路由                    存 CASTGC + token + educational
MCP Server 内存                  MemStore / FileStore
```

### 扫码登录流程

```
login_qr:
  CAS QR 图片 → OpenCV 解码 → qrcode 生成 ASCII → 终端显示
  用户 APP 扫码

login_qr_poll:
  轮询 CAS → CASTGC → Playwright harvest → token + educational
  → 存入 MemStore/FileStore → 返回 session_id
```

## 项目结构

```
mcp_http/
├── server.py              # 入口 (--transport stdio|sse)
├── requirements.txt
├── README.md
├── skill/
│   └── SKILL.md           # Claude Code Skill
└── lib/                   # 自包含依赖模块
    ├── library_api.py
    ├── cas_login.py
    ├── cas_encrypt.py
    ├── login_helper.py
    ├── captcha_solver.py
    ├── captcha_refs.npz   # 验证码参考库 (865KB)
    ├── courses_tool.py
    ├── exam_tool.py
    ├── grades_tool.py
    └── weather_tool.py
```

## 部署

### 服务器

```bash
cd /www/wwwroot/luojiaagent/mcp_http
pip install -r requirements.txt
playwright install chromium && playwright install-deps chromium
python server.py --transport sse --host 127.0.0.1 --port 8000
```

### 宝塔保活

Supervisor → 添加守护进程：

| 字段 | 值 |
|---|---|
| 名称 | whu-mcp |
| 目录 | /www/wwwroot/luojiaagent/mcp_http |
| 命令 | `python server.py --transport sse --host 127.0.0.1 --port 8000` |

### Nginx 反代

```nginx
location /sse {
    proxy_pass http://127.0.0.1:8000/sse;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_read_timeout 3600s;
}
location /messages { proxy_pass http://127.0.0.1:8000/messages; }
location /health   { proxy_pass http://127.0.0.1:8000/health; }
```

## FAQ

**Q: 密码安全吗？**  
A: HTTPS 传输。不存盘、不进日志、不落存储。对话中短暂出现后即被覆盖。

**Q: 二维码不显示？**  
A: 按 Ctrl+O 展开工具输出。ASCII QR 码在折叠区里。

**Q: stdio 和 SSE 怎么选？**  
A: 自己用 → stdio（零运维）。给同学用 → SSE（部署一次，大家共享）。

**Q: 为什么去掉 Redis？**  
A: 不需要。MCP Server 单进程，内存字典就够了。登录也在 MCP 里完成，没有跨进程通信需求。
