$ErrorActionPreference = 'Stop'
$ProgressPreference = 'Continue'

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeRoot = Join-Path $Root 'runtime\llama.cpp'
$ModelsRoot = Join-Path $Root 'models'
$CacheRoot = Join-Path $Root 'cache\installer'
$ModelName = 'Qwen3-4B-Q4_K_M.gguf'
$ModelPath = Join-Path $ModelsRoot $ModelName
$ModelUrl = 'https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf?download=true'

New-Item -ItemType Directory -Force -Path $RuntimeRoot, $ModelsRoot, $CacheRoot | Out-Null

function Download-File {
    param(
        [Parameter(Mandatory=$true)][string]$Url,
        [Parameter(Mandatory=$true)][string]$Destination
    )
    if (Test-Path $Destination) {
        Write-Host "[ROB] Ya existe: $Destination"
        return
    }
    Write-Host "[ROB] Descargando: $Url"
    $partial = "$Destination.partial"
    Remove-Item -Force -ErrorAction SilentlyContinue $partial
    try {
        Start-BitsTransfer -Source $Url -Destination $partial -DisplayName 'ROB Genealogy Lab' -Description 'Descargando componente local'
    }
    catch {
        Write-Host '[ROB] BITS no disponible; usando Invoke-WebRequest...'
        Invoke-WebRequest -Uri $Url -OutFile $partial -UseBasicParsing
    }
    Move-Item -Force $partial $Destination
}

Write-Host ''
Write-Host 'ROB Genealogy Lab — instalacion explicita de IA local'
Write-Host '----------------------------------------------------'
Write-Host "Todo se guardara dentro de: $Root"
Write-Host 'No se configura ninguna API de pago.'
Write-Host 'Modelo inicial: Qwen3-4B Q4_K_M (GGUF).'
Write-Host 'Runtime inicial: llama.cpp para Windows x64 con Vulkan.'
Write-Host ''

# Resolve the current official llama.cpp Windows Vulkan package dynamically.
$Release = Invoke-RestMethod -Uri 'https://api.github.com/repos/ggml-org/llama.cpp/releases/latest' -Headers @{ 'User-Agent' = 'ROB-Genealogy-Lab' }
$Asset = $Release.assets | Where-Object { $_.name -match '^llama-.*-bin-win-vulkan-x64\.zip$' } | Select-Object -First 1
if (-not $Asset) {
    throw 'No se encontro el paquete oficial Windows x64 Vulkan de llama.cpp en la ultima version.'
}

$RuntimeZip = Join-Path $CacheRoot $Asset.name
Download-File -Url $Asset.browser_download_url -Destination $RuntimeZip

$Marker = Join-Path $RuntimeRoot '.installed-release.txt'
$InstalledTag = if (Test-Path $Marker) { (Get-Content $Marker -Raw).Trim() } else { '' }
if ($InstalledTag -ne $Release.tag_name -or -not (Get-ChildItem -Path $RuntimeRoot -Filter 'llama-server.exe' -Recurse -ErrorAction SilentlyContinue)) {
    Write-Host "[ROB] Extrayendo llama.cpp $($Release.tag_name)..."
    Get-ChildItem -Path $RuntimeRoot -Force | Remove-Item -Recurse -Force
    Expand-Archive -Path $RuntimeZip -DestinationPath $RuntimeRoot -Force
    Set-Content -Path $Marker -Value $Release.tag_name -Encoding UTF8
}
else {
    Write-Host "[ROB] llama.cpp $InstalledTag ya esta instalado."
}

Download-File -Url $ModelUrl -Destination $ModelPath

$Server = Get-ChildItem -Path $RuntimeRoot -Filter 'llama-server.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $Server) {
    throw 'La instalacion termino sin encontrar llama-server.exe.'
}
if (-not (Test-Path $ModelPath)) {
    throw 'La instalacion termino sin encontrar el modelo GGUF.'
}

$ModelSizeGB = [Math]::Round((Get-Item $ModelPath).Length / 1GB, 2)
Write-Host ''
Write-Host '[ROB] IA local preparada.'
Write-Host "[ROB] llama-server: $($Server.FullName)"
Write-Host "[ROB] modelo: $ModelPath ($ModelSizeGB GB)"
Write-Host '[ROB] No se ha creado ninguna cuenta ni clave de API.'
Write-Host '[ROB] Ya puedes iniciar ROB Genealogy Lab.'
Write-Host ''
