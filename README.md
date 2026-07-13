# WHU MCP Server

武汉大学校园服务 MCP 服务器 + Claude Code Skill，覆盖图书馆座位、课表、成绩、考试、天气五大场景。

## 两种使用方式

| | 远程 SSE（推荐） | 本地 stdio |
|---|---|---|
| **需要什么** | 我们维护的服务器 | 自己电脑跑 |
| **配置** | 三行 JSON | 三行 JSON |
| **安装** | 零安装 | pip install |
| **多人共享** | ✅ 同一服务器 | ❌ 各自独立 |
| **依赖 Redis** | ✅ 凭证中心存储 | ❌ 本地文件 |

## 用户指南

### 方式一：远程 SSE

**1.** 在 `~/.mcp.json` 中加入：

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

**2.** 安装 Skill（可选但推荐）：

```bash
mkdir -p ~/.claude/skills/whu-lib
cp skill/SKILL.md ~/.claude/skills/whu-lib/SKILL.md
```

**3.** 重启 Claude Code。

### 方式二：本地 stdio

**1.** 安装：

```bash
git clone https://github.com/daoxiang0520/LuojiaAgent.git
cd LuojiaAgent/mcp_http
pip install -r requirements.txt
playwright install chromium
```

**2.** `~/.mcp.json`：

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

**3.** 重启 Claude Code。

### 三种登录方式

| 方式 | 工具 | 说明 |
|---|---|---|
| 密码登录 | `login_password` | 在对话中直接输入学号密码 |
| 扫码登录 | `login_qr` → `login_qr_poll` | QR 码在对话中显示，APP 扫码 |
| Streamlit | 浏览器 | 扫码后侧边栏获取 session_id |

### 日常使用

```
第一次:

  你: "帮我登录武大"
  Claude: [显示 QR 码]
  [你用 APP 扫码]
  Claude: "✅ session_id: abc123"

之后（4 小时内无需重新登录）:

  你: "工学分馆明天下午有座位吗"
  你: "我这学期 GPA 多少"
  你: "明天什么课"
  你: "帮我预约 305 区 120 号"
  你: "我要走了，签退"
  你: "明天要带伞吗"
```

## 工具列表（14 个）

| 工具 | 需要登录 | 说明 |
|---|---|---|
| `login_password` | — | 学号+密码登录 |
| `login_qr` | — | 生成 CAS 扫码二维码 |
| `login_qr_poll` | — | 轮询扫码结果，完成登录 |
| `get_seats` | ✅ | 查询分馆座位大盘 |
| `get_seat_map` | ✅ | 查询区域内座位排布 |
| `reserve_seat` | ✅ | 预约座位（自动破解验证码） |
| `get_current_usage` | ✅ | 当前在坐座位 |
| `get_reservations` | ✅ | 预约记录（含历史） |
| `cancel_reservation` | ✅ | 取消预约 |
| `stop_usage` | ✅ | 签退释放座位 |
| `query_schedule` | ✅ | 课表查询 |
| `query_exam_schedule` | ✅ | 考试安排 |
| `query_grades` | ✅ | 成绩查询（含 GPA） |
| `get_weather` | ❌ | 珞珈山天气（免登） |

## 架构

```
┌──────────────────────────────────────────────────────────┐
│                      你的电脑                             │
│  Claude Code                                             │
│   ├── Skill (whu-lib)  ← 武大领域知识                    │
│   └── MCP Client       ← SSE/stdio 协议                  │
└──────────┬───────────────────────────────────────────────┘
           │
    ┌──────▼──────┐
    │  MCP Server  │  也可远程部署 (whu-mcp.daoxiang.xyz)
    │  :8000       │
    └──────┬──────┘
           │
    ┌──────▼──────┐     ┌─────────────┐
    │    Redis    │     │  lib/ 模块   │
    │  session    │     │ library_api  │──▶ 图书馆 API
    │  凭证 4h TTL │     │ courses_tool │──▶ 教务 API
    └─────────────┘     │ weather_tool │──▶ 天气 API
                        │ captcha      │──▶ 验证码破解
                        └─────────────┘
```

## 部署（维护者）

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

### Streamlit 集成

在 `app.py` 登录成功后：

```python
from streamlit_redis import save_to_redis
sid = save_to_redis(
    castgc=st.session_state.cookies["castgc"],
    token=st.session_state.cookies["library_token"],
    hmac_key=st.session_state.cookies["library_hmac_key"],
    educational=st.session_state.cookies.get("educational", ""),
    student_id=st.session_state.cookies.get("student_id", ""),
)
st.sidebar.success(f"MCP session_id: `{sid}`")
```

## 项目结构

```
mcp_http/
├── server.py              # MCP Server (--transport stdio|sse)
├── redis_store.py         # Redis 凭证存取
├── streamlit_redis.py     # Streamlit ↔ Redis 集成
├── requirements.txt
├── start.bat              # Windows 一键启动
├── README.md
├── skill/
│   └── SKILL.md           # Claude Code Skill
└── lib/                   # 自包含依赖
    ├── library_api.py     # 图书馆 HMAC 签名 + API
    ├── cas_login.py       # CAS 多方式登录
    ├── cas_encrypt.py     # 密码 AES 加密
    ├── login_helper.py    # Playwright 凭证收割
    ├── captcha_solver.py  # 验证码破解
    ├── captcha_refs.npz   # 参考库 (865KB)
    ├── courses_tool.py    # 课表
    ├── exam_tool.py       # 考试
    ├── grades_tool.py     # 成绩
    └── weather_tool.py    # 天气
```

## FAQ

**Q: 密码安全吗？**  
A: HTTPS 传输，不存盘、不进日志、不进 Redis。对话中短暂出现后即被后续上下文覆盖。

**Q: 为什么有时 404？**  
A: Nginx 超时杀了 SSE 长连接。确认 `proxy_read_timeout 3600s` 已配置。

**Q: stdio 和 SSE 怎么选？**  
A: 自己用 → stdio。给同学用 → SSE。
