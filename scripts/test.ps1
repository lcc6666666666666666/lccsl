. "$PSScriptRoot\common.ps1"
& $Python scripts/run_tests.py -q --basetemp=.runtime/pytest-tmp --junitxml=test-results/pytest.xml --cov=backend/app --cov-report=html:test-results/coverage --cov-report=term
Check-Exit 'MySQL test suite'
& "$NodeDirectory\npm.cmd" run typecheck --prefix frontend
Check-Exit 'Frontend type check'
