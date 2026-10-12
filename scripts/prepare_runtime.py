"""Install an isolated, checksum-verified Node runtime; do not alter global Node."""
import hashlib
from pathlib import Path
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
VERSION='22.23.3'
SHA256='2b0ff57b049cda1bbcea2240eec20467018713c1efe1f7360c2681859b90ed71'
tools=ROOT/'.tools'
tools.mkdir(exist_ok=True)
directory=tools/f'node-v{VERSION}-win-x64'
if not (directory/'node.exe').exists():
    archive=tools/f'node-v{VERSION}-win-x64.zip'
    if not archive.exists() or hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()!=SHA256:
        urllib.request.urlretrieve(f'https://nodejs.org/dist/v{VERSION}/node-v{VERSION}-win-x64.zip',archive)
    with archive.open('rb') as source:
        if hashlib.file_digest(source,'sha256').hexdigest()!=SHA256:
            raise RuntimeError('Node SHA-256 verification failed; refusing extraction')
    with zipfile.ZipFile(archive) as source:
        source.extractall(tools)
print(f'Project Node {VERSION} ready.')
