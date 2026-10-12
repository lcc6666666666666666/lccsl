. "$PSScriptRoot\common.ps1"
& "$NodeDirectory\npm.cmd" run build --prefix frontend
Check-Exit 'Build frontend'
