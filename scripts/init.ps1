param([switch] $Isolated, [string] $Database = 'course_registration')
. "$PSScriptRoot\common.ps1"
if (-not (Test-Path -LiteralPath $Python)) {
    & py -3.12 -m venv .venv
    Check-Exit 'Create Python 3.12 virtual environment'
}
& $Python -m pip install --upgrade pip==26.2.1
Check-Exit 'Upgrade pip'
& $Python -m pip install -r requirements.lock
Check-Exit 'Install Python dependencies'
& $Python scripts/prepare_runtime.py
Check-Exit 'Prepare Node'
& "$NodeDirectory\npm.cmd" ci --prefix frontend
Check-Exit 'Install frontend dependencies'
if (-not (Test-Path -LiteralPath '.env')) {
    $Arguments = @('scripts/bootstrap.py', '--database', $Database)
    if ($Isolated) { $Arguments += '--isolated' }
    & $Python @Arguments
    Check-Exit 'Initialize project database'
}
& "$PSScriptRoot\build.ps1"
Write-Host 'Ready. Run scripts/start.ps1; credentials: .runtime/ (local only).'
