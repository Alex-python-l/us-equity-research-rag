"""Restore tracked local patches without changing the upstream pin."""
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'tech_rag/vendor/TradingAgents'
PIN = 'e8f5503e9b86d5c2710b0c76b1f686703968c9df'

def restore():
    def git(*args, check=True):
        return subprocess.run(['git', '-C', str(VENDOR), *args], capture_output=True, text=True, check=check)
    if git('rev-parse', 'HEAD').stdout.strip() != PIN:
        raise RuntimeError('Upstream commit mismatch; no changes applied')
    patches = ROOT / 'scripts/vendor_patches'
    manifest = json.loads((patches / 'manifest.json').read_text(encoding='utf-8'))
    for row in manifest['patches']:
        path = patches / row['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise RuntimeError('Patch hash mismatch')
        if git('apply', '--reverse', '--check', str(path), check=False).returncode == 0:
            continue
        git('apply', '--check', str(path))
        git('apply', str(path))
    print('Pinned framework patches verified')

if __name__ == '__main__':
    restore()
