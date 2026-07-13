@echo off
title WHU Library MCP Server
cd /d %~dp0
python -X utf8 server.py --port 8000
pause
