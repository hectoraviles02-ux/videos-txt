# Asegura que Ollama use CUDA (25-oct-2026). Medido: Ollama 0.40.9 a veces cae a Vulkan al arrancar en frío porque la
# detección de CUDA se agota («GPU discovery watchdog timed out»). Con Vulkan el 14B usa 8.9 GB de RAM (con CUDA 3.7) y
# el 7B va a ~30 tok/s (con CUDA 37). Ver laboratorio\ollama_gpu_20261025\INFORME_OLLAMA_GPU.md.
#
# Qué hace: lee la última detección de server.log. Si es CUDA, no hace nada. Si es Vulkan:
#   - si hay un modelo cargado (alguien lo está usando), NO reinicia: solo lo anota;
#   - si ya reintentó en este arranque de Windows, NO reintenta otra vez (un solo reintento por arranque);
#   - si no, reinicia Ollama una vez y espera la nueva detección (hasta 5 min).
# No cambia configuración, variables, drivers ni modelos. Registro: C:\VIDEOS-TXT\logs\asegurar_ollama_cuda.log
# Uso: powershell -NoProfile -File asegurar_ollama_cuda.ps1 [-LogPath <server.log de prueba>] [-SoloRevisar]
param(
    [string]$LogPath = "$env:LOCALAPPDATA\Ollama\server.log",
    [switch]$SoloRevisar
)
$Registro = 'C:\VIDEOS-TXT\logs\asegurar_ollama_cuda.log'
$Marca = 'C:\VIDEOS-TXT\logs\asegurar_ollama_cuda.reintento'
$App = "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"

function Anota([string]$m) {
    $l = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $m
    Write-Output $l
    Add-Content -Path $Registro -Value $l -Encoding UTF8
}

function UltimaDeteccion([string]$ruta) {
    $l = Select-String -Path $ruta -Pattern 'msg="inference compute"' -EA SilentlyContinue |
         ? { $_.Line -notmatch 'type=integrated' } | Select-Object -Last 1
    if (-not $l) { return $null }
    if ($l.Line -match 'library=(\w+)') { return $Matches[1] }
    return $null
}

$lib = UltimaDeteccion $LogPath
if (-not $lib) { Anota "sin detección en $LogPath (¿Ollama no ha arrancado?): no hago nada"; exit 0 }
if ($lib -eq 'CUDA') { Anota "OK: Ollama usa CUDA"; exit 0 }

Anota "AVISO: Ollama usa $lib, no CUDA"
if ($SoloRevisar) { Anota "modo -SoloRevisar: no reinicio"; exit 1 }

try { $ps = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/ps' -TimeoutSec 10 } catch { $ps = $null }
if ($ps -and @($ps.models).Count -gt 0) {
    Anota "hay $(@($ps.models).Count) modelo(s) cargado(s): no reinicio para no cortar un trabajo; se queda en $lib"
    exit 1
}

$arranque = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('yyyy-MM-dd HH:mm:ss')
if ((Test-Path $Marca) -and ((Get-Content $Marca -Raw).Trim() -eq $arranque)) {
    Anota "ya reintenté en este arranque de Windows ($arranque): no reintento otra vez; se queda en $lib"
    exit 1
}
Set-Content -Path $Marca -Value $arranque -Encoding UTF8

Anota "reinicio Ollama una vez (bibliotecas CUDA ya en memoria)"
Get-Process 'ollama app', 'ollama' -EA SilentlyContinue | Stop-Process -Force
Start-Sleep 3
Start-Process $App
$t0 = Get-Date
do {
    Start-Sleep 5
    $nueva = Select-String -Path $LogPath -Pattern 'msg="inference compute"' -EA SilentlyContinue |
             ? { $_.Line -notmatch 'type=integrated' -and $_.Line -match 'time=(\S+)' -and [datetime]$Matches[1] -gt $t0 } |
             Select-Object -Last 1
} until ($nueva -or ((Get-Date) - $t0).TotalSeconds -gt 300)
if ($nueva -and $nueva.Line -match 'library=CUDA') { Anota "OK tras reintento: Ollama usa CUDA"; exit 0 }
Anota "tras el reintento sigue sin CUDA ($(if($nueva){'detección: ' + ($nueva.Line -replace '.*library=(\w+).*','$1')}else{'sin detección en 5 min'})); revisar a mano"
exit 1
