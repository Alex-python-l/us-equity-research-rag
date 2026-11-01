@echo off
cd /d "%~dp0"
"%~dp0tech_rag\integration\.venv\Scripts\python.exe" "%~dp0tech_rag\chat_launcher.py"
if errorlevel 1 pause
