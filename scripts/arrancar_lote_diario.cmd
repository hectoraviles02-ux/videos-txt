@echo off
REM Manda a Telegram el lote diario de 6 reels con boton APROBAR (lo usa la tarea "VIDEOS_TXT_Lote_Diario").
cd /d C:\VIDEOS-TXT
set PYTHONIOENCODING=utf-8
python -u scripts\enviar_lote_diario.py --n 6 >> logs\lote_diario.log 2>&1
