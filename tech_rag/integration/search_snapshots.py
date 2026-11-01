"""Explicit host-search handoff, not a pretend browsing LLM API.

Python cannot call the Codex session's tool implicitly. A host performs a real
search, archives its exact response, and hands matching snapshots to this
provider. New queries/time windows fail closed instead of using old searches.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from common import parse_time
from integration.news_body import safe_url


def normalize_host_response(raw, max_results=3):
    if not 1 <= max_results <= 3: raise ValueError('max_results must be 1..3')
    text=raw.get('raw','')
    if not isinstance(text,str): raise ValueError('Expected original engine text response')
    blocks=re.split(r'\n-{20,}\n',text)
    items=[];seen=set();raw_count=0
    for block in blocks:
        match=re.match(r'\s*(.*?) \((https?://[^\s]+)\)\n',block,re.S)
        if not match: continue
        raw_count+=1
        # Apply the frozen returned-result cap before inspecting quality or
        # publication dates. Never cherry-pick further down the engine output.
        if raw_count>max_results: continue
        title,url=match.groups()
        try:url,host,_=safe_url(url)
        except ValueError:continue
        if url in seen:continue
        seen.add(url)
        date=re.search(r'Published:\s*([^;\n]+)',block)
        items.append(dict(candidate_id='NC_'+hashlib.sha256(url.encode()).hexdigest()[:24],
            title=title.strip(),url=url,publisher=None,provider_domain=host,
            provider_published_text=date[1] if date else None,
            provider_published_at=None,actual_public_at=None,
            first_seen_at=raw.get('completed_at'),status='candidate_metadata',
            body_acquired=False,source_verified=False,historical_eligible=False,
            unknowns=['Engine date/snippet is metadata; fetch original body and verify its times.']))
    return dict(provider='Codex hosted web search',status='candidates' if items else 'no_candidates',
        query=raw['query'],query_hash=raw['query_hash'],as_of=raw['as_of'],
        time_window=raw['time_window'],items=items,calls=1,real_network_executed=True,
        actual_tool=raw.get('actual_tool'),seconds=raw.get('seconds'),
        raw_engine_result_count=raw_count,returned_result_budget=max_results,
        provider_token_usage=None,provider_currency_cost=None,
        search_LLM_API_used=False,coverage='Only first engine-ranked results; not exhaustive.',
        raw_response_sha256=hashlib.sha256(text.encode()).hexdigest())


class SnapshotSearchProvider:
    """Reproduce an actual bounded host search; no network or fallback here."""
    def __init__(self,directory):self.directory=Path(directory).resolve()

    def __call__(self,task,as_of,*,historical=False,max_results=3,cache_dir=None):
        base=dict(provider='Codex hosted web search snapshot',items=[],calls=0,
                  real_network_executed=False,reused_snapshot=False)
        if historical:return {**base,'status':'unsupported_historical','reason':'No verified point-in-time engine snapshot.'}
        key=task.get('query_hash','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',key):return {**base,'status':'provider_unavailable','reason':'invalid_query_hash'}
        path=self.directory/(key+'_raw.json')
        if not path.is_file():return {**base,'status':'provider_unavailable','reason':'host_search_callback_or_matching_snapshot_required'}
        raw=json.loads(path.read_text(encoding='utf-8'))
        if (raw.get('query')!=task['query_en'] or raw.get('query_hash')!=key or
            parse_time(raw['as_of'])!=parse_time(as_of) or raw.get('time_window')!=task['time_window']):
            return {**base,'status':'provider_unavailable','reason':'snapshot_query_clock_or_window_mismatch'}
        normalized=normalize_host_response(raw,max_results)
        return {**normalized,'calls':0,'real_network_executed':False,'reused_snapshot':True,
                'original_host_search_executed':True,'snapshot_path':str(path),
                'snapshot_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


class CachedArticleProvider:
    """Re-use successes AND failures from this collection, never re-fetch."""
    def __init__(self,path):
        self.path=Path(path).resolve()
        self.cache=json.loads(self.path.read_text(encoding='utf-8'))

    def __call__(self,item,as_of,*,historical=False,archive_dir=None,max_chars=18000):
        base=dict(status='insufficient',eligible=False,body_acquired=False,source_verified=False,
                  paragraphs=[],calls=0,real_network_executed=False,body_cache_reused=True)
        if historical:return {**base,'reason':'no_verified_historical_body_cache'}
        cached=self.cache.get(item.get('url'))
        if not cached:return {**base,'reason':'body_not_in_frozen_collection_no_retry'}
        if parse_time(cached.get('as_of'))!=parse_time(as_of):return {**base,'reason':'cached_body_clock_mismatch'}
        record=deepcopy(cached)
        if record.get('body_acquired'):
            try:
                raw=Path(record['raw_path']).resolve()
                if not raw.is_relative_to(self.path.parent):raise ValueError('archive path outside collection')
                if hashlib.sha256(raw.read_bytes()).hexdigest()!=record['raw_sha256']:raise ValueError('raw hash mismatch')
                text=record['body_text']
                if len(text)>max_chars or hashlib.sha256(text.encode()).hexdigest()!=record['body_sha256']:raise ValueError('body hash mismatch')
                for p in record['paragraphs']:
                    if text[p['char_start']:p['char_end']]!=p['text']:raise ValueError('paragraph coordinate mismatch')
            except (OSError,KeyError,TypeError,ValueError):
                return {**base,'reason':'cached_body_integrity_failure'}
        record.update(calls=0,real_network_executed=False,body_cache_reused=True,
                      original_acquisition_calls=cached.get('calls'),
                      cache_reverified_at_use=bool(record.get('body_acquired')))
        return record


def source_aware_candidates(snapshot,body_cache):
    """Within the same top-three cap, avoid known impossible acquisitions.

    This is availability routing, not evidence of an event or lack of events.
    A source excluded here remains in the original search snapshot/audit list.
    """
    blocked_domains={urlsplit(url).hostname for url,item in body_cache.items()
        if item.get('status') in ('access_restricted','archiving_disallowed')}
    selected,excluded=[],[]
    for candidate in snapshot.get('items',[])[:3]:
        url=candidate.get('url','');reason=None
        if url in body_cache and not body_cache[url].get('eligible'):
            reason='previous_body_attempt_unavailable_or_ineligible_no_retry'
        elif urlsplit(url).hostname in blocked_domains:
            reason='origin_previously_access_restricted_or_archiving_disallowed'
        elif urlsplit(url).path.lower().endswith('.pdf'):
            reason='current_body_parser_does_not_support_PDF'
        elif re.search(r'\b(?:months?|years?) ago\b',str(candidate.get('provider_published_text','')),re.I):
            reason='provider_metadata_outside_30_day_window_not_a_verified_time'
        if reason:excluded.append({**candidate,'exclusion_reason':reason})
        else:selected.append(deepcopy(candidate))
    return {**deepcopy(snapshot),'items':selected,'excluded_candidates':excluded,
            'selection_policy':'availability_within_original_top_three_v5_r2',
            'status':'candidates' if selected else 'no_acquirable_top_three_candidates'}


class SourceAwareSnapshotProvider(SnapshotSearchProvider):
    def __init__(self,directory,body_cache_path):
        super().__init__(directory)
        self.body_cache_path=Path(body_cache_path)
        self.availability_snapshot=json.loads(self.body_cache_path.read_text(encoding='utf-8'))
    def __call__(self,*args,**kwargs):
        snapshot=super().__call__(*args,**kwargs)
        if snapshot['status'] not in ('candidates','no_candidates'):return snapshot
        return source_aware_candidates(snapshot,self.availability_snapshot)
