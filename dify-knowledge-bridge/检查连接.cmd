@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -c "import yaml" >nul 2>&1 || python -m pip install -r requirements.txt
python kb_bridge.py doctor
pause
