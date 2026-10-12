"""Run pytest with all temporary files on the project drive."""
import importlib.util
import os
from pathlib import Path
import sys
import sysconfig

ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0,str(ROOT))
temporary=ROOT/'.runtime/tmp'
temporary.mkdir(parents=True,exist_ok=True)
os.environ['TEMP']=os.environ['TMP']=str(temporary)
# Some Windows Python installations contain an unrelated top-level py.py.
# Load pytest's own compatibility shim explicitly without changing that installation.
shim=Path(sysconfig.get_path('purelib'))/'py.py'
if shim.exists():
    spec=importlib.util.spec_from_file_location('py',shim)
    module=importlib.util.module_from_spec(spec)
    sys.modules['py']=module
    spec.loader.exec_module(module)
import pytest
raise SystemExit(pytest.main(sys.argv[1:] or ['-q','--basetemp=.runtime/pytest-tmp','--junitxml=test-results/pytest.xml']))
