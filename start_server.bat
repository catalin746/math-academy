@echo off
setlocal
cd /d %~dp0
py -m pip install -r requirements.txt
py -m uvicorn server:app --host 127.0.0.1 --port 8000
