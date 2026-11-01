from pathlib import Path
from datetime import datetime, timezone
import hashlib,json,sys

ROOT=Path(__file__).resolve().parent
WORKSPACE=ROOT.parent
for p in ['raw','text','output','state']:(ROOT/p).mkdir(exist_ok=True)
sys.path.insert(0,str(WORKSPACE/'financial_rag'))
sys.path.insert(0,str(WORKSPACE/'原油产业链RAG试点/_tools'))
DATASET='tech_company_v1'
ASOF_DATE='2026-10-17'

def now():return datetime.now(timezone.utc).isoformat()
def sha(text):return hashlib.sha256(text.encode()).hexdigest()
def dump(path,data):Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def jsonl(path,rows):
 with Path(path).open('w',encoding='utf-8') as f:
  for r in rows:f.write(json.dumps(r,ensure_ascii=False)+'\n')
def readl(path):
 with Path(path).open(encoding='utf-8') as f:
  return [json.loads(line) for line in f if line.strip()]
def parse_time(value):
 d=datetime.fromisoformat(value.replace('Z','+00:00'))
 if d.tzinfo is None:raise ValueError('analysis time requires timezone')
 return d
