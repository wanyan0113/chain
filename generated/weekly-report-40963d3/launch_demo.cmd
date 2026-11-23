@echo off
chcp 65001 >nul
"%~dp0..\..\.venv\Scripts\python.exe" -B "%~dp0demo_server.py" --open
pause
