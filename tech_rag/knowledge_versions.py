"""Immutable local generations. Only a fully validated manifest can become active."""
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from common import ROOT, readl, load, now

ARTIFACTS=('source_catalog.json','entities.json','relations.json','evidence.json','chunks.jsonl',
           'key_records.jsonl','embedded_records.jsonl','embeddings.npy','model_provenance.json')

def file_hash(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def safe_path(root, value):
    path=(Path(root)/value).resolve()
    if not path.is_relative_to(Path(root).resolve()):raise ValueError('Knowledge path escapes dataset')
    return path

@dataclass(frozen=True)
class KnowledgeSnapshot:
    generation: str
    artifacts: Path
    root: Path
    manifest: dict | None = None

    def file(self,name):
        if name not in ARTIFACTS:raise ValueError('Unknown knowledge artifact')
        path=self.artifacts/name
        if self.manifest and file_hash(path)!=self.manifest['artifacts'][name]:
            raise ValueError('Knowledge artifact hash mismatch: '+name)
        return path

def active_snapshot(root=ROOT,generation=None):
    root=Path(root).resolve();directory=root/'state/kb'
    if generation in (None,'legacy') and (generation=='legacy' or not (directory/'active.json').exists()):
        return KnowledgeSnapshot('legacy',root/'output',root)
    pointer=load(directory/'active.json') if generation is None else None
    identity=generation or pointer['generation']
    if not re.fullmatch(r'KB_[a-f0-9]{32}',identity):raise ValueError('Invalid generation')
    artifacts=directory/'versions'/identity
    manifest=load(artifacts/'manifest.json')
    if pointer and pointer['manifest_sha256']!=file_hash(artifacts/'manifest.json'):
        raise ValueError('Active knowledge manifest mismatch')
    if manifest['generation']!=identity:raise ValueError('Knowledge generation mismatch')
    return KnowledgeSnapshot(identity,artifacts,root,manifest)

def validate_snapshot(snapshot):
    import numpy as np
    paths={name:snapshot.file(name) for name in ARTIFACTS}
    sources=load(paths['source_catalog.json']);by_source={s['source_id']:s for s in sources}
    gaps=[];blocks={};raw_count=0
    for source in sources:
        sid=source['source_id']
        if not source.get('local_path') or not source.get('text_path'):
            gaps.append(dict(source_id=sid,reason='not_archived'));continue
        raw=safe_path(snapshot.root,source['local_path']);text=safe_path(snapshot.root,source['text_path'])
        if file_hash(raw)!=source['sha256']:raise ValueError('Original source hash mismatch: '+sid)
        if snapshot.manifest:
            expected=snapshot.manifest['sources'][sid]
            if file_hash(text)!=expected['text_sha256']:raise ValueError('Extracted text changed: '+sid)
        blocks[sid]={(b.get('pdf_page'),b.get('paragraph')):b for b in readl(text)};raw_count+=1
    chunks=readl(paths['chunks.jsonl']);ids=set()
    for chunk in chunks:
        sid=chunk['source_id'];block=blocks.get(sid,{}).get((chunk.get('pdf_page'),chunk.get('paragraph')))
        if chunk['chunk_id'] in ids:raise ValueError('Duplicate chunk')
        ids.add(chunk['chunk_id'])
        if not block or chunk['source_sha256']!=by_source[sid]['sha256'] or re.sub(r'\s+',' ',block['text']).strip()[chunk['char_start']:chunk['char_end']]!=chunk['text']:
            raise ValueError('Chunk original round-trip mismatch')
    evidence={e['evidence_id']:e for e in load(paths['evidence.json'])}
    for e in evidence.values():
        block=blocks.get(e['source_id'],{}).get((e.get('pdf_page'),e.get('paragraph')))
        if not block or re.sub(r'\s+',' ',block['text']).strip()[e['char_start']:e['char_end']]!=e['quote']:
            raise ValueError('Reviewed evidence original mismatch')
    entities={e['entity_id'] for e in load(paths['entities.json'])}
    for r in load(paths['relations.json']):
        if r['subject_id'] not in entities or r['object_id'] not in entities or not r['evidence_ids'] or not set(r['evidence_ids'])<=set(evidence):
            raise ValueError('Reviewed graph reference closure failed')
    records=readl(paths['embedded_records.jsonl']);matrix=np.load(paths['embeddings.npy'],allow_pickle=False)
    provenance=load(paths['model_provenance.json'])
    signature=hashlib.sha256(json.dumps(provenance,sort_keys=True).encode()).hexdigest()
    if matrix.shape!=(len(records),1024) or not np.isfinite(matrix).all() or not np.allclose(np.linalg.norm(matrix,axis=1),1,atol=1e-5):
        raise ValueError('Invalid vector matrix')
    if any(r.get('embedding_signature')!=signature for r in records):raise ValueError('Embedding signature mismatch')
    keys=readl(paths['key_records.jsonl'])
    if len({r['record_id'] for r in records})!=len(records) or [r['record_id'] for r in records]!=[r['record_id'] for r in keys]:raise ValueError('Vector/record ordering or identity mismatch')
    if any(record.get('text')!=key.get('text') for record,key in zip(records,keys)):raise ValueError('Embedded text differs from indexed key record')
    return dict(status='verified_with_catalog_gaps' if gaps else 'verified',generation=snapshot.generation,
                archived_sources=raw_count,chunks=len(chunks),vectors=len(records),catalog_gaps=gaps,checked_at=now())

class KnowledgeVersions:
    def __init__(self,root=ROOT):
        self.root=Path(root).resolve();self.state=self.root/'state/kb';self.state.mkdir(parents=True,exist_ok=True)

    @contextmanager
    def publication_lock(self):
        # SQLite releases the writer lease if a process crashes; no stale lock-file deletion.
        db=sqlite3.connect(self.state/'publisher.sqlite3',timeout=0,isolation_level=None)
        try:
            try:db.execute('BEGIN IMMEDIATE')
            except sqlite3.OperationalError:raise RuntimeError('Knowledge publisher already running') from None
            yield
            db.commit()
        finally:db.close()

    def publish(self,documents=(),*,check_owner=None):
        """Documents are archived originals+blocks; never inferred graph facts."""
        with self.publication_lock():
            base=active_snapshot(self.root);validate_snapshot(base)
            identity='KB_'+uuid.uuid4().hex;stage=self.state/'staging'/identity;stage.mkdir(parents=True)
            for name in ARTIFACTS:shutil.copyfile(base.file(name),stage/name)
            sources=load(stage/'source_catalog.json');chunks=readl(stage/'chunks.jsonl')
            seen={s['source_id']:s for s in sources};added=[];candidates=[]
            entities=load(stage/'entities.json')
            for source,blocks in documents:
                sid=source['source_id']
                if sid in seen:
                    if seen[sid].get('sha256')!=source['sha256']:raise ValueError('Immutable source identity changed')
                    continue
                sources.append(source);seen[sid]=source;added.append(sid)
                for block in blocks:
                    text=re.sub(r'\s+',' ',block['text']).strip()
                    # Local deterministic blocks; never invent numerical table structure.
                    for start in range(0,len(text),1200):
                        end=min(start+1400,len(text));part=text[start:end]
                        if not part:continue
                        loc=dict(pdf_page=block.get('pdf_page'),paragraph=block.get('paragraph'),char_start=start,char_end=end)
                        cid='TC_'+hashlib.sha256((sid+json.dumps(loc,sort_keys=True)+part).encode()).hexdigest()[:22]
                        chunks.append(dict(chunk_id=cid,source_id=sid,ticker=source['ticker'],section=block.get('section_hint','Official filing'),
                            **loc,text=part,source_sha256=source['sha256'],source_url=source['url'],available_at=source['available_at'],
                            historical_eligible=source.get('historical_eligible',False),report_period=source.get('report_period'),
                            source_kind=source['kind'],table_policy=block.get('table_policy','Keep complete block and original'),
                            semantic_chunking='incremental same-block character windows; no new dense claims'))
                    mentions=[e['entity_id'] for e in entities if len(e.get('name',''))>2 and e['name'].lower() in text.lower()]
                    if len(mentions)>1:
                        candidates.append(dict(source_id=sid,source_sha256=source['sha256'],pdf_page=block.get('pdf_page'),
                            paragraph=block.get('paragraph'),entity_ids=mentions,status='pending_human_review',
                            predicate=None,kind='co_mention_not_a_relation',fact_promotion_allowed=False))
            (stage/'source_catalog.json').write_text(json.dumps(sources,ensure_ascii=False,indent=2),encoding='utf-8')
            with (stage/'chunks.jsonl').open('w',encoding='utf-8') as f:
                for c in chunks:f.write(json.dumps(c,ensure_ascii=False)+'\n')
            from lexical import KeywordIndex
            KeywordIndex(self.state/'indexes'/(identity+'.sqlite3'),chunks)
            snapshot=KnowledgeSnapshot(identity,stage,self.root)
            report=validate_snapshot(snapshot)
            manifest=dict(schema='knowledge_generation_v1',generation=identity,created_at=now(),parent=base.generation,
                artifacts={name:file_hash(stage/name) for name in ARTIFACTS},
                sources={s['source_id']:dict(raw_sha256=s['sha256'],text_sha256=file_hash(safe_path(self.root,s['text_path'])))
                         for s in sources if s.get('local_path') and s.get('text_path')},added_source_ids=added,
                reviewed_graph_unchanged=True,pending_relations=candidates,validation=report)
            (stage/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
            final=self.state/'versions'/identity;final.parent.mkdir(parents=True,exist_ok=True)
            os.replace(stage,final)
            if check_owner:check_owner()
            pointer=self.state/('active.'+uuid.uuid4().hex+'.tmp')
            pointer.write_text(json.dumps(dict(generation=identity,manifest_sha256=file_hash(final/'manifest.json'))),encoding='utf-8')
            os.replace(pointer,self.state/'active.json')
            return dict(status='published',generation=identity,added_sources=added,pending_relations=len(candidates),validation=report)

    def audit(self):return validate_snapshot(active_snapshot(self.root))

    def rollback(self,generation):
        with self.publication_lock():
            snapshot=active_snapshot(self.root,generation);validate_snapshot(snapshot)
            if generation=='legacy':raise ValueError('Publish an adopted legacy generation before rollback')
            temp=self.state/('rollback.'+uuid.uuid4().hex+'.tmp')
            temp.write_text(json.dumps(dict(generation=generation,manifest_sha256=file_hash(snapshot.artifacts/'manifest.json'))),encoding='utf-8')
            os.replace(temp,self.state/'active.json')
