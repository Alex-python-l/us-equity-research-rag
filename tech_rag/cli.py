"""Executable company graph, evidence, scenario, news and agent tools."""
from common import *
import argparse
from tools import CompanyTools

def main():
 p=argparse.ArgumentParser(description='Company-first technology supply-chain RAG v1')
 p.add_argument('--backend',choices=['local','neo4j'],default='local',help='local is explicitly offline; neo4j never silently falls back')
 p.add_argument('--config',help='explicit path to user-selected existing Neo4j secure config')
 p.add_argument('--out',help='save JSON result')
 sub=p.add_subparsers(dest='command',required=True)
 s=sub.add_parser('company');s.add_argument('ticker',choices=['MU','TSM','NVDA']);s.add_argument('--as-of',required=True);s.add_argument('--question',default='');s.add_argument('--page-size',type=int,default=20);s.add_argument('--cursor');s.add_argument('--max-hops',type=int,default=3);s.add_argument('--max-paths',type=int,default=2000)
 s=sub.add_parser('evidence');s.add_argument('ids',nargs='+');s.add_argument('--as-of',required=True)
 s=sub.add_parser('scenario');s.add_argument('ticker',choices=['MU','TSM','NVDA']);s.add_argument('kind',choices=['hbm_shortage','packaging_capacity']);s.add_argument('--as-of',required=True)
 s=sub.add_parser('search');s.add_argument('query');s.add_argument('--as-of',required=True);s.add_argument('--top-k',type=int,default=6)
 s=sub.add_parser('keyword');s.add_argument('query');s.add_argument('--as-of',required=True);s.add_argument('--top-k',type=int,default=10);s.add_argument('--cursor')
 sub.add_parser('build-index')
 sub.add_parser('sync-links')
 for name in ['research','adapt']:
  s=sub.add_parser(name);s.add_argument('ticker');s.add_argument('question');s.add_argument('--as-of',required=True);s.add_argument('--budget',help='JSON budget object');s.add_argument('--cursor');s.add_argument('--no-dense',action='store_true')
 s=sub.add_parser('news');s.add_argument('ticker',choices=['MU','TSM','NVDA']);s.add_argument('--as-of',required=True);s.add_argument('--query',default='');s.add_argument('--no-refresh',action='store_true')
 s=sub.add_parser('agent');s.add_argument('ticker',choices=['MU','TSM','NVDA']);s.add_argument('question');s.add_argument('--as-of',required=True);s.add_argument('--session',default='default');s.add_argument('--max-rounds',type=int,default=3)
 sub.add_parser('probe');s=sub.add_parser('import');s.add_argument('--confirm-existing-target',action='store_true')
 a=p.parse_args()
 if a.command=='sync-links':
  from neo4j_links import sync_links
  result=sync_links(a.config)
 elif a.command in ['build-index','keyword','research','adapt']:
  from research import ResearchEngine
  engine=ResearchEngine(a.backend,a.config)
  if a.command=='build-index':result=engine.index.report
  elif a.command=='keyword':result=engine.index.search(a.query,a.as_of,a.top_k,a.cursor)
  else:
   budget=json.loads(a.budget) if a.budget else {}
   if a.no_dense:budget['use_dense']=False
   result=engine.build(a.ticker,a.question,a.as_of,budget,cursor=a.cursor)
   if a.command=='adapt':
    from tradingagents_adapter import adapt_evidence_packet
    result=adapt_evidence_packet(result)
 elif a.command in ['probe','import']:
  from neo4j_store import probe,import_dataset
  result=probe(a.config) if a.command=='probe' else import_dataset(a.config,a.confirm_existing_target)
 elif a.command=='news':
  from news_tool import news_search
  result=news_search(a.ticker,a.as_of,a.query,refresh=not a.no_refresh)
 elif a.command=='agent':
  from agent import run_agent
  result=run_agent(a.ticker,a.question,a.as_of,a.session,a.backend,a.config,a.max_rounds)
 else:
  tools=CompanyTools(a.backend,a.config)
  if a.command=='company':result=tools.company_relations(a.ticker,a.as_of,a.question,a.page_size,a.cursor,a.max_hops,a.max_paths)
  elif a.command=='evidence':result=tools.evidence_lookup(a.ids,a.as_of)
  elif a.command=='scenario':result=tools.scenario(a.ticker,a.kind,a.as_of)
  else:result=tools.semantic_evidence(a.query,a.as_of,a.top_k)
 if a.out:dump(a.out,result)
 print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
