"""MCP 客户端测试脚本 — 模拟 Claude Code 调用你的 MCP 服务器"""
import asyncio
import json
from mcp.client.sse import sse_client
from mcp import ClientSession

async def main():
    print(">>> 连接 MCP 服务器 http://localhost:8000/sse ...")
    async with sse_client("http://localhost:8000/sse") as (read, write):
        async with ClientSession(read, write) as session:
            # 1. Initialize
            await session.initialize()
            print("    ✅ Initialize")

            # 2. List tools
            tools = await session.list_tools()
            print(f"    ✅ tools/list: {len(tools.tools)} 个工具")
            for t in tools.tools:
                print(f"       - {t.name}")

            # 3. Call get_seats (session_id 已存在 Redis 中)
            print("\n>>> 调用 get_seats(工学分馆, 2026-07-13)...")
            result = await session.call_tool("get_seats", {
                "session_id": "19283de0-eeb",
                "date": "2026-07-13",
                "library": "工学分馆",
            })
            print("    ✅ tools/call 成功!")
            text = result.content[0].text if result.content else ""
            # 只展示前几行
            for line in text.split("\n")[:8]:
                print(f"    {line[:130]}")
            if len(text.split("\n")) > 8:
                print(f"    ... (共 {len(text)} 字符)")

            # 4. 试试 get_current_usage
            print("\n>>> 调用 get_current_usage...")
            r2 = await session.call_tool("get_current_usage", {
                "session_id": "19283de0-eeb",
            })
            print(f"    {r2.content[0].text[:200]}")

    print("\n============================================")
    print("MCP 全链路测试通过!")
    print("SSE → Initialize → tools/list → tools/call ✅")
    print("============================================")

asyncio.run(main())
