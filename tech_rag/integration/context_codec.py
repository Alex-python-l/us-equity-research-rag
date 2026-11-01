"""Lossless JSON tables/text references for required context that will not fit.

No sentence, field, condition or unknown is shortened. The identical expanded
view can be reconstructed and hashed before sending the compact representation.
"""
from collections import Counter
from copy import deepcopy
import hashlib


def pack_context(view, *, compact_ids=False):
    data=deepcopy(view)
    for name,mapping in (('accepted_parents',False),('evidence',True),('news_evidence',True)):
        value=data.get(name)
        if not value:continue
        rows=list(value.values()) if mapping else value
        if not rows:continue
        columns=list(rows[0])
        if not all(list(r)==columns for r in rows):continue
        table={'columns':columns,'rows':[[r[k] for k in columns] for r in rows]}
        if mapping:
            table['ids']=list(value)
            if compact_ids:
                from os.path import commonprefix
                prefix=commonprefix(table['ids'])
                if len(prefix)>=8:
                    table['id_prefix']=prefix
                    table['ids']=[key[len(prefix):] for key in table['ids']]
        if compact_ids and name=='news_evidence' and 'version_id' in columns:
            position=columns.index('version_id')
            versions=list(dict.fromkeys(row[position] for row in table['rows']))
            codes={f'V{i}':version for i,version in enumerate(versions)}
            lookup={version:code for code,version in codes.items()}
            table['value_codebooks']={'version_id':codes}
            for row in table['rows']:row[position]=lookup[row[position]]
        data.pop(name);data[name+'_table']=table
    counts=Counter()
    def count(value):
        if isinstance(value,str) and len(value)>=80:counts[value]+=1
        elif isinstance(value,dict):
            for item in value.values():count(item)
        elif isinstance(value,list):
            for item in value:count(item)
    count(data)
    texts={'T_'+hashlib.sha256(s.encode()).hexdigest()[:16]:s for s,n in counts.items() if n>1}
    lookup={text:key for key,text in texts.items()}
    def replace(value):
        if isinstance(value,str) and value in lookup:return {'$text':lookup[value]}
        if isinstance(value,list):return [replace(v) for v in value]
        if isinstance(value,dict):return {k:replace(v) for k,v in value.items()}
        return value
    data=replace(data)
    data['shared_text']=texts
    data['context_encoding']='lossless_tables_and_shared_text_v1'
    data['encoding_notice']=('Resolve {$text: ID} with shared_text. For *_table, rows align exactly with columns; '
        'evidence_table.ids identify citations. Every original field is retained. Expand references before reasoning '
        'and output full original conditions/unknowns, never reference objects.')
    if compact_ids:
        data['encoding_notice']+=(' Reconstruct full citation IDs by prepending table.id_prefix to each table.ids entry. '
            'Decode each column using table.value_codebooks[column] before reasoning; output the full citation IDs.')
    if unpack_context(data)!=view:raise ValueError('Context encoding lost data')
    return data


def unpack_context(encoded):
    if encoded.get('context_encoding')!='lossless_tables_and_shared_text_v1':return deepcopy(encoded)
    texts=encoded['shared_text']
    def restore(value):
        if isinstance(value,dict) and set(value)=={'$text'}:return texts[value['$text']]
        if isinstance(value,list):return [restore(v) for v in value]
        if isinstance(value,dict):return {k:restore(v) for k,v in value.items()}
        return value
    data=restore({k:v for k,v in encoded.items() if k not in ('shared_text','context_encoding','encoding_notice')})
    for name in ('accepted_parents','evidence','news_evidence'):
        table=data.pop(name+'_table',None)
        if table:
            for column,codes in table.get('value_codebooks',{}).items():
                position=table['columns'].index(column)
                for row in table['rows']:row[position]=codes[row[position]]
            rows=[dict(zip(table['columns'],r,strict=True)) for r in table['rows']]
            data[name]=dict(zip([table.get('id_prefix','')+key for key in table['ids']],rows,strict=True)) if 'ids' in table else rows
    return data
