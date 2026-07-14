"""
武大图书馆 MCP Server — 支持 stdio 和 SSE 两种传输模式

stdio 模式 (本地, 零依赖):
    python server.py --transport stdio
    Claude Code 配置:
    {
      "mcpServers": {
        "whu-lib": {
          "command": "python",
          "args": ["-X", "utf8", "/path/to/server.py", "--transport", "stdio"]
        }
      }
    }

SSE 模式 (远程服务器):
    python server.py --transport sse --port 8000
    Claude Code 配置:
    {
      "mcpServers": {
        "whu-lib": {
          "type": "sse",
          "url": "https://your-server/sse"
        }
      }
    }
"""

import sys
import os

# 所有依赖模块统一放在 lib/ 目录，自包含
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))

import json
import asyncio
import argparse
from functools import partial

import uvicorn

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent

# ── 配置 ────────────────────────────────────────────────────

class MemStore:
    """内存凭证存储 — 零外部依赖。重启丢失，SSE 模式使用。"""
    def __init__(self):
        self._data: dict = {}

    def save(self, castgc: str, token: str, hmac_key: str,
             student_id: str = "", educational: str = "", **kw) -> str:
        import uuid as _uuid
        sid = str(_uuid.uuid4())[:12]
        self._data[sid] = {
            "castgc": castgc, "library_token": token,
            "library_hmac_key": hmac_key, "student_id": student_id,
            "educational": educational,
        }
        return sid

    def get(self, session_id: str) -> dict | None:
        return self._data.get(session_id)

    def delete(self, session_id: str) -> bool:
        return self._data.pop(session_id, None) is not None

    def exists(self, session_id: str) -> bool:
        return session_id in self._data


class FileStore:
    """本地文件凭证存储 (stdio 模式使用，零外部依赖)。"""
    def __init__(self):
        self._path = os.path.join(os.path.dirname(__file__), ".whu_session.json")

    def save(self, castgc: str, token: str, hmac_key: str,
             student_id: str = "", educational: str = "", **kw) -> str:
        data = {"castgc": castgc, "library_token": token,
                "library_hmac_key": hmac_key, "student_id": student_id,
                "educational": educational}
        import json as _json
        with open(self._path, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False)
        return "local"

    def get(self, session_id: str = "") -> dict | None:
        import json as _json
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            return None

    def delete(self, session_id: str = "") -> bool:
        try:
            os.remove(self._path)
            return True
        except Exception:
            return False

    def exists(self, session_id: str = "") -> bool:
        return os.path.exists(self._path)


cred_store = None  # 在 main 中根据 --transport 初始化


def _get_store():
    """获取凭证存储实例 (延迟初始化)。"""
    assert cred_store is not None, "cred_store 未初始化"
    return cred_store


server = Server("whu-lib")

# ══════════════════════════════════════════════════════════════
# 阻塞操作的同步实现（与 LuojiaAgent 一致，运行在 asyncio.to_thread）
# ══════════════════════════════════════════════════════════════

def _do_login(sid: str, pwd: str) -> tuple:
    """同步：CAS 密码登录 → Playwright harvest → 返回 (castgc, token, hmac_key, educational)"""
    from cas_login import CasClient
    from login_helper import harvest_from_castgc

    client = CasClient()
    castgc = client.login_password(sid, pwd)
    harvest = harvest_from_castgc(castgc)
    return (castgc, harvest["library_token"], harvest["library_hmac_key"],
            harvest.get("educational", ""))


def _do_get_session(session_id: str) -> tuple:
    """同步：取凭证 → 创建 LibrarySession。"""
    creds = _get_store().get(session_id)
    if not creds:
        return None, "❌ session 无效或已过期，请重新登录。"
    try:
        from library_api import LibrarySession
        sess = LibrarySession(
            token=creds["library_token"],
            hmac_key_encrypted=creds["library_hmac_key"],
        )
        return (sess, creds), None
    except Exception as e:
        return None, f"❌ 创建 LibrarySession 失败: {e}"


def _refresh_creds(session_id: str) -> bool:
    """token 过期时用 CASTGC 重新 harvest，返回是否成功。"""
    store = _get_store()
    creds = store.get(session_id)
    if not creds or not creds.get("castgc"):
        return False
    try:
        from login_helper import harvest_from_castgc
        harvest = harvest_from_castgc(creds["castgc"])
        store.save(
            castgc=creds["castgc"],
            token=harvest["library_token"],
            hmac_key=harvest["library_hmac_key"],
            student_id=creds.get("student_id", ""),
            educational=harvest.get("educational", creds.get("educational", "")),
        )
        return True
    except Exception:
        return False


def _do_get_seats(session_id: str, date: str, library: str) -> str:
    return _call_library(session_id, lambda s: s.get_seats(date, library))


def _call_library(session_id: str, fn, ok_msg: str = "") -> str:
    """通用 wrapper：调图书馆 API，token 过期自动刷新后重试。"""
    sess_creds, err = _do_get_session(session_id)
    if err:
        return err
    sess, creds = sess_creds

    def _try():
        try:
            result = fn(sess)
            if isinstance(result, str):
                return result, True
            if isinstance(result, dict):
                msg = str(result.get("message", ""))
                if not result.get("status") and any(
                    kw in msg for kw in ["过期", "token", "Token", "鉴权", "失效", "登录"]
                ):
                    return msg, False
                if result.get("status") and ok_msg:
                    return ok_msg, True
                return json.dumps(result, ensure_ascii=False, indent=2), True
            return str(result), True
        except Exception as e:
            return str(e), False

    text, ok = _try()
    if ok:
        return text

    # token 过期 → CASTGC 刷新 → 重试
    if _refresh_creds(session_id):
        sess_creds2, err2 = _do_get_session(session_id)
        if not err2:
            text2, _ = _try()
            return text2

    return f"❌ 请求失败（凭证已过期，请重新登录）: {text}"


def _do_get_seat_map(session_id: str, date: str, area_id: str, begin_minute: int) -> str:
    return _call_library(session_id,
        lambda s: s.get_seat_map(date, area_id, begin_minute))


def _do_reserve(session_id: str, date: str, area_id: str,
                seat_label: str, begin_minute: int, end_minute: int) -> str:
    sess_creds, err = _do_get_session(session_id)
    if err:
        return err
    sess, creds = sess_creds

    def _try_reserve():
        seat_map = sess.get_seat_map(date, area_id)
        seat_uuid = ""
        for uid, info in seat_map.get("data", {}).items():
            if info.get("label") == seat_label:
                seat_uuid = uid
                break
        if not seat_uuid:
            return f"❌ 未找到 {seat_label} 号座位。"
        path = f"/jsq/static/frontApi/make/freeBook/{seat_uuid}/{date}/{begin_minute}/{end_minute}"
        result = sess._post(path, {})
        msg = result.get("message", "")
        if not result.get("status") and ("验证" in msg or "滑块" in msg or "captcha" in msg.lower()):
            from captcha_solver import solve_captcha
            cap_result = solve_captcha(username=creds.get("student_id", ""))
            if cap_result.get("success"):
                cap_token = cap_result.get("data", {}).get("token", "")
                result = sess._post(f"{path}?capToken={cap_token}", {})
        if result.get("status"):
            return f"✅ 预约成功！{date} {seat_label}号"
        return f"❌ 预约失败: {result.get('message', '未知错误')}"

    text = _try_reserve()
    if text.startswith("✅"):
        return text
    if _refresh_creds(session_id):
        sess2, _ = _do_get_session(session_id)
        sess = sess2
        return _try_reserve()
    return text


def _do_current_usage(session_id: str) -> str:
    return _call_library(session_id, lambda s: s.get_current_usage())


def _do_reservations(session_id: str) -> str:
    return _call_library(session_id, lambda s: s.get_reservations())


def _do_cancel(session_id: str, reservation_id: str) -> str:
    return _call_library(session_id, lambda s: s.cancel(reservation_id),
                         ok_msg="✅ 取消成功！")


def _do_stop(session_id: str) -> str:
    return _call_library(session_id, lambda s: s.stop(),
                         ok_msg="✅ 签退成功，座位已释放！")


def _get_educational(session_id: str) -> tuple[str | None, str | None]:
    """从 Redis 获取 educational (教务系统) cookie。"""
    creds = _get_store().get(session_id)
    if not creds:
        return None, "❌ session 无效或已过期。"
    edu = creds.get("educational", "")
    if not edu or "JSESSIONID" not in edu:
        return None, "❌ 教务系统 Cookie 缺失，请重新登录（login_password）获取完整凭证。"
    return edu, None


def _do_query_schedule(session_id: str, year: str, semester: str) -> str:
    """同步：查询课表。复用 courses_tool.py 的逻辑。"""
    from courses_tool import map_semester, format_schedule_report
    import requests, time as _time, json as _json

    edu_cookie, err = _get_educational(session_id)
    if err:
        return err

    xqm_code, semester_display, start_sunday = map_semester(semester)

    url = "https://jwgl.whu.edu.cn/kbcx/xskbcx_cxXsgrkb.html?gnmkdm=N2151"
    headers = {
        "Cookie": edu_cookie,
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Referer": "https://jwgl.whu.edu.cn/kbcx/xskbcx_cxXskbcxIndex.html?gnmkdm=N2151&layout=default",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": "Mozilla/5.0",
    }
    payload = {
        "xnm": year, "xqm": xqm_code, "kzlx": "ck",
        "xsdm": "", "kclbdm": "", "kclxdm": "",
        "validate": "fake_captcha_token_for_test:",
    }

    resp = requests.post(url, data=payload, headers=headers, timeout=15, verify=False)
    if resp.status_code != 200:
        return f"❌ 教务系统返回 HTTP {resp.status_code}"
    data = _json.loads(resp.text)
    kb_list = data.get("kbList", [])
    if not kb_list:
        # 自动回退到第二学期
        if semester == "3":
            xqm2, sem2_disp, start_sun2 = map_semester("2")
            payload["xqm"] = xqm2
            resp2 = requests.post(url, data=payload, headers=headers, timeout=15, verify=False)
            data2 = _json.loads(resp2.text)
            kb_list = data2.get("kbList", [])
            semester_display = sem2_disp
            start_sunday = start_sun2
        if not kb_list:
            return f"📭 {year}-{int(year)+1} 学年 {semester_display} 无课程安排。"
    return format_schedule_report(kb_list, year, semester_display, start_sunday)


def _do_query_exam(session_id: str, year: str, semester: str) -> str:
    """同步：查询考试安排。"""
    from exam_tool import map_semester, format_exam_report
    import requests, time as _time, json as _json

    edu_cookie, err = _get_educational(session_id)
    if err:
        return err

    xqm_code, semester_display = map_semester(semester)

    url = "https://jwgl.whu.edu.cn/kwgl/kscx_cxXsksxxIndex.html?doType=query&gnmkdm=N358105"
    headers = {
        "Cookie": edu_cookie,
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Referer": "https://jwgl.whu.edu.cn/kwgl/kscx_cxXsksxxIndex.html?gnmkdm=N358105&layout=default",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": "Mozilla/5.0",
    }
    payload = {
        "xnm": year, "xqm": xqm_code,
        "ksmcdmb_id": "", "kch": "", "kc": "", "ksrq": "", "kkbm_id": "",
        "_search": "false", "nd": str(int(_time.time() * 1000)),
        "queryModel.showCount": "15", "queryModel.currentPage": "1",
        "queryModel.sortName": "", "queryModel.sortOrder": "asc", "time": "3",
    }

    resp = requests.post(url, data=payload, headers=headers, timeout=15, verify=False)
    if resp.status_code != 200:
        return f"❌ 教务系统返回 HTTP {resp.status_code}"
    data = _json.loads(resp.text)
    items = data.get("items", [])
    if not items:
        return f"📭 {year}-{int(year)+1} 学年 {semester_display} 无考试安排。"
    return format_exam_report(items, year, semester_display)


def _do_query_grades(session_id: str, year: str, semester: str) -> str:
    """同步：查询成绩。"""
    from grades_tool import map_semester, format_grade_report
    import requests, time as _time, json as _json

    edu_cookie, err = _get_educational(session_id)
    if err:
        return err

    xqm_code, semester_display = map_semester(semester)

    url = "https://jwgl.whu.edu.cn/cjcx/cjcx_cxXsgrcj.html?doType=query&gnmkdm=N305005"
    headers = {
        "Cookie": edu_cookie,
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Referer": "https://jwgl.whu.edu.cn/cjcx/cjcx_cxDgXscj.html?gnmkdm=N305005&layout=default",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": "Mozilla/5.0",
    }
    payload = {
        "xnm": year, "xqm": xqm_code, "sfzgcj": "",
        "validate": "sl4fz4ckumrbtqq6j:",
        "kcbj": "", "pkey": "", "_search": "false",
        "nd": str(int(_time.time() * 1000)),
        "queryModel.showCount": "15", "queryModel.currentPage": "1",
        "queryModel.sortName": "", "queryModel.sortOrder": "asc", "time": "666",
    }

    resp = requests.post(url, data=payload, headers=headers, timeout=15, verify=False)
    if resp.status_code != 200:
        return f"❌ 教务系统返回 HTTP {resp.status_code}"
    data = _json.loads(resp.text)
    items = data.get("items", [])
    if not items:
        return f"📭 {year}-{int(year)+1} 学年 {semester_display} 无成绩记录。"
    return format_grade_report(items, year, semester_display)


# QR 登录的全局状态（同一时刻只有一个 QR 码有效）
_qr_client = None


def _do_get_qr() -> str:
    """同步：获取 CAS QR → OpenCV 解码 → qrcode 生成 ASCII 终端二维码。"""
    global _qr_client
    import base64 as _b64, io as _io
    import cv2, numpy as _np, qrcode as _qr

    from cas_login import CasClient
    _qr_client = CasClient()
    qr_b64 = _qr_client.qr_get_image()
    if not qr_b64:
        raise RuntimeError("获取 CAS QR 码失败，请重试。")

    qr_bytes = _b64.b64decode(qr_b64)
    nparr = _np.frombuffer(qr_bytes, _np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
    data, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
    if not data:
        raise RuntimeError("QR 解码失败，请重试。")

    qr = _qr.QRCode(border=1, box_size=1)
    qr.add_data(data)
    qr.make()
    buf = _io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    return "\n" + buf.getvalue()


def _do_poll_qr() -> tuple[str, str, str, str]:
    """同步：轮询 QR 扫码结果 → harvest → 返回 (castgc, token, hmac_key, educational)"""
    global _qr_client
    from login_helper import harvest_from_castgc

    if _qr_client is None:
        raise RuntimeError("没有活跃的 QR 码，请先调用 login_qr。")

    castgc = _qr_client.qr_poll(timeout=120)
    if not castgc:
        _qr_client = None
        raise TimeoutError("QR 码扫码超时 (120s)，请重新调用 login_qr 重试。")

    harvest = harvest_from_castgc(castgc)
    _qr_client = None
    return (castgc, harvest["library_token"], harvest["library_hmac_key"],
            harvest.get("educational", ""))




def _do_weather() -> str:
    """同步：查询武大天气，复用 weather_tool.py。"""
    from weather_tool import get_whu_rain_forecast
    return get_whu_rain_forecast.invoke({})

# ══════════════════════════════════════════════════════════════
# 工具注册
# ══════════════════════════════════════════════════════════════

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="login_password",
            description="用学号和密码直接登录 CAS，获取图书馆凭证。凭证存入 Redis，返回 session_id。",
            inputSchema={
                "type": "object",
                "properties": {
                    "student_id": {
                        "type": "string",
                        "description": "学号（如 2025302114221）",
                    },
                    "password": {
                        "type": "string",
                        "description": "CAS 统一身份认证密码",
                    },
                },
                "required": ["student_id", "password"],
            },
        ),
        Tool(
            name="login_qr",
            description="生成武大 CAS 扫码登录的二维码。QR 码直接在对话中显示，用户用智慧珞珈 APP 扫码。扫码后需调用 login_qr_poll 完成登录。",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="login_qr_poll",
            description="轮询 CAS 扫码状态。需先调用 login_qr 获取 QR 码。扫码完成后自动 harvest 凭证，返回 session_id。",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_seats",
            description="查询图书馆某天的座位大盘。需要 session_id（从 login_password 获取，或从 Streamlit 登录后获取）。",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "会话 ID（登录后获得）",
                    },
                    "date": {
                        "type": "string",
                        "description": "日期 YYYY-MM-DD",
                    },
                    "library": {
                        "type": "string",
                        "description": "分馆: 总馆/信息分馆/工学分馆/医学分馆",
                        "default": "总馆",
                    },
                },
                "required": ["session_id", "date"],
            },
        ),
        Tool(
            name="get_seat_map",
            description="查询指定区域内所有座位的实时空闲状态。需要先通过 get_seats 获取 area_id。",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "会话 ID"},
                    "date": {"type": "string", "description": "日期 YYYY-MM-DD"},
                    "area_id": {"type": "string", "description": "区域 ID（19位雪花ID，从 get_seats 返回）"},
                    "begin_time": {"type": "string", "description": "开始时间 HH:MM", "default": "08:12"},
                },
                "required": ["session_id", "date", "area_id"],
            },
        ),
        Tool(
            name="reserve_seat",
            description="预约图书馆座位。自动破解滑块验证码。需要 session_id。",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "会话 ID"},
                    "date": {"type": "string", "description": "日期 YYYY-MM-DD"},
                    "area_id": {"type": "string", "description": "区域 ID"},
                    "seat_label": {"type": "string", "description": "座位号（桌贴号）"},
                    "begin_time": {"type": "string", "description": "开始时间 HH:MM"},
                    "end_time": {"type": "string", "description": "结束时间 HH:MM"},
                },
                "required": ["session_id", "date", "area_id", "seat_label", "begin_time", "end_time"],
            },
        ),
        Tool(
            name="get_current_usage",
            description="查询当前正在使用的座位（已签到入座）。",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "会话 ID"},
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="get_reservations",
            description="查询预约记录（含当前和历史）。",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "会话 ID"},
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="cancel_reservation",
            description="取消预约。",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "会话 ID"},
                    "reservation_id": {"type": "string", "description": "预约单 ID"},
                },
                "required": ["session_id", "reservation_id"],
            },
        ),
        Tool(
            name="stop_usage",
            description="签退释放当前座位。",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "会话 ID"},
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="query_schedule",
            description="查询武大教务系统课程表。需要 session_id（登录后获得）。",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "会话 ID"},
                    "year": {"type": "string", "description": "学年，如 2025 表示 2025-2026", "default": "2025"},
                    "semester": {"type": "string", "description": "学期: 1=第一学期, 2=第二学期, 3=第三学期", "default": "3"},
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="query_exam_schedule",
            description="查询武大教务系统期末考试安排。",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "会话 ID"},
                    "year": {"type": "string", "description": "学年", "default": "2025"},
                    "semester": {"type": "string", "description": "学期: 1/2/3", "default": "1"},
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="query_grades",
            description="查询武大教务系统成绩（含 GPA 计算）。",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "会话 ID"},
                    "year": {"type": "string", "description": "学年", "default": "2025"},
                    "semester": {"type": "string", "description": "学期: 1/2/3", "default": "2"},
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="get_weather",
            description="查询武汉大学（珞珈山）精准天气和降雨预测。无需登录。",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


# ══════════════════════════════════════════════════════════════
# 内部辅助
# ══════════════════════════════════════════════════════════════

def _get_library_session(session_id: str):
    """从 Redis 获取凭证，创建 LibrarySession。"""
    creds = _get_store().get(session_id)
    if not creds:
        return None, "❌ session 无效或已过期，请重新登录。"
    try:
        from library_api import LibrarySession
        sess = LibrarySession(
            token=creds["library_token"],
            hmac_key_encrypted=creds["library_hmac_key"],
        )
        return sess, None
    except Exception as e:
        return None, f"❌ 创建 LibrarySession 失败: {e}"


def _get_username_from_session(creds: dict) -> str:
    """从 Redis 凭证中获取 username（用于验证码）。"""
    sid = creds.get("student_id", "")
    if not sid:
        # 从 library_token 反推（JWT sub 字段）
        try:
            import base64 as _b64
            jwt_token = ""  # 需要从 harvest 流程获取
        except Exception:
            pass
    return sid


# ══════════════════════════════════════════════════════════════
# 工具实现
# ══════════════════════════════════════════════════════════════

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    match name:
        case "login_qr":
            try:
                qr_text = await asyncio.to_thread(_do_get_qr)
                return [TextContent(type="text",
                    text=f"{qr_text}\n（如未显示完整二维码，请按 Ctrl+O 展开）\n📱 用智慧珞珈 APP 扫码，完成后调用 login_qr_poll 登录。"
                )]
            except Exception as e:
                return [TextContent(type="text", text=f"❌ 获取 QR 码失败: {e}")]

        case "login_qr_poll":
            try:
                castgc, token, hmac_key, educational = await asyncio.to_thread(
                    _do_poll_qr
                )
                session_id = _get_store().save(
                    castgc=castgc, token=token, hmac_key=hmac_key,
                    educational=educational,
                )
                return [TextContent(type="text",
                    text=f"✅ 扫码登录成功！session_id: `{session_id}` (有效期约 4 小时)")
                ]
            except TimeoutError as e:
                return [TextContent(type="text", text=f"⏰ {e}")]
            except Exception as e:
                return [TextContent(type="text", text=f"❌ 登录失败: {e}")]

        case "login_password":
            sid = arguments["student_id"]
            pwd = arguments["password"]
            try:
                castgc, token, hmac_key, educational = await asyncio.to_thread(
                    _do_login, sid, pwd
                )
                session_id = _get_store().save(
                    castgc=castgc, token=token, hmac_key=hmac_key,
                    student_id=sid, educational=educational,
                )
                return [TextContent(
                    type="text",
                    text=(f"✅ 登录成功！\n"
                          f"session_id: `{session_id}`\n"
                          f"有效期: 约 4 小时")
                )]
            except Exception as e:
                return [TextContent(type="text", text=f"❌ 登录失败: {e}")]

        case "get_seats":
            sid = arguments["session_id"]
            date = arguments["date"]
            library = arguments.get("library", "总馆")
            result = await asyncio.to_thread(
                _do_get_seats, sid, date, library
            )
            return [TextContent(type="text", text=result)]

        case "get_seat_map":
            sid = arguments["session_id"]
            date = arguments["date"]
            area_id = arguments["area_id"]
            bt = arguments.get("begin_time", "08:12")
            try:
                h, m = map(int, bt.split(":"))
                bm = h * 60 + m
            except Exception:
                bm = 492
            result = await asyncio.to_thread(
                _do_get_seat_map, sid, date, area_id, bm
            )
            return [TextContent(type="text", text=result)]

        case "reserve_seat":
            sid = arguments["session_id"]
            date = arguments["date"]
            area_id = arguments["area_id"]
            seat_label = arguments["seat_label"]
            begin_time = arguments["begin_time"]
            end_time = arguments["end_time"]
            try:
                bh, bm = map(int, begin_time.split(":"))
                eh, em = map(int, end_time.split(":"))
            except Exception:
                return [TextContent(type="text", text="❌ 时间格式错误")]
            result = await asyncio.to_thread(
                _do_reserve, sid, date, area_id, seat_label,
                bh * 60 + bm, eh * 60 + em
            )
            return [TextContent(type="text", text=result)]

        case "get_current_usage":
            result = await asyncio.to_thread(
                _do_current_usage, arguments["session_id"]
            )
            return [TextContent(type="text", text=result)]

        case "get_reservations":
            result = await asyncio.to_thread(
                _do_reservations, arguments["session_id"]
            )
            return [TextContent(type="text", text=result)]

        case "cancel_reservation":
            result = await asyncio.to_thread(
                _do_cancel, arguments["session_id"], arguments["reservation_id"]
            )
            return [TextContent(type="text", text=result)]

        case "stop_usage":
            result = await asyncio.to_thread(
                _do_stop, arguments["session_id"]
            )
            return [TextContent(type="text", text=result)]

        case "query_schedule":
            sid = arguments["session_id"]
            year = arguments.get("year", "2025")
            semester = arguments.get("semester", "3")
            result = await asyncio.to_thread(
                _do_query_schedule, sid, year, semester
            )
            return [TextContent(type="text", text=result)]

        case "query_exam_schedule":
            sid = arguments["session_id"]
            year = arguments.get("year", "2025")
            semester = arguments.get("semester", "1")
            result = await asyncio.to_thread(
                _do_query_exam, sid, year, semester
            )
            return [TextContent(type="text", text=result)]

        case "query_grades":
            sid = arguments["session_id"]
            year = arguments.get("year", "2025")
            semester = arguments.get("semester", "2")
            result = await asyncio.to_thread(
                _do_query_grades, sid, year, semester
            )
            return [TextContent(type="text", text=result)]

        case "get_weather":
            result = await asyncio.to_thread(_do_weather)
            return [TextContent(type="text", text=result)]

        case _:
            return [TextContent(type="text", text=f"未知工具: {name}")]


# ══════════════════════════════════════════════════════════════
# SSE + HTTP 服务器
# ══════════════════════════════════════════════════════════════

sse = SseServerTransport("/messages")


async def handle_sse(scope, receive, send):
    """SSE 长连接端点。"""
    async with sse.connect_sse(scope, receive, send) as (read, write):
        await server.run(read, write, server.create_initialization_options())


async def async_app(scope, receive, send):
    """原生 ASGI 应用，按路径路由。"""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    path = scope["path"]
    if path == "/sse" and scope["type"] == "http":
        await handle_sse(scope, receive, send)
    elif path == "/messages" and scope["method"] == "POST":
        await sse.handle_post_message(scope, receive, send)
    elif path == "/health":
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({
            "type": "http.response.body",
            "body": b'{"status":"ok"}',
        })
    else:
        await send({
            "type": "http.response.start",
            "status": 404,
            "headers": [(b"content-type", b"text/plain")],
        })
        await send({
            "type": "http.response.body",
            "body": b"Not Found",
        })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WHU Library MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="sse",
                        help="传输模式: stdio (本地) 或 sse (远程服务器)")
    parser.add_argument("--host", default="0.0.0.0", help="SSE 模式监听地址")
    parser.add_argument("--port", type=int, default=8000, help="SSE 模式监听端口")
    args = parser.parse_args()

    if args.transport == "stdio":
        # === stdio 模式：本地文件存储，零外部依赖 ===
        from mcp.server.stdio import stdio_server as _stdio_server
        _store = FileStore()
        cred_store = _store
        print(f"[stdio] 凭证文件: {_store._path}", file=sys.stderr)

        async def _stdio_main():
            async with _stdio_server() as (read, write):
                await server.run(read, write, server.create_initialization_options())

        asyncio.run(_stdio_main())

    else:
        # === SSE 模式：Redis 凭证 + HTTP 服务器 ===
        cred_store = MemStore()
        print(f"""
==========================================================
       WHU Library MCP Server (SSE)

  SSE endpoint: http://{args.host}:{args.port}/sse
  Message endpoint: http://{args.host}:{args.port}/messages
  Health check: http://{args.host}:{args.port}/health

  Claude Code config (.mcp.json):
  {{
    "mcpServers": {{
      "whu-lib": {{
        "type": "sse",
        "url": "http://<host>:{args.port}/sse"
      }}
    }}
  }}
==========================================================
        """)
        uvicorn.run(async_app, host=args.host, port=args.port)
