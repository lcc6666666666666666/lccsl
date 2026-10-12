. "$PSScriptRoot\common.ps1"
New-Item -ItemType Directory -Force test-results | Out-Null
& $Python -m pip_audit --format=json --output=test-results/pip-audit.json --cache-dir=.runtime/audit-cache
Check-Exit 'Python vulnerability audit'
& "$NodeDirectory\npm.cmd" audit --prefix frontend --registry=https://registry.npmjs.org --json | Out-File test-results/npm-audit.json -Encoding utf8
Check-Exit 'Frontend vulnerability audit'
