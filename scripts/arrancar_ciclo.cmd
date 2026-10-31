@echo off
REM Corre el ciclo diario desde la raiz del repo (lo usa la tarea "VIDEOS_TXT_Ciclo_Diario").
cd /d C:\VIDEOS-TXT
set PYTHONIOENCODING=utf-8
python -u scripts\ciclo_diario.py --n 4
