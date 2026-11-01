"""Trusted local registry for one-shot immutable RAG handoffs; no generation."""
from copy import deepcopy
from contextlib import closing
from pathlib import Path
import hashlib
import json
import os
import sqlite3
import uuid
from common import ROOT

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')

class RagPackages:
    def __init__(self, directory=None):
        self.directory=Path(directory or ROOT/'state/rag_packages')
        self.directory.mkdir(parents=True,exist_ok=True)
        self.db_path=self.directory/'registry.sqlite3'
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS packages (id TEXT PRIMARY KEY, sha TEXT NOT NULL, payload BLOB NOT NULL)')

    def put(self, payload):
        payload=deepcopy(payload)
        if not isinstance(payload,dict):raise ValueError('RAG package requires an object')
        payload.pop('rag',None)
        data=canonical(payload);sha=hashlib.sha256(data).hexdigest();identity='RAG_'+sha
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute('INSERT OR IGNORE INTO packages VALUES (?,?,?)',(identity,sha,data))
        return dict(schema_version='rag_package_v1',package_id=identity,sha256=sha,
                    ticker=payload.get('ticker') or payload.get('guide',{}).get('ticker'),
                    analysis_time=payload.get('analysis_time'),frozen=True)

    def get(self, reference):
        if not isinstance(reference,dict) or reference.get('schema_version')!='rag_package_v1':
            raise ValueError('A trusted frozen RAG reference is required')
        with closing(sqlite3.connect(self.db_path)) as db:
            row=db.execute('SELECT sha,payload FROM packages WHERE id=?',(reference.get('package_id'),)).fetchone()
        if not row or row[0]!=reference.get('sha256') or hashlib.sha256(row[1]).hexdigest()!=row[0]:
            raise ValueError('Unknown or altered RAG package')
        value=json.loads(row[1])
        if reference.get('ticker')!=(value.get('ticker') or value.get('guide',{}).get('ticker')):
            raise ValueError('RAG company mismatch')
        return value

    def verify(self, payload, reference):
        value=deepcopy(payload);value.pop('rag',None)
        if canonical(value)!=canonical(self.get(reference)):
            raise ValueError('RAG content differs from the trusted frozen package')
        return deepcopy(reference)

def freeze(payload, directory=None):
    store=RagPackages(directory)
    if payload.get('rag'):
        store.verify(payload,payload['rag'])
        return deepcopy(payload)
    result=deepcopy(payload);result['rag']=store.put(result)
    return result
