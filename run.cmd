@echo off
cd /d G:\Projects\quota-hub
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
echo Open http://127.0.0.1:8787
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8787
