@echo off
REM Arranca la fabrica continua (scripts/fabrica_continua.py). No es tarea programada.
REM Detener: crear C:\VIDEOS-TXT\logs\DETENER_FABRICA (termina al acabar la pieza en curso).
cd /d C:\VIDEOS-TXT
set PYTHONIOENCODING=utf-8
del /q C:\VIDEOS-TXT\logs\DETENER_FABRICA 2>nul
python scripts\fabrica_continua.py >> C:\VIDEOS-TXT\logs\fabrica_continua_salida.log 2>&1
