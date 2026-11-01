"""Export dossiers, gaps and execution status without fabricating database execution."""
from common import *
import csv,hashlib
from collections import Counter
from tools import CompanyTools

def csvout(filename,rows):
 if not rows:return
 cols=list(dict.fromkeys(k for row in rows for k in row))
 with (ROOT/'output'/filename).open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
  for r in rows:w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in r.items()})

def run():
 sources=load(ROOT/'output/source_catalog.json');sec=load(ROOT/'output/sec_sources.json')
 csvout('sources.csv',sources)
 relations=load(ROOT/'output/relations.json');evidence=load(ROOT/'output/evidence.json');entities=load(ROOT/'output/entities.json')
 imported=load(ROOT/'output/neo4j_import_report.json').get('executed',False) if (ROOT/'output/neo4j_import_report.json').exists() else False
 desktop=load(ROOT/'output/desktop_target_status.json') if (ROOT/'output/desktop_target_status.json').exists() else {}
 ui=load(ROOT/'output/desktop_validation.json') if (ROOT/'output/desktop_validation.json').exists() else {}
 T=CompanyTools();profiles=[];company_rows=[]
 for ticker in ['MU','TSM','NVDA']:
  p=T.company_relations(ticker,'current',page_size=20);profiles.append(p)
  ss=[s for s in sources if s['ticker']==ticker]
  company_rows.append({'ticker':ticker,'annual_sources':[s['source_id'] for s in ss if s['kind']=='annual'],
   'latest_quarter_sources':[s['source_id'] for s in ss if s['kind'] in ['quarterly','quarterly_update','corrected_transcript']],
   'major_businesses':[r['object_name'] for r in p['main_business']],
   'named_direct_upstream':[r['subject_name'] for r in p['direct_upstream']],
   'direct_downstream_or_anonymous':[r['object_name'] for r in p['direct_downstream']],
   'enumerated_paths':p['pagination']['enumerated_paths'],'path_page_size':20,'gaps':p['gaps']})
  dump(ROOT/'output'/f'company_{ticker}.json',p)
  lines=[f'科技产业链公司关系档案｜{ticker}',f"分析时间：{p['analysis_time']}；数据源：{p['backend']}；范围：本次审核知识集。",'']
  for label,key in [('主要业务','main_business'),('具名直接上游','direct_upstream'),('直接下游（匿名保持匿名）','direct_downstream'),('技术与投入依赖／协作','technical_dependencies'),('竞争与替代','competition_and_alternatives'),('经营约束','operating_constraints')]:
   lines.append(label)
   if not p[key]:lines.append('  所审核资料不足；未补造交易对手或关系。')
   for r in p[key]:
    lines.extend([f"  {r['subject_name']} — {r['predicate']} → {r['object_name']}",f"    {r['claim']}；类别：{r['evidence_class']}；范围：{r['business_scope']}",f"    条件：{r['conditions']}",f"    可用时间：{r['available_at']}；有效起止：{r['valid_from']} / {r['valid_to']}（空值表示未证实起止）"])
    for eid in r['evidence_ids']:
     e=T.evidence[eid];where=f"PDF物理页{e['pdf_page']}" if e['pdf_page'] else f"HTML段落{e['paragraph']}"
     lines.append(f"    出处：{e['source_id']}，{where}，字符{e['char_start']}:{e['char_end']}，{eid}，{e['source_url']}")
   lines.append('')
  lines.extend(['完整有序路径使用CLI company分页查询；本文仅列公司直接关联的业务地图，间接路径与条件推断另见company_*.json及scenario_validation.json。','资料缺口：',*['  '+gap for gap in p['gaps']]])
  (ROOT/'output'/f'company_dossier_{ticker}.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
 dump(ROOT/'output/company_profiles.json',profiles);csvout('company_coverage.csv',company_rows);csvout('sec_filings.csv',sec)
 referenced={id for r in relations for id in [r['subject_id'],r['object_id']]}
 entity_coverage=[{**e,'reviewed_relation_count':sum(e['entity_id'] in [r['subject_id'],r['object_id']] for r in relations),
  'status':'referenced_in_reviewed_assertions' if e['entity_id'] in referenced else 'candidate_no_reviewed_edges'} for e in entities]
 dump(ROOT/'output/entity_coverage.json',entity_coverage)
 gaps=[]
 for s in sources:
  if not s.get('text_path'):gaps.append({'scope':s['source_id'],'gap':'原文正文未取得；仅保留链接','status':s['access_status'],'url':s['url']})
  if not s.get('historical_eligible'):gaps.append({'scope':s['source_id'],'gap':'历史内容版本/公开时刻未独立核验；历史模式保守排除','status':'temporal_restricted','url':s['url']})
  if s.get('sec_acceptance_api_raw'):gaps.append({'scope':s['source_id'],'gap':'SEC接受时间原值保留，实际公开时分秒未核验；可用时间使用日期级保守值','status':'intraday_not_ready','url':s.get('sec_original_url',s['url'])})
 copied={s.get('accession') for s in sources if s.get('text_path') and s['kind'] in ['annual','quarterly']}
 for row in sec:
  if row['accessionNumber'] not in copied:gaps.append({'scope':row['source_id'],'gap':'SEC主文档下载返回403，部分有官方季度更新但不等于完整8-K/6-K原件','status':'link_only_sec','url':row['url']})
 for ticker in ['MU','TSM']:
  gaps.append({'scope':ticker,'gap':'所审核段落未识别具名直接上游供货商；没有将设备/原料/联盟成员直接变为供应商','status':'not_disclosed_in_reviewed_evidence','url':None})
 gaps.extend([
  *([] if imported else [{'scope':'Neo4j','gap':'用户已指定现有Desktop Finance实例并配置本地连接；待启动连接、导入及数据库检索验证完成','status':'pending_finance_import_validation','url':None}]),
  {'scope':'Agent','gap':'规则式有界工具编排已运行；未配置/实测LLM规划器和自然语言综合生成','status':'not_implemented_llm_generation','url':None},
  {'scope':'News','gap':'独立工具已实测SEC事件元数据刷新；新事件正文核验及全市场实时新闻尚不完整','status':'partial_live_metadata','url':None},
  {'scope':'Policy','gap':'已归档规则原文；尚未建立完整后续修订时间线、个案许可证与现行法规全量核验','status':'partial_policy_history','url':None},
  {'scope':'Tables','gap':'保留原页/段/单位上下文；未完成全部跨页表格及会计口径的结构化重建','status':'partial_table_extraction','url':None}])
 dump(ROOT/'output/gaps.json',gaps);csvout('gaps.csv',gaps)
 audit={'MU_FY2026':{'index_url':'https://www.sec.gov/Archives/edgar/data/723125/000072312526000023/0000723125-26-000023-index.html',
  'index_accepted_display':'2026-10-09 08:21:00','api_acceptanceDateTime':'2026-10-09T16:21:00.000Z',
  'resolution':'未猜测时区；不声称实际公开时分秒，日期级保守可用时间2026-10-10T12:00:00+00:00'},
  'vendor_financial_feed':{'source_file':'raw/NVDA_public_financial_feed.json','discovery_only':True,
   'warning':'ReportYear/ReportDate为厂商日历标签，未当作实际报告期或公开日期；FY2027 Q2真实期末取SEC/10-Q July 26, 2026。'},
  'first_global_public_time_verified':False,'strict_intraday_backtest_ready':False}
 dump(ROOT/'output/public_time_audit.json',audit)
 numeric=[{'ticker':t,'source':'SEC companyfacts XBRL','url':f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json',
  'method':'HTTP GET with identified User-Agent, then filter accession, form, start/end, filed, units and frame; preserve original vintage',
  'downloaded':False,'rag_embedded':False} for t,cik in [('MU',723125),('TSM',1046179),('NVDA',1045810)]]
 (ROOT/'numeric').mkdir(exist_ok=True);dump(ROOT/'numeric/source_registry.json',numeric)
 # A visually verified static report table, kept outside text embedding/continuous prices.
 sample=[{'ticker':'NVDA','report_period':'FY2025','period_end':'2025-01-26','metric':'segment_revenue','segment':segment,
  'value':value,'unit':'USD million','source_id':'NVDA_2025_10K','pdf_page':41,'table':'Revenue by Reportable Segments',
  'available_at':next(s['available_at'] for s in sources if s['source_id']=='NVDA_2025_10K'),
  'actual_public_at':None,'vintage_status':'issuer accession-matched filing copy; intraday time unverified'} for segment,value in [('Compute & Networking',116193),('Graphics',14304),('Total',130497)]]
 dump(ROOT/'numeric/reported_snapshot_example.json',sample)
 import_report=ROOT/'output/neo4j_import_report.json'
 if not imported:dump(import_report,{'executed':False,'status':'pending_finance_import_validation',
  'desktop_instance_name':'Finance','desktop_instance_id':'164b5ace-03b4-45cd-bc0f-ac9c533d6ef4',
  'uri':'bolt://localhost:7687','database':'neo4j',
  'new_dbms_created':False,'new_database_created':False,'old_data_deleted':False,
  'pending_action':'probe Finance using configured tech_rag/.env; import and validate before claiming completion'})
 report={'as_of_date':ASOF_DATE,'generated_at':now(),'core_companies':['MU','TSM','NVDA'],'source_records':len(sources),
  'archived_sources':sum(bool(s.get('text_path')) for s in sources),'sec_filing_metadata_records':len(sec),
  'canonical_annual_reports':sum(s['kind']=='annual' for s in sources),'entities':len(entities),'entities_with_reviewed_relations':len(referenced),
  'relations':len(relations),'evidence':len(evidence),'relation_classes':dict(Counter(r['evidence_class'] for r in relations)),
  **load(ROOT/'output/index_report.json'),'local_validation':load(ROOT/'output/validation_local.json')['passed'],
  'neo4j_import_executed':load(import_report)['executed'],'desktop_ui_verified':ui.get('passed',False),
  'desktop_existing_instance_verified':desktop.get('home_verified_against_user_selected_instance',False),'desktop_instance':'Finance','local_validation_checks':load(ROOT/'output/validation_local.json')['check_count'],
  'neo4j_validation':load(ROOT/'output/validation_neo4j.json')['passed'] if (ROOT/'output/validation_neo4j.json').exists() else False,
  'neo4j_validation_checks':load(ROOT/'output/validation_neo4j.json')['check_count'] if (ROOT/'output/validation_neo4j.json').exists() else 0,
  'neo4j_readback_audit':load(ROOT/'output/neo4j_readback_audit.json')['passed'] if (ROOT/'output/neo4j_readback_audit.json').exists() else False,
  'idempotency_verified':load(ROOT/'output/neo4j_idempotency_report.json')['passed'] if (ROOT/'output/neo4j_idempotency_report.json').exists() else False,
  'neo4j_dataset_counts':load(ROOT/'output/neo4j_idempotency_report.json')['after'] if (ROOT/'output/neo4j_idempotency_report.json').exists() else None,
  'llm_generation_executed':False,'oil_artifacts_modified':False,'model_downloaded_again':False}
 dump(ROOT/'output/manifest.json',report)
 print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':run()
