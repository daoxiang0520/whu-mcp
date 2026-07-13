"""
Redis 凭证存储 — 在 Streamlit 和 MCP Server 之间共享 CASTGC/token。

Streamlit 登录 → 写入 Redis (set)
MCP Server 调用 → 从 Redis 读取 (get) + 自动续期
TTL 过期后 Redis 自动清理，无需手动维护。
"""
import json
import time
import uuid
from typing import Any

import redis


DEFAULT_TTL = 4 * 3600  # CASTGC 有效期约几小时


class CredStore:
    """图书馆凭证的 Redis 存取层。

    用法:
        store = CredStore(redis_url="redis://localhost:6379")
        sid = store.save(castgc="TGT-xxx", token="a0ee...", hmac_key="iME1...")
        creds = store.get(sid)
    """

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis = redis.Redis.from_url(redis_url, decode_responses=True)

    # ── session 级别 ──────────────────────────────────────

    def save(self, castgc: str, token: str, hmac_key: str,
             student_id: str = "", educational: str = "",
             ttl: int = DEFAULT_TTL) -> str:
        """保存凭证，返回 session_id。"""
        sid = str(uuid.uuid4())[:12]
        self.redis.setex(
            f"whu:session:{sid}",
            ttl,
            json.dumps({
                "castgc": castgc,
                "library_token": token,
                "library_hmac_key": hmac_key,
                "student_id": student_id,
                "educational": educational,
                "created_at": int(time.time()),
            }, ensure_ascii=False),
        )
        return sid

    def get(self, session_id: str) -> dict | None:
        """读取并自动续期。"""
        key = f"whu:session:{session_id}"
        raw = self.redis.get(key)
        if not raw:
            return None
        self.redis.expire(key, DEFAULT_TTL)
        return json.loads(raw)

    def delete(self, session_id: str) -> bool:
        return bool(self.redis.delete(f"whu:session:{session_id}"))

    def exists(self, session_id: str) -> bool:
        return bool(self.redis.exists(f"whu:session:{session_id}"))

    # ── 工具：列出所有活跃 session（管理用） ──────────────

    def list_sessions(self) -> list[dict]:
        """列出所有活跃 session 的元信息。"""
        sessions = []
        for key in self.redis.scan_iter("whu:session:*"):
            sid = key.split(":")[-1]
            raw = self.redis.get(key)
            if raw:
                data = json.loads(raw)
                sessions.append({
                    "session_id": sid,
                    "student_id": data.get("student_id", "?"),
                    "created_at": data.get("created_at", 0),
                    "ttl_remaining": self.redis.ttl(key),
                })
        return sessions
