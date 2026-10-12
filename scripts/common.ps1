$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:PYTHONUTF8 = '1'
$env:TEMP = Join-Path $ProjectRoot '.runtime\tmp'
$env:TMP = $env:TEMP
$env:npm_config_cache = Join-Path $ProjectRoot '.runtime\npm-cache'
$env:PIP_NO_CACHE_DIR = '1'
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$NodeDirectory = Join-Path $ProjectRoot '.tools\node-v22.23.3-win-x64'
$env:PATH = "$NodeDirectory;$env:PATH"
function Check-Exit([string] $Task) {
    if ($LASTEXITCODE -ne 0) { throw "$Task failed (exit $LASTEXITCODE)." }
}
