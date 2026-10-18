<#
.SYNOPSIS
  Instala JARVIS en Windows de forma idempotente: se puede ejecutar las veces que haga falta.

.DESCRIPTION
  1. Comprueba la versión de Python (3.11 a 3.14).
  2. Crea el venv si no existe e instala requirements.txt (torch con CUDA desde el índice de PyTorch).
  3. Comprueba ffmpeg, VLC, Node, Ollama y CUDA, y dice cómo instalar lo que falte.
  4. Descarga la voz de Piper y el modelo de Ollama si faltan (salvo con -SinDescargas).
  5. Crea la carpeta de secretos con una plantilla .env.example SIN valores y rutas.json con las
     carpetas reales de este usuario (Descargas, Escritorio, perfil), si no existe. Nunca lee,
     crea ni modifica .env, tokens ni credenciales.
  6. Si no hay .env, ofrece el asistente configurar.py (clave de Anthropic, sin mostrarla) salvo
     con -SinConfigurar.
  7. Ejecuta doctor.py y resume lo que falta.

  Uso (desde la carpeta del repo):
    .\instalar.cmd
    .\instalar.cmd -SinDescargas            # sin voz de Piper ni modelo de Ollama
    .\instalar.cmd -SinConfigurar           # sin preguntar por el asistente (instalación desatendida)
#>
param(
    [switch]$SinDescargas,
    [switch]$SinConfigurar,
    [string]$Secretos = $(if ($env:JARVIS_SECRETOS) { $env:JARVIS_SECRETOS } else { "C:\Jarvis-secrets" })
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$Raiz        = $PSScriptRoot
$Venv        = Join-Path $Raiz "venv"
$PythonVenv  = Join-Path $Venv "Scripts\python.exe"
$IndiceTorch = "https://download.pytorch.org/whl/cu128"
$Voz         = "es_ES-davefx-medium.onnx"
$UrlVoz      = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/es/es_ES/davefx/medium/$Voz"
$ModeloOllama = "qwen2.5:3b"
$Vlc         = "C:\Program Files\VideoLAN\VLC\vlc.exe"
$Pendiente   = New-Object System.Collections.Generic.List[string]

function Paso($texto)   { Write-Host "`n== $texto" -ForegroundColor Cyan }
function Bien($texto)   { Write-Host "   OK     $texto" -ForegroundColor Green }
function Aviso($texto, $accion) {
    Write-Host "   AVISO  $texto" -ForegroundColor Yellow
    if ($accion) { Write-Host "          -> $accion"; $Pendiente.Add("$texto -> $accion") }
}
function Existe($comando) { return [bool](Get-Command $comando -ErrorAction SilentlyContinue) }

# ── 1. Python ─────────────────────────────────────────────────────
Paso "Python"
$Python = $null; $PythonArgs = @()
foreach ($candidato in @("py -3", "python")) {
    $partes = $candidato -split " "
    $exe, $extra = $partes[0], @($partes | Select-Object -Skip 1)
    if (Existe $exe) {
        $version = & $exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version) { $Python = $exe; $PythonArgs = $extra; break }
    }
}
if (-not $Python) {
    Write-Host "   ERROR  No hay Python. Instálalo: winget install Python.Python.3.14 (marca 'Add to PATH')" -ForegroundColor Red
    exit 1
}
$mayor, $menor = $version.Split(".") | ForEach-Object { [int]$_ }
if ($mayor -ne 3 -or $menor -lt 11) {
    Write-Host "   ERROR  Python ${version}: hace falta 3.11 o posterior (probado con 3.14)" -ForegroundColor Red
    exit 1
}
if ($menor -gt 14) { Aviso "Python $version es más nuevo que el probado (3.14): puede que torch no tenga wheels" "Instala Python 3.14 si pip falla" }
else { Bien "Python $version" }

# ── 2. venv y dependencias ────────────────────────────────────────
Paso "Entorno virtual y dependencias"
if (Test-Path $PythonVenv) { Bien "venv ya existe" }
else {
    & $Python @PythonArgs -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Write-Host "   ERROR  No se pudo crear el venv" -ForegroundColor Red; exit 1 }
    Bien "venv creado"
}
& $PythonVenv -m pip install --disable-pip-version-check -q --upgrade pip
# pip no reinstala lo que ya está en su versión: repetirlo es rápido
$Requisitos = Join-Path $Raiz "requirements.txt"
& $PythonVenv -m pip install --disable-pip-version-check -q -r $Requisitos --extra-index-url $IndiceTorch
if ($LASTEXITCODE -ne 0) {
    # Una caché de pip dañada o sin permisos también hace fallar la instalación: se reintenta sin ella
    Aviso "pip falló; se reintenta sin la caché de pip (descarga todo de nuevo)" ""
    & $PythonVenv -m pip install --disable-pip-version-check -q --no-cache-dir -r $Requisitos --extra-index-url $IndiceTorch
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "   ERROR  pip no pudo instalar requirements.txt (mira el error de arriba)" -ForegroundColor Red
    exit 1
}
Bien "requirements.txt instalado"

# ── 3. Programas externos ─────────────────────────────────────────
Paso "Programas externos"
if (Existe "ffmpeg") { Bien "ffmpeg" } else { Aviso "Falta ffmpeg (Whisper lo necesita para leer el audio)" "winget install Gyan.FFmpeg y abre una consola nueva" }
if (Test-Path $Vlc) { Bien "VLC" } else { Aviso "Falta VLC (música)" "winget install VideoLAN.VLC" }
if (Existe "node") {
    Bien "Node $(node -v)"
    $App = Join-Path $Raiz "app"
    if (Test-Path (Join-Path $App "node_modules")) { Bien "UI: dependencias de app/ ya instaladas" }
    elseif (Test-Path (Join-Path $App "package.json")) {
        Push-Location $App
        try { npm install --no-fund --no-audit --loglevel=error; if ($LASTEXITCODE -eq 0) { Bien "UI: npm install en app/" } else { Aviso "npm install falló en app/" "cd app; npm install" } }
        finally { Pop-Location }
    }
} else { Aviso "Falta Node.js (solo para la UI de escritorio; JARVIS funciona sin ella)" "winget install OpenJS.NodeJS.LTS" }
if (Existe "nvidia-smi") { Bien "GPU NVIDIA (Whisper usará CUDA)" }
else { Aviso "Sin GPU NVIDIA o sin driver: Whisper irá en CPU (más lento, funciona)" "" }

# ── 4. Voz de Piper y modelo de Ollama ───────────────────────────
Paso "Modelos"
$RutaVoz = Join-Path $Raiz "voices\$Voz"
if (Test-Path $RutaVoz) { Bien "Voz de Piper ($Voz)" }
elseif ($SinDescargas) { Aviso "Falta la voz de Piper" "Descárgala de $UrlVoz a voices\" }
else {
    try {
        $tmp = "$RutaVoz.descargando"
        Invoke-WebRequest -Uri $UrlVoz -OutFile $tmp -UseBasicParsing
        Move-Item -Force $tmp $RutaVoz
        Bien "Voz de Piper descargada"
    } catch {
        Remove-Item -ErrorAction SilentlyContinue "$RutaVoz.descargando"
        Aviso "No se pudo descargar la voz de Piper" "Descárgala a mano de $UrlVoz a voices\"
    }
}
# Ollama puede estar instalado sin estar en el PATH de esta consola: su ruta por defecto
$Ollama = if (Existe "ollama") { "ollama" } else {
    $porDefecto = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $porDefecto) { $porDefecto } else { $null }
}
if ($Ollama) {
    $modelos = (& $Ollama list 2>$null) -join "`n"
    if ($modelos -match [regex]::Escape($ModeloOllama)) { Bien "Ollama con $ModeloOllama" }
    elseif ($SinDescargas) { Aviso "Ollama sin el modelo $ModeloOllama" "ollama pull $ModeloOllama" }
    else {
        & $Ollama pull $ModeloOllama
        if ($LASTEXITCODE -eq 0) { Bien "Modelo $ModeloOllama descargado" } else { Aviso "ollama pull falló (¿está arrancado Ollama?)" "ollama pull $ModeloOllama" }
    }
} else {
    # Sin CLI en Windows puede haber un Ollama accesible (p. ej. en WSL): se pregunta a su API
    $api = $null
    try { $api = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3 } catch { }
    if ($api -and ($api.models.name -contains $ModeloOllama)) { Bien "Ollama (API en 127.0.0.1:11434) con $ModeloOllama" }
    elseif ($api) { Aviso "Ollama responde pero no tiene $ModeloOllama" "ollama pull $ModeloOllama (donde esté instalado Ollama)" }
    else { Aviso "Falta Ollama (respuestas locales; sin él todo va a Claude)" "winget install Ollama.Ollama y luego: ollama pull $ModeloOllama" }
}

# ── 5. Carpeta de secretos, plantilla y rutas de este usuario ─────
Paso "Rutas"
Bien "Repo en $((Get-Item $Raiz).FullName) (cualquier carpeta vale: las rutas salen de rutas.py)"
if ($Secretos -ine "C:\Jarvis-secrets" -and $Secretos -ine $env:JARVIS_SECRETOS) {
    Aviso "La carpeta de secretos no es la de por defecto" "Define la variable de entorno de Windows JARVIS_SECRETOS=$Secretos"
}
$env:JARVIS_SECRETOS = $Secretos   # el doctor de abajo mira esta carpeta

if (Test-Path $Secretos) { Bien "Carpeta de secretos $Secretos" }
else { New-Item -ItemType Directory -Path $Secretos | Out-Null; Bien "Creada la carpeta de secretos $Secretos" }
$Plantilla = Join-Path $Secretos ".env.example"
if (Test-Path $Plantilla) { Bien ".env.example ya existe (no se toca)" }
else {
    Copy-Item (Join-Path $Raiz "env.example") $Plantilla
    Bien "Plantilla .env.example creada (sin valores)"
}
# rutas.json: las carpetas reales del usuario de Windows (no es un secreto; es de esta máquina)
$RutasJson = Join-Path $Secretos "rutas.json"
if (Test-Path $RutasJson) { Bien "rutas.json ya existe (no se toca)" }
else {
    $descargas = $null
    try { $descargas = (New-Object -ComObject Shell.Application).NameSpace("shell:Downloads").Self.Path } catch { }
    if (-not $descargas) { $descargas = Join-Path $env:USERPROFILE "Downloads" }
    $rutasUsuario = [ordered]@{
        descargas  = $descargas
        escritorio = [Environment]::GetFolderPath("Desktop")
        home       = $env:USERPROFILE
    }
    [System.IO.File]::WriteAllText($RutasJson, ($rutasUsuario | ConvertTo-Json), (New-Object System.Text.UTF8Encoding $false))
    Bien "rutas.json creado: Descargas = $descargas"
}
# ── 6. Asistente de configuración ────────────────────────────────
# configurar.py pide la clave sin mostrarla y escribe el .env; este script nunca lo toca
$Configurar = "venv\Scripts\python.exe configurar.py"
if (Test-Path (Join-Path $Secretos ".env")) {
    Bien ".env ya existe. Para cambiar la clave o ver los servicios opcionales: $Configurar"
} elseif ($SinConfigurar -or -not [Environment]::UserInteractive) {
    Aviso "No hay .env (falta la clave de Anthropic)" $Configurar
} else {
    Paso "Configuración"
    $respuesta = Read-Host "   ¿Abrir ahora el asistente de configuración (clave de Anthropic)? [S/n]"
    if ($respuesta -match '^\s*(n|no)\s*$') { Aviso "No hay .env (falta la clave de Anthropic)" $Configurar }
    else {
        & $PythonVenv (Join-Path $Raiz "configurar.py")
        if ($LASTEXITCODE -ne 0) { Aviso "El asistente no ha guardado la clave de Anthropic" $Configurar }
    }
}

# ── 7. Diagnóstico ────────────────────────────────────────────────
Paso "Diagnóstico (doctor.py)"
$json = & $PythonVenv (Join-Path $Raiz "doctor.py") --json 2>$null
try {
    $doctor = ($json -join "`n") | ConvertFrom-Json
    foreach ($c in $doctor.comprobaciones) {
        if ($c.estado -ne "OK") {
            $linea = "[$($c.estado)] $($c.nombre): $($c.detalle)"
            if ($c.accion) { $linea += " -> $($c.accion)" }
            $Pendiente.Add($linea)
        }
    }
    Write-Host "   doctor: $($doctor.estado) ($($doctor.resumen.OK) OK, $($doctor.resumen.AVISO) AVISO, $($doctor.resumen.ERROR) ERROR)"
} catch {
    Aviso "doctor.py no devolvió un resultado legible" "venv\Scripts\python.exe doctor.py"
}

Write-Host ""
if ($Pendiente.Count -eq 0) {
    Write-Host "Todo listo. Arranca JARVIS con: venv\Scripts\python.exe jarvis.py" -ForegroundColor Green
} else {
    Write-Host "Falta por hacer ($($Pendiente.Count)):" -ForegroundColor Yellow
    $Pendiente | Select-Object -Unique | ForEach-Object { Write-Host "  - $_" }
    Write-Host "Vuelve a ejecutar .\instalar.cmd cuando lo arregles: no repite lo que ya está hecho."
}
