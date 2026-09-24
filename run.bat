@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Creating virtual environment...
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
echo.
echo  Opening Backlink Scout at http://127.0.0.1:7860
echo.
start "" http://127.0.0.1:7860
.venv\Scripts\python.exe -m backlink_checker.web
