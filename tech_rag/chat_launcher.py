"""Open or start the local RAG-only window; reuse an existing healthy server."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener
import webbrowser

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:8876'


def running():
    try:
        with build_opener(ProxyHandler({})).open(URL + '/api/health', timeout=2) as response:
            state = json.load(response)
        if state.get('application') == 'financial-research-workspace':
            return True
        if state.get('rag_only') is not True or state.get('news_enabled') is not False:
            raise RuntimeError('Port 8876 is occupied by another application; no changes made.')
        raise RuntimeError('The old RAG server is still running on port 8876. Stop it only after explicit user authorization, then launch again to load the research workspace update.')
    except HTTPError as exc:
        raise RuntimeError('Port 8876 returned an unexpected HTTP response; no changes made.') from exc
    except URLError:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not running():
        state = ROOT / 'state'
        state.mkdir(exist_ok=True)
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0
        with (state / 'rag_chat_stdout.log').open('ab') as stdout, (state / 'rag_chat_stderr.log').open('ab') as stderr:
            process = subprocess.Popen([sys.executable, str(ROOT / 'chat_server.py'), '--port', '8876'],
                cwd=str(ROOT.parent), env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                creationflags=flags)
        for _ in range(40):
            if running():
                break
            if process.poll() is not None:
                raise RuntimeError('RAG server did not start; see tech_rag/state/rag_chat_stderr.log.')
            time.sleep(.25)
        else:
            raise RuntimeError('RAG server is not ready; see tech_rag/state/rag_chat_stderr.log.')
    print('RAG chat: ' + URL)
    if not args.no_browser:
        webbrowser.open(URL)


if __name__ == '__main__':
    main()
