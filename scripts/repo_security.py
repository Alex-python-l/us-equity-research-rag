"""Check tracked paths without printing credentials or file contents."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def check():
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode('utf-8').split('\0')
    secrets = []
    for file in (ROOT / 'apikey.env', ROOT / 'tech_rag/.env', ROOT / 'financial_rag/.env'):
        if file.is_file():
            content = file.read_text(encoding='utf-8-sig')
            secrets.extend(x.encode() for x in re.findall(r'\bsk-[A-Za-z0-9_-]{16,}', content))
            for match in re.finditer(r'(?im)^[ \t]*([A-Z_0-9]*(?:KEY|PASSWORD|TOKEN))[ \t]*=[ \t]*([^\r\n]*)', content):
                value = match[2].strip().strip('\"\'')
                if len(value) >= 8:
                    secrets.append(value.encode())
    bad = []
    for name in filter(None, paths):
        p = ROOT / name
        if p.is_dir():
            continue
        if p.name in ('.env', 'apikey.env', '.env.enterprise') or any(x in p.parts for x in ('.venv', 'state', 'raw', 'text', 'output', '__pycache__')):
            bad.append((name, 'excluded runtime/config path'))
        data = p.read_bytes()
        if len(data) > 5_000_000:
            bad.append((name, 'large source blob'))
        if any(value in data for value in secrets):
            bad.append((name, 'local credential match'))
    for name, reason in bad:
        print(f'{reason}: {name}')
    if bad:
        raise SystemExit(1)
    print(f'Checked {len(list(filter(None, paths)))} source paths; no local credential matches or runtime files')

if __name__ == '__main__':
    check()
