param([switch] $Development)
. "$PSScriptRoot\common.ps1"
if ($Development) {
    & $Python scripts/manage.py dev
} else {
    if (-not (Test-Path -LiteralPath 'frontend/dist/index.html')) { & "$PSScriptRoot\build.ps1" }
    & $Python scripts/manage.py start
}
Check-Exit 'Start project'
