@echo off
rem start-job-agent.cmd — start the AI Job Agent bot from anywhere.
rem Usage:  start-job-agent          (poll from POLL_INTERVAL_MIN or 15)
rem         start-job-agent 5       (poll every 5 minutes)
rem         start-job-agent 0       (no auto-poll; manual /scan only)
setlocal
set "APP_DIR=F:\Email_Automation"
cd /d "%APP_DIR%"
set "PY=%APP_DIR%\emailvenv\Scripts\python.exe"
rem If no arg given, run with no --interval-flag so runner uses POLL_INTERVAL_MIN.
if "%~1"=="" (
    echo Starting AI Job Agent (poll interval: from POLL_INTERVAL_MIN / 15) from %APP_DIR%
    "%PY%" runner.py
) else (
    echo Starting AI Job Agent (poll interval=%~1 min) from %APP_DIR%
    "%PY%" runner.py --interval %~1
)
endlocal