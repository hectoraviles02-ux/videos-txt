@echo off
REM Arranca el bot de Telegram desde la raiz del repo (lo usa la tarea "VIDEOS_TXT_Bot").
cd /d C:\VIDEOS-TXT
set PYTHONIOENCODING=utf-8
python -m telegram_bot.bot >> C:\VIDEOS-TXT\logs\_bot_run_tarea.log 2>&1
