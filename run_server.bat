@echo off
setlocal
REM Vse poti so relativne na lokacijo te datoteke — deluje, kjerkoli je repo.
set "ROOT=%~dp0"

echo Zagon vseh storitev...

REM ---- 1) Backend (uvicorn, port 8000) ----
start "backend" cmd /k "cd /d %ROOT% && venv\Scripts\activate && uvicorn api:app --host 0.0.0.0 --port 8000 --reload"

REM ---- 2) Frontend (Vite, port 5173) ----
start "frontend" cmd /k "cd /d %ROOT%frontend && npm run dev -- --host 0.0.0.0 --port 5173"

REM ---- 3) ngrok tunel na frontend (Vite proxy /api -> backend:8000) ----
where ngrok >nul 2>nul
if errorlevel 1 (
    echo [OPOZORILO] ngrok ni najden v PATH — tunel preskocen.
    echo            Namesti: https://ngrok.com/download  ali  choco install ngrok
) else (
    start "ngrok" cmd /k "ngrok http 5173"
)

echo.
echo Vse storitve zagnane v locenih oknih.
echo Javni URL najdes v ngrok oknu (Forwarding vrstica).
pause
