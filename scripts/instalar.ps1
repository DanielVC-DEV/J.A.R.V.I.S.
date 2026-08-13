<#
.SYNOPSIS
    Prepara el entorno de desarrollo de JARVIS en un equipo nuevo.

.DESCRIPTION
    Comprueba los requisitos, crea el entorno virtual, instala las
    dependencias, prepara el archivo de configuración y ejecuta las pruebas
    para confirmar que la instalación quedó en buen estado.

    Es idempotente: puede ejecutarse varias veces sin causar daño. En
    particular, nunca sobrescribe un archivo .env existente, porque contiene
    la clave de API del usuario.

.PARAMETER LocalStt
    Instala además el motor de transcripción local (faster-whisper). Ocupa más
    de un gigabyte y solo merece la pena con una GPU NVIDIA.

.EXAMPLE
    .\scripts\instalar.ps1

.EXAMPLE
    .\scripts\instalar.ps1 -LocalStt
#>

[CmdletBinding()]
param(
    [switch]$LocalStt
)

$ErrorActionPreference = 'Stop'

function Write-Paso    { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Bien    { param($m) Write-Host "    $m" -ForegroundColor Green }
function Write-Aviso   { param($m) Write-Host "    $m" -ForegroundColor Yellow }
function Write-Detalle { param($m) Write-Host "    $m" -ForegroundColor DarkGray }

# El script vive en scripts\, de modo que la raíz del proyecto es su carpeta padre.
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

Write-Host "`nInstalación de JARVIS" -ForegroundColor White
Write-Detalle $raiz

# --------------------------------------------------------------------------- #
# 1. Requisitos
# --------------------------------------------------------------------------- #

Write-Paso "Comprobando Python 3.11"

$python = $null
foreach ($candidato in @('py -3.11', 'python3.11', 'python')) {
    try {
        $partes  = $candidato -split ' '
        $version = & $partes[0] $partes[1..($partes.Length - 1)] --version 2>&1
        if ($version -match 'Python 3\.(11|12)\.') {
            $python = $candidato
            Write-Bien "$version  ($candidato)"
            break
        }
    } catch { continue }
}

if (-not $python) {
    Write-Host "`nNo se encontró Python 3.11." -ForegroundColor Red
    Write-Host "Instálalo con:  winget install Python.Python.3.11" -ForegroundColor Red
    Write-Host "Después cierra esta consola, abre una nueva y vuelve a ejecutar." -ForegroundColor Red
    exit 1
}

# --------------------------------------------------------------------------- #
# 2. Entorno virtual
# --------------------------------------------------------------------------- #

Write-Paso "Preparando el entorno virtual"

if (Test-Path '.venv') {
    Write-Detalle "Ya existía, se reutiliza."
} else {
    $partes = $python -split ' '
    & $partes[0] $partes[1..($partes.Length - 1)] -m venv .venv
    Write-Bien "Creado en .venv"
}

$pip = Join-Path $raiz '.venv\Scripts\pip.exe'
$py  = Join-Path $raiz '.venv\Scripts\python.exe'

if (-not (Test-Path $py)) {
    Write-Host "`nEl entorno virtual quedó incompleto. Borra la carpeta .venv y reinténtalo." -ForegroundColor Red
    exit 1
}

# --------------------------------------------------------------------------- #
# 3. Dependencias
# --------------------------------------------------------------------------- #

Write-Paso "Instalando dependencias"
Write-Detalle "Puede tardar un par de minutos."

& $py -m pip install --upgrade pip --quiet
& $pip install -e ".[dev]" --quiet
Write-Bien "Dependencias de desarrollo instaladas."

if ($LocalStt) {
    Write-Paso "Instalando el motor de transcripción local"
    Write-Detalle "Más de un gigabyte; ten paciencia."
    & $pip install -e ".[local-stt]" --quiet
    Write-Bien "faster-whisper instalado."
}

# --------------------------------------------------------------------------- #
# 4. Configuración
# --------------------------------------------------------------------------- #

Write-Paso "Preparando la configuración"

if (Test-Path '.env') {
    Write-Detalle "Ya existe un .env; no se toca."
} else {
    Copy-Item '.env.example' '.env'
    Write-Bien "Creado .env a partir de la plantilla."
    Write-Aviso "Falta poner tu clave de API dentro."
}

# --------------------------------------------------------------------------- #
# 5. Comprobación
# --------------------------------------------------------------------------- #

Write-Paso "Ejecutando las pruebas"

& $py -m pytest -q
if ($LASTEXITCODE -ne 0) {
    Write-Host "`nAlgunas pruebas fallaron. La instalación no está en buen estado." -ForegroundColor Red
    exit 1
}
Write-Bien "Todas las pruebas pasaron."

# --------------------------------------------------------------------------- #
# 6. Siguientes pasos
# --------------------------------------------------------------------------- #

$tieneClave = (Select-String -Path '.env' -Pattern '^JARVIS_API_KEY=.+' -Quiet -ErrorAction SilentlyContinue)

Write-Host "`n" -NoNewline
Write-Host "Instalación completada." -ForegroundColor Green

if (-not $tieneClave) {
    Write-Host "`nFalta la clave de API:" -ForegroundColor Yellow
    Write-Host "  1. Consigue una gratis en https://console.groq.com/keys"
    Write-Host "  2. notepad .env"
    Write-Host "  3. Pégala en JARVIS_API_KEY="
    Write-Host "  4. Copia un modelo de la lista a JARVIS_MODEL="
}

Write-Host "`nPara empezar:" -ForegroundColor White
Write-Host "  .venv\Scripts\activate.ps1"
Write-Host "  python scripts\diagnostico.py     # comprueba la configuración"
Write-Host "  python main.py --verbose          # arranca el asistente"
Write-Host ""
