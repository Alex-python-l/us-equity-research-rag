"""CLI for the actual evidence-constrained TradingAgents graph."""
from pathlib import Path
import argparse
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticker',required=True,help='MU/美光, TSM/台积电, NVDA/英伟达')
    parser.add_argument('--question',required=True)
    parser.add_argument('--as-of',default='current')
    parser.add_argument('--backend',choices=['local','neo4j'],default='local')
    parser.add_argument('--model-mode',choices=['controlled','online'],default='controlled')
    parser.add_argument('--llm-config',type=Path)
    parser.add_argument('--dense',dest='dense',action='store_true',default=True)
    parser.add_argument('--no-dense',dest='dense',action='store_false',help='Explicit keyword+graph diagnostic; default includes reviewed BGE vectors')
    parser.add_argument('--bad-rewrite',action='store_true',help='Controlled adversarial trader for validation only')
    parser.add_argument('--fetch-news',action='store_true',help='Fetch current industry-keyword news after front-loaded RAG, including in controlled mode')
    parser.add_argument('--workflow',choices=['brief','multiagent'],default='brief',help='Default one researcher + one reviewer; all roles share the front-loaded snapshot')
    parser.add_argument('--news-mode',choices=['auto','native','legacy'],default='auto',help='Online auto uses Pro native industry search + Yahoo direct company news; legacy explicitly preserves prior route')
    parser.add_argument('--search-snapshots',type=Path,help='Explicit actual host-search snapshots matching the frozen query/time; no new search is impersonated')
    parser.add_argument('--body-cache',type=Path,help='Re-use original collection bodies and failures; do not re-fetch')
    parser.add_argument('--event-json',type=Path,help='Optional received news. Unverified metadata remains a candidate.')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--persist-audit',action='store_true',help='Explicit host-side audit write; never available to models')
    args=parser.parse_args()
    from integration.framework import run_research
    from integration.search_snapshots import SnapshotSearchProvider,CachedArticleProvider
    result=run_research(args.ticker,args.question,args.as_of,backend=args.backend,
        model_mode=args.model_mode,llm_config_path=args.llm_config,use_dense=args.dense,
        bad_rewrite=args.bad_rewrite,fetch_news=args.fetch_news,
        workflow=args.workflow,news_mode=args.news_mode,archive_dir=args.output.parent/(args.output.stem+'_audit'),
        search_provider=SnapshotSearchProvider(args.search_snapshots) if args.search_snapshots else None,
        article_provider=CachedArticleProvider(args.body_cache) if args.body_cache else None,
        event=json.loads(args.event_json.read_text(encoding='utf-8')) if args.event_json else None)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    audit=None
    if args.persist_audit:
        from integration.research_store import persist_research_run
        if result.get('schema_version')=='industry_research_v5':
            raise ValueError('v5 findings are local research artifacts; v3 Neo4j claim promotion is not supported')
        audit=persist_research_run(result)
        args.output.with_suffix('.audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(args.output.resolve()),'run_id':result['run_id'],
        'model_mode':args.model_mode,'gate':result['research_gate'],
        'analysis_usage':result['run_usage'],
        'native_discovery_usage':result.get('native_discovery_usage'),
        'combined_provider_usage':result.get('combined_provider_usage'),
        'total_model_requests':result.get('total_model_requests',result['run_usage']['model_requests']),
        'audit':audit},ensure_ascii=False))

if __name__=='__main__':main()
