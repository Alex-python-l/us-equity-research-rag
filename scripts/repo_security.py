"""Check tracked paths without printing credentials or file contents."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def check():
    entries=subprocess.check_output(['git','ls-files','--stage','-z'],cwd=ROOT).decode('utf-8').split('\0')
    blobs=[]
    for entry in filter(None,entries):
        meta,name=entry.split('\t',1);mode,oid,stage=meta.split()
        if mode=='160000':continue
        if stage!='0':raise SystemExit('Unmerged index; resolve before publishing')
        blobs.append((name,oid))
    secrets = []
    for file in (ROOT / 'apikey.env', ROOT / 'tech_rag/.env', ROOT / 'financial_rag/.env'):
        if file.is_file():
            content = file.read_text(encoding='utf-8-sig')
            secrets.extend(x.encode() for x in re.findall(r'\bsk-[A-Za-z0-9_-]{16,}', content))
            secrets.extend(x.encode() for x in re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',content))
            for match in re.finditer(r'(?im)^[ \t]*([A-Z_0-9]*(?:KEY|PASSWORD|TOKEN))[ \t]*=[ \t]*([^\r\n]*)', content):
                value = match[2].strip().strip('\"\'')
                if len(value) >= 8:
                    secrets.append(value.encode())
    bad = []
    for name,oid in blobs:
        p = ROOT / name
        if p.name in ('.env', 'apikey.env', '.env.enterprise') or any(x in Path(name).parts for x in ('.venv', 'state', 'raw', 'text', 'output', '__pycache__')):
            bad.append((name, 'excluded runtime/config path'))
        # Inspect the exact indexed blob, which is what a subsequent commit publishes.
        data=subprocess.check_output(['git','cat-file','blob',oid],cwd=ROOT)
        if len(data) > 5_000_000:
            bad.append((name, 'large source blob'))
        if any(value in data for value in secrets):
            bad.append((name, 'local credential/private contact match'))
        if re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',data):
            bad.append((name,'private key material'))
        if re.search(rb'\bgh[pousr]_[A-Za-z0-9]{30,}\b',data):
            bad.append((name,'GitHub token pattern'))
    for name, reason in bad:
        print(f'{reason}: {name}')
    if bad:
        raise SystemExit(1)
    print(f'Checked {len(blobs)} indexed source blobs; no local credential matches or runtime files')

if __name__ == '__main__':
    check()
