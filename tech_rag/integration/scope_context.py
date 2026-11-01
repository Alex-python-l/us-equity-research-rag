"""Verified publisher markup only; never infer business scope from similarity."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from common import ROOT


def _time(value):
    stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
    if stamp.tzinfo is None:raise ValueError('timezone required')
    return stamp.astimezone(timezone.utc)


def norm(text):return re.sub(r'\s+',' ',text).strip()


def qualify_record(record,analysis_time,historical):
    """Return at most two exact preceding DOM labels in the same source version.

    Cached .env or external hints are never read. PDF/DOM mismatch fails closed.
    This is structural evidence, not an assertion that an economic fact is true.
    """
    result={'status':'unavailable','labels':[],'source_record':{k:record.get(k) for k in
        ('source_id','source_sha256','pdf_page','paragraph','char_start','char_end')},
        'inference_performed':False}
    def fail(reason):return {**result,'reason':reason}
    try:
        source=next(s for s in json.loads((ROOT/'output/source_catalog.json').read_text(encoding='utf-8'))
                    if s['source_id']==record['source_id'])
        at=_time(analysis_time)
        if historical and source.get('historical_eligible') is not True:return fail('historical_version_not_verified')
        if _time(source['available_at'])>at:return fail('source_not_yet_public')
        if not historical and source.get('retrieved_at') and _time(source['retrieved_at'])>at:
            return fail('snapshot_not_collected_at_analysis_time')
        if record.get('pdf_page') is not None:return fail('pdf_markup_not_supported')
        raw=(ROOT/source['local_path']).resolve();text=(ROOT/source['text_path']).resolve()
        if not raw.is_relative_to(ROOT.resolve()) or not text.is_relative_to(ROOT.resolve()):return fail('source_path_outside_dataset')
        if raw.suffix.lower() not in ('.html','.htm'):return fail('non_html_source')
        content=raw.read_bytes();fingerprint=hashlib.sha256(content).hexdigest()
        if fingerprint!=source['sha256'] or fingerprint!=record['source_sha256']:return fail('raw_sha256_mismatch')
        blocks=[json.loads(line) for line in text.read_text(encoding='utf-8').splitlines() if line.strip()]
        block=next((b for b in blocks if b.get('paragraph')==record.get('paragraph') and b.get('pdf_page') is None),None)
        if not block:return fail('record_quote_coordinate_mismatch')
        start,end=record['char_start'],record['char_end']
        if type(start) is not int or type(end) is not int or not 0<=start<end or norm(block['text'])[start:end]!=record['text']:
            return fail('record_quote_coordinate_mismatch')
        try:from bs4 import BeautifulSoup
        except ImportError:
            # Reuse the exact parser already on this computer. Only its small
            # licensed source packages are vendored; no other environment path.
            sys.path.insert(0,str(ROOT/'vendor/html_scope'))
            from bs4 import BeautifulSoup
        soup=BeautifulSoup(content,'html.parser')
        for tag in soup(['script','style','nav','footer','header']):tag.decompose()
        root=soup.find('main') or soup;nodes=[]
        for element in root.find_all(['h1','h2','h3','h4','p','li','table']):
            if element.name!='table' and element.find_parent('table'):continue
            if element.get_text(' ',strip=True):nodes.append(element)
        if len(nodes)!=len(blocks) or any(norm(n.get_text(' ',strip=True))!=norm(b['text']) for n,b in zip(nodes,blocks)):
            return fail('html_text_archive_mismatch')
        levels={};emphasis=None
        for node,b in zip(nodes,blocks):
            if b.get('paragraph')==record['paragraph']:break
            label=norm(b['text']);markup=None
            if node.name in ('h1','h2','h3','h4'):
                level=int(node.name[1]);levels={k:v for k,v in levels.items() if k<level};emphasis=None
                markup=node.name
            elif node.name=='p' and len(label)<=160 and not re.search(r'[.!?。！？;；]$',label):
                strong=node.find(['strong','b'])
                if strong and norm(strong.get_text(' ',strip=True))==label:markup='p/'+strong.name
            if markup:
                row={'source_id':source['source_id'],'source_sha256':fingerprint,'source_url':source.get('url'),
                    'pdf_page':None,'paragraph':b['paragraph'],'char_start':0,'char_end':len(label),
                    'text':label,'publisher_markup':markup,'round_trip_verified':True,
                    'available_at':source['available_at'],'historical_eligible':source.get('historical_eligible',False)}
                if markup.startswith('h'):levels[level]=row
                else:emphasis=row
        candidates=sorted([*levels.values(),*([emphasis] if emphasis else [])],key=lambda r:r['paragraph'],reverse=True)
        labels=[];chars=0
        for label in candidates:
            if len(labels)==2:break
            if chars+len(label['text'])<=400:labels.append(label);chars+=len(label['text'])
        return {**result,'status':'verified','labels':sorted(labels,key=lambda r:r['paragraph']),
                'reason':None if labels else 'no_preceding_publisher_label','label_text_chars':chars}
    except (OSError,ValueError,KeyError,TypeError,StopIteration,ImportError):return fail('source_structure_unavailable')
