"""Tiny synthetic corpus for source-only CI; never used as production evidence."""
from pathlib import Path
import hashlib,json
import numpy as np

def make_kb(root):
    root=Path(root);(root/'output').mkdir();(root/'raw').mkdir();(root/'text').mkdir()
    raw=b'<p>Synthetic supplier disclosure.</p>';(root/'raw/a.html').write_bytes(raw)
    fingerprint=hashlib.sha256(raw).hexdigest()
    block=dict(source_id='s',pdf_page=None,paragraph=1,text='Synthetic supplier disclosure.')
    (root/'text/a.jsonl').write_text(json.dumps(block)+'\n',encoding='utf-8')
    source=dict(source_id='s',ticker='NVDA',local_path='raw/a.html',text_path='text/a.jsonl',sha256=fingerprint,
                url='https://example.test/source',title='Synthetic disclosure',kind='synthetic',available_at='2020-01-01T00:00:00Z',historical_eligible=True)
    provenance=dict(model='SYNTHETIC',revision='fixture',dimensions=1024)
    signature=hashlib.sha256(json.dumps(provenance,sort_keys=True).encode()).hexdigest()
    record=dict(record_id='r',embedding_signature=signature,kind='synthetic',text='fixture')
    chunk=dict(chunk_id='c',source_id='s',source_sha256=fingerprint,source_url=source['url'],ticker='NVDA',pdf_page=None,
                paragraph=1,char_start=0,char_end=len(block['text']),text=block['text'],available_at=source['available_at'],historical_eligible=True)
    for name,value in {'source_catalog.json':[source],'entities.json':[],'relations.json':[],'evidence.json':[],
                       'model_provenance.json':provenance}.items():
        (root/'output'/name).write_text(json.dumps(value),encoding='utf-8')
    for name,values in {'chunks.jsonl':[chunk],'key_records.jsonl':[record],'embedded_records.jsonl':[record]}.items():
        (root/'output'/name).write_text(''.join(json.dumps(r)+'\n' for r in values),encoding='utf-8')
    matrix=np.zeros((1,1024),dtype=np.float32);matrix[0,0]=1;np.save(root/'output/embeddings.npy',matrix)
    return source
