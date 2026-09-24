@echo off
setlocal
set "ROOT=%~dp0"

echo Zagon vseh storitev...

start "backend" cmd /k "cd /d %ROOT% && venv\Scripts\activate && uvicorn api:app --host 0.0.0.0 --port 8000 --reload"

start "frontend" cmd /k "cd /d %ROOT%frontend && npm run dev -- --host 0.0.0.0 --port 5173"

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
