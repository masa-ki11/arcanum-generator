@echo off
rem Launch with this project's own Python 3.14 (.venv).
rem Note: keep this file ASCII-only. cmd.exe reads .bat in the OEM codepage
rem (cp932 on Japanese Windows) and mangles UTF-8 text.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo [ERROR] .venv not found. See "kankyou kouchiku" section in README.md
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" main.py
