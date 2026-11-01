"""Archive public source material, runnable workspace code and validated data, without secrets."""
from common import *
import zipfile,hashlib

def run():
 archive=WORKSPACE/'科技产业链RAG试点_MU_TSM_NVDA_2026-10-17.zip'
 paths=set()
 for p in ROOT.iterdir():
  if p.is_file() and (p.suffix in ['.py','.ps1','.cypher'] or p.name in ['README.txt','.env.example','.gitignore']):paths.add(p)
 for subdir in ['output','text','numeric']:
  for p in (ROOT/subdir).rglob('*'):
   if not p.is_file() or p.suffix=='.log' or p.name in ['package_report.json','test_db_port_fix_superseded.json']:continue
   if 'qa' in p.parts and not (p.name in ['Finance_graph.png','Finance_ready.png'] or p.name.startswith(('MU_','NVDA_','TSM_'))):continue
   paths.add(p)
 for s in load(ROOT/'output/source_catalog.json'):
  if s.get('local_path'):
   p=(ROOT/s['local_path']).resolve();p.relative_to(ROOT)
   if p.is_file():paths.add(p)
 paths.update((ROOT/'raw').glob('*.json'))
 paths.add(ROOT/'state/embedding_cache.sqlite3')
 paths.add(WORKSPACE/'下次从这里继续.txt')
 for name in ['model.py','requirements.txt','requirements.lock.txt']:paths.add(WORKSPACE/'financial_rag'/name)
 note='''本包是本机工作区的科技RAG v1资料/代码归档。运行说明见tech_rag/README.txt。
已包含官方归档原文、结构化JSON/CSV、文本块、152条实际向量、模型指纹、向量缓存及验证结果。
凭据.env、Desktop凭据存储、实例配置备份、会话SQLite、完整venv及2.27GB模型权重均未打包。
当前工作区可直接复用financial_rag/.venv和已缓存BGE-M3运行；附共享model.py及依赖清单供核对。
文档重新采集还依赖原工作区原油产业链RAG试点/_tools中的PDF/HTML库，原工作区不需要重新安装。
脱离本工作区并不是独立安装包，需要本地模型缓存及说明中的依赖；不要为已有环境重复下载模型。
Finance连接已在本机配置并实测。迁移机器时须由用户另行提供本地安全配置，不从本包获取密码。
原油成果保留在原工作区及单独原油资料包中，没有混入科技检索或这个数据集。
'''
 with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
  for p in sorted(paths,key=str):
   rel=p.relative_to(WORKSPACE)
   if p.name=='.env' or any(part in ['.venv','neo4j_runtime','backups'] for part in rel.parts):raise RuntimeError('private/runtime path in archive')
   z.write(p,rel.as_posix())
  z.writestr('交付说明.txt',note)
 with zipfile.ZipFile(archive) as z:
  assert z.testzip() is None
  names=z.namelist();assert not any(Path(n).name=='.env' for n in names)
 with archive.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
 report={'archive':str(archive),'files':len(names),'bytes':archive.stat().st_size,'sha256':digest,'crc_verified':True,
  'credentials_included':False,'model_weights_included':False,'requires_existing_local_runtime':True,'created_at':now()}
 dump(ROOT/'output/package_report.json',report);print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':run()
