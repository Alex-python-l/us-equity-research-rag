"""Own only the server process started here; never kill an existing listener."""
from pathlib import Path
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import time
from urllib.request import ProxyHandler,build_opener

ROOT=Path(__file__).resolve().parents[1]

def healthy():
    try:
        with build_opener(ProxyHandler({})).open('http://127.0.0.1:8876/api/health',timeout=3) as response:
            value=json.load(response)
        if value.get('application')!='financial-research-workspace':raise RuntimeError('Port belongs to another application')
        return True
    except OSError:return False

def main():
    mutex=None
    if os.name=='nt':
        kernel=ctypes.windll.kernel32;kernel.CreateMutexW.restype=ctypes.c_void_p
        mutex=kernel.CreateMutexW(None,False,'Local\\USResearchRAG_'+hashlib.sha256(str(ROOT).encode()).hexdigest()[:16])
        if kernel.GetLastError()==183:return
    state=ROOT/'tech_rag/state';state.mkdir(parents=True,exist_ok=True)
    if healthy():return
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    for attempt in range(4):
        env={**os.environ,'PYTHONIOENCODING':'utf-8'}
        with (state/'background_stdout.log').open('ab') as out,(state/'background_stderr.log').open('ab') as err:
            child=subprocess.Popen([sys.executable,str(ROOT/'tech_rag/chat_server.py')],cwd=str(ROOT),env=env,
                stdin=subprocess.DEVNULL,stdout=out,stderr=err,creationflags=flags)
            code=child.wait()
        if code==0:return
        if attempt<3:time.sleep(30*(2**attempt))
    raise SystemExit('Background server failed after three restarts; inspect local logs')

if __name__=='__main__':main()
