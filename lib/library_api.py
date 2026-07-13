"""
武汉大学图书馆 API 完整调用链（纯 Python，无需浏览器）

逆向自 seat.lib.whu.edu.cn SPA (app.949ae52dbe27da10244e.js)

认证体系：
  frontApi/*  → HMAC-SHA256 签名（必须）
  cap/cg/*    → AES-CTR + RSA（验证码，此模块不涉及）

用法:
    from library_api import LibrarySession

    # 方式1: 从浏览器手动获取
    sess = LibrarySession(
        token="a0ee9b...",           # sessionStorage['jsq_p-token']
        hmac_key_encrypted="iME1...", # sessionStorage['jsq_p-systemInfo'].hmacKey
    )
    sess.get_seats("2026-07-10", "总馆")

    # 方式2: CAS SSO 自动获取（需要 CASTGC）
    sess = LibrarySession.from_castgc("TGT-112146--...")
    sess.get_seats("2026-07-10", "总馆")
"""

import base64
import hashlib
import hmac as _hmac
import json
import time
import uuid

import requests
import urllib3
from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import unpad

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://seat.lib.whu.edu.cn"

LIBRARY_IDS = {
    "总馆":     "1812737769937670144",
    "主馆":     "1812737769937670144",
    "信息分馆": "1812738485913751552",
    "工学分馆": "1812738878798401536",
    "医学分馆": "1812739190351302656",
}


# ============================================================
# HMAC 签名
# ============================================================

def decrypt_hmac_key(encrypted_b64: str) -> bytes:
    """
    解密 sessionStorage['jsq_p-systemInfo'].hmacKey

    SPA 以 AES-128-CBC 加密存储：
      key  = b"server_date_time"  (16 bytes)
      iv   = b"client_date_time"  (16 bytes)
      mode = CBC, padding = PKCS7

    解密后得到真实 HMAC 密钥（实测为 b"whu2024lib"，10 bytes）
    """
    ct = base64.b64decode(encrypted_b64)
    cipher = AES.new(b"server_date_time", AES.MODE_CBC, b"client_date_time")
    return unpad(cipher.decrypt(ct), AES.block_size)


def make_signature(hmac_key: bytes, method: str = "POST") -> dict:
    """
    生成 HMAC-SHA256 签名请求头。

    签名公式（来自 app.js Axios 拦截器）：
      sign_str = "seat::" + UUID + "::" + timestamp_ms + "::" + METHOD.toUpperCase()
      signature = HMAC-SHA256(sign_str, hmac_key).hex()
    """
    rid = str(uuid.uuid4())
    ts = str(int(time.time() * 1000))
    sign_str = f"seat::{rid}::{ts}::{method.upper()}"
    sig = _hmac.new(hmac_key, sign_str.encode(), hashlib.sha256).hexdigest()
    return {
        "X-request-id": rid,
        "X-request-date": ts,
        "X-hmac-request-key": sig,
    }


# ============================================================
# LibrarySession
# ============================================================

class LibrarySession:
    """图书馆 API 会话，自动处理 HMAC 签名"""

    def __init__(self, token: str, hmac_key_encrypted: str):
        self.token = token
        self.hmac_key = decrypt_hmac_key(hmac_key_encrypted)
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({
            "Content-Type": "application/json;charset=UTF-8",
            "token": token,
            "loginType": "PC",
        })

    def _post(self, path: str, body: dict = None) -> dict:
        """发送带 HMAC 签名的 POST 请求"""
        sig_headers = make_signature(self.hmac_key)
        headers = {**sig_headers}
        resp = self.session.post(
            f"{BASE_URL}{path}",
            json=body or {},
            headers=headers,
            timeout=15,
        )
        return resp.json()

    # ---- 座位查询 ----

    def get_seats(self, date: str, library: str = "总馆") -> list:
        """查询分馆座位大盘"""
        venue_id = LIBRARY_IDS.get(library, LIBRARY_IDS["总馆"])
        path = f"/jsq/static/frontApi/res/findRoomDuration/{venue_id}/{date}"
        body = {
            "beginMinute": 492, "currentPage": 1, "endMinute": 0,
            "floorId": 0, "minMinute": 0, "pageSize": 200,
            "power": False, "roomType": False, "sortField": "", "sortType": "", "windows": False,
        }
        return self._post(path, body)

    def get_seat_map(self, date: str, area_id: str, begin_minute: int = 492) -> dict:
        """查询区域内座位排布"""
        path = f"/jsq/static/frontApi/res/freeSeatIdsDuration/{area_id}/{date}"
        return self._post(path, {"beginMinute": begin_minute, "endMinute": 0})

    # ---- 预约 ----

    def reserve(self, date: str, area_id: str, seat_label: str,
                begin_time: str, end_time: str, captcha_token: str = "") -> dict:
        """预约座位"""
        bh, bm = map(int, begin_time.split(":"))
        eh, em = map(int, end_time.split(":"))
        begin_min = bh * 60 + bm
        end_min = eh * 60 + em

        # 1. 获取座位 UUID
        seat_map = self.get_seat_map(date, area_id, begin_min)
        seat_uuid = ""
        for uid, info in seat_map.get("data", {}).items():
            if info.get("label") == seat_label:
                seat_uuid = uid
                break
        if not seat_uuid:
            return {"status": False, "message": f"座位 {seat_label} 未找到"}

        # 2. 提交预约
        path = f"/jsq/static/frontApi/make/freeBook/{seat_uuid}/{date}/{begin_min}/{end_min}"
        if captcha_token:
            path += f"?capToken={captcha_token}"
        return self._post(path, {})

    # ---- 记录查询 ----

    def get_reservations(self) -> dict:
        """查询预约记录"""
        return self._post("/jsq/static/frontApi/user/history/1/50", {})

    def get_current_usage(self) -> dict:
        """查询当前使用中座位"""
        return self._post("/jsq/static/frontApi/user/currentUseMake", {})

    def cancel(self, reservation_id: str) -> dict:
        """取消预约"""
        return self._post(f"/jsq/static/frontApi/make/cancel/{reservation_id}", {})

    def stop(self) -> dict:
        """签退释放座位"""
        return self._post("/jsq/static/frontApi/make/stop", {})

    # ---- CAS SSO（需要 CASTGC） ----

    @classmethod
    def from_castgc(cls, castgc: str) -> "LibrarySession":
        """
        用 CASTGC 票据免浏览器完成 CAS SSO → 图书馆 OAuth → 获取 token + hmacKey

        要求: CASTGC 票据仍有效（CAS 登录后几小时内有效）
        """
        sess = requests.Session()
        sess.verify = False
        sess.cookies.set("CASTGC", castgc, domain="cas.whu.edu.cn", path="/authserver")

        # Step 1: CAS → 图书馆 OAuth → JWT
        oauth_url = (
            "https://cas.whu.edu.cn/authserver/login"
            "?service=https%3A%2F%2Fseat.lib.whu.edu.cn%2Frem%2Fstatic%2Fsso%2FwebOAuthRed"
        )
        resp = sess.get(oauth_url, allow_redirects=True, timeout=30)

        # 从重定向历史中找 JWT
        import re
        jwt_token = ""
        for h in resp.history + [resp]:
            if "token=" in h.url:
                m = re.search(r"token=([^&]+)", h.url)
                jwt_token = m.group(1) if m else ""
                break

        if not jwt_token:
            raise RuntimeError("CAS SSO 失败，CASTGC 可能已过期")

        # Step 2: JWT → sessionStorage token（需要浏览器）
        # 这一步需要 Playwright 加载 SPA 提取 sessionStorage
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            # 注入 CAS cookies
            for cookie in sess.cookies:
                ctx.add_cookies([{
                    "name": cookie.name, "value": cookie.value,
                    "domain": cookie.domain or "seat.lib.whu.edu.cn",
                    "path": cookie.path or "/",
                }])
            page = ctx.new_page()
            page.goto(f"{BASE_URL}/seat/#/login?token={jwt_token}", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=30000)

            # 提取
            token = ""
            for _ in range(80):
                token = page.evaluate("() => sessionStorage.getItem('jsq_p-token')")
                if token:
                    break
                time.sleep(0.15)

            hmac_key = ""
            raw = page.evaluate("() => sessionStorage.getItem('jsq_p-systemInfo')")
            if raw:
                hmac_key = json.loads(raw).get("hmacKey", "")

            browser.close()

        if not token:
            raise RuntimeError("未获取到 token")
        return cls(token, hmac_key)


# ============================================================
# 快速测试
# ============================================================
if __name__ == "__main__":
    print("""
使用方法:
    from library_api import LibrarySession

    # 用浏览器中提取的凭证
    sess = LibrarySession(
        token="从 sessionStorage['jsq_p-token'] 复制",
        hmac_key_encrypted="从 sessionStorage['jsq_p-systemInfo'].hmacKey 复制",
    )
    result = sess.get_seats("2026-07-10", "总馆")
    print(result)

    # 或用 CASTGC 自动获取
    sess = LibrarySession.from_castgc("TGT-112146--...")
""")
