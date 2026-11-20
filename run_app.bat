@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [*] جاري تشغيل AUTO SILENCE CUTTER...
python desktop_app.py
