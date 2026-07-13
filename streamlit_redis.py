"""
Streamlit ↔ Redis 集成：登录成功后写入凭证。

在 app.py 的 harvest 成功后调用:

    from streamlit_redis import save_to_redis
    sid = save_to_redis(
        castgc=st.session_state.cookies["castgc"],
        token=st.session_state.cookies["library_token"],
        hmac_key=st.session_state.cookies["library_hmac_key"],
    )
    st.sidebar.success(f"MCP session_id: `{sid}`")
"""

import os
from redis_store import CredStore

_store = CredStore(redis_url=os.getenv("REDIS_URL", "redis://localhost:6379"))


def save_to_redis(castgc: str, token: str, hmac_key: str,
                  student_id: str = "") -> str:
    """登录成功后保存到 Redis，返回 session_id。"""
    return _store.save(
        castgc=castgc,
        token=token,
        hmac_key=hmac_key,
        student_id=student_id,
    )
