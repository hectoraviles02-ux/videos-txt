# Arranque al iniciar sesion (operador, 23-oct-2026: «que arranque de inicio»). No es tarea programada:
# lo lanza un acceso en la carpeta Inicio de Windows. Levanta Ollama, el bot y la fabrica si no estan ya corriendo.
# La fabrica solo produce de 19:00 a 07:00 (HORARIO_FABRICA en scripts\fabrica_continua.py); fuera de horario espera.
$log = "C:\VIDEOS-TXT\logs\arranque_inicio.log"
function Log($m) { Add-Content -Path $log -Value ("{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) -Encoding utf8 }
function Corre($patron) {
    @(Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object { $_.CommandLine -match $patron }).Count -gt 0
}
function OllamaVivo {
    try { Invoke-WebRequest -UseBasicParsing http://localhost:11434/api/tags -TimeoutSec 5 | Out-Null; $true } catch { $false }
}

Log "inicio de sesion: arranque"
Start-Sleep 60   # deja que la red y el acceso de Ollama de la carpeta Inicio levanten primero
if (-not (OllamaVivo)) {
    Start-Process -WindowStyle Hidden -FilePath "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" -ArgumentList "serve"
    Log "Ollama no respondia: lo arranque"
}
for ($i = 0; $i -lt 40 -and -not (OllamaVivo); $i++) { Start-Sleep 3 }
Log ("Ollama responde: {0}" -f (OllamaVivo))

if (Corre 'telegram_bot\.bot') { Log "bot ya estaba corriendo" }
else { Start-Process -WindowStyle Hidden -FilePath "C:\VIDEOS-TXT\scripts\arrancar_bot.cmd"; Log "bot arrancado" }

if (Corre 'fabrica_continua\.py') { Log "fabrica ya estaba corriendo" }
else { Start-Process -WindowStyle Hidden -FilePath "C:\VIDEOS-TXT\scripts\arrancar_fabrica.cmd"; Log "fabrica arrancada" }
