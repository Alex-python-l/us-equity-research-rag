"""Local-only RAG question window. Standard library; no new web dependency."""
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
import argparse,json,secrets,threading,os
from common import ROOT,dump
from chat_service import RAGChat

WEB=ROOT/'chat_web'


def make_handler(chat,token,monitor=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,fmt,*args):
            # HTTP paths/status only. Never log request bodies/auth headers.
            return
        def trusted(self):
            host=self.headers.get('Host','')
            return host in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
        def send(self,status,data,mime='application/json; charset=utf-8'):
            if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False).encode()
            self.send_response(status);self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; object-src 'none'")
            self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):pass
        def do_GET(self):
            if not self.trusted():return self.send(403,{'error':'仅允许本机访问。'})
            path=urlsplit(self.path).path
            if path.startswith('/api/monitor/'):
                if monitor is None:return self.send(503,{'error':'监控服务待接入。'})
                try:return self.send(200,monitor.get(path,parse_qs(urlsplit(self.path).query)))
                except ValueError as exc:return self.send(400,{'error':str(exc),'error_type':'ValueError'})
                except Exception as exc:return self.send(500,{'error':'监控状态读取失败。','error_type':type(exc).__name__})
            if path=='/api/health':
                return self.send(200,dict(status='ready',application='financial-research-workspace',version='monitor-v1',rag_only=monitor is None,backend='local',core_companies=['MU','TSM','NVDA'],
                    raw_chunks=sum(1 for _ in (ROOT/'output/chunks.jsonl').open(encoding='utf-8')),
                    reviewed_vectors=sum(1 for _ in (ROOT/'output/embedded_records.jsonl').open(encoding='utf-8')),
                    news_enabled=bool(monitor and any(p['status']=='active' for p in monitor.store.list_plans())),
                    monitor_worker=monitor.runtime.worker_status() if monitor else None,
                    provider_config_present=(ROOT.parent/'apikey.env').is_file()))
            if path=='/':
                text=(WEB/'index.html').read_text(encoding='utf-8').replace('__CSRF_TOKEN__',token)
                return self.send(200,text.encode(),'text/html; charset=utf-8')
            names={'/app.js':('app.js','text/javascript; charset=utf-8'),'/monitor.js':('monitor.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
            if path in names:
                name,mime=names[path];return self.send(200,(WEB/name).read_bytes(),mime)
            if path=='/favicon.ico':return self.send(204,b'')
            return self.send(404,{'error':'页面不存在。'})
        def do_POST(self):
            if not self.trusted() or not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),token):
                return self.send(403,{'error':'页面会话已失效，请刷新后重试。'})
            origin=self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'):
                return self.send(403,{'error':'不允许跨站请求。'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=32000:raise ValueError('请求大小超出范围。')
                body=json.loads(self.rfile.read(size));path=urlsplit(self.path).path
                if path=='/api/chat':out=chat.ask(body)
                elif path=='/api/more':out=chat.more(body.get('query_id'))
                elif path.startswith('/api/monitor/'):
                    if monitor is None:return self.send(503,{'error':'监控服务待接入。'})
                    try:out=monitor.post(path,body)
                    except ValueError as exc:return self.send(400,{'error':str(exc),'error_type':'ValueError'})
                else:return self.send(404,{'error':'接口不存在。'})
                return self.send(200,out)
            except (ValueError,KeyError,TypeError) as exc:
                return self.send(400,{'error':'输入或检索状态不正确，请检查公司、问题和带时区的时间。','error_type':type(exc).__name__})
            except Exception as exc:
                return self.send(500,{'error':'检索未成功返回。没有自动切换后端或调用新闻，请缩小问题或暂时关闭语义检索。','error_type':type(exc).__name__})
    return Handler


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8876)
    args=parser.parse_args();token=secrets.token_hex(24)
    from monitor_api import MonitorAPI
    monitor=MonitorAPI()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),make_handler(RAGChat(),token,monitor))
    server.daemon_threads=True
    monitor.runtime.start()
    dump(ROOT/'state/rag_chat_server.json',dict(pid=os.getpid(),url=f'http://127.0.0.1:{args.port}',
        local_only=True,news_enabled=False,application='financial-research-workspace',version='monitor-v1'))
    print(f'RAG chat ready: http://127.0.0.1:{args.port}',flush=True)
    try:server.serve_forever()
    finally:monitor.runtime.stop();server.server_close()


if __name__=='__main__':main()
