"""Curated assertions anchored to exact, locally archived original-publisher passages."""
from common import *
import re,csv
from datetime import timedelta

ENTITIES={};RELATIONS=[];EVIDENCE={};FAILED=[]
SOURCES={}

def node(id,name,kind='Technology',ticker=None):
 ENTITIES[id]={'entity_id':id,'name':name,'kind':kind,'ticker':ticker}
 return id

def evidence(sid,needle,page=None,section=''):
 if sid=='NVDA_2026_10K' and page in {92,96,97,103,104,124}:
  # Issuer standalone 10-K page map verified against the previously reviewed annual bundle.
  page={92:4,96:8,97:9,103:15,104:16,124:36}[page]
 source=SOURCES[sid]
 rows=readl(ROOT/source['text_path'])
 for block in rows:
  if page and block.get('pdf_page')!=page:continue
  text=re.sub(r'\s+',' ',block['text']).strip()
  at=text.lower().find(needle.lower())
  if at<0:continue
  # A compact exact excerpt; full page/paragraph remains addressable through source hash.
  start=max(0,at-80);end=min(len(text),at+max(480,len(needle)+100))
  eid='EV_'+sha(f'{sid}|{block.get("pdf_page")}|{block.get("paragraph")}|{start}|{end}')[:22]
  ev={'evidence_id':eid,'source_id':sid,'pdf_page':block.get('pdf_page'),'paragraph':block.get('paragraph'),
   'char_start':start,'char_end':end,'quote':text[start:end],'matched_text':needle,'section':section,
   'source_sha256':source['sha256'],'source_url':source['url'],'report_period':source.get('report_period'),
   'published_at':source.get('published_at'),'published_date':source.get('published_date'),
   'available_at':source['available_at'],'availability_basis':source['availability_basis'],
   'historical_eligible':source['historical_eligible'],'extraction_normalization':'collapse whitespace, Unicode original preserved',
   'table_policy':'quote includes local wording; table scope/unit only as stated on original page',
   'review_status':'curated_claim_with_exact_passage_match'}
  EVIDENCE[eid]=ev;return eid
 raise ValueError(f'evidence not found: {sid} page={page} needle={needle}')

def rel(sub,pred,obj,claim,sid,needle,page=None,scope='',conditions='',category='business',
        directness='not_a_supply_relation',direction='not_applicable',cls='original_disclosure',section='',
        valid_from=None,valid_to=None,status='as_disclosed',extra_evidence=None):
 try:
  eid=evidence(sid,needle,page,section)
  ids=[eid]
  for spec in extra_evidence or []:ids.append(evidence(**spec))
 except Exception as exc:
  FAILED.append({'subject':sub,'predicate':pred,'object':obj,'claim':claim,'error':str(exc)});return
 rid='TR_'+sha('|'.join([sub,pred,obj,sid,scope]))[:22]
 evs=[EVIDENCE[i] for i in ids]
 available=max(ev['available_at'] for ev in evs)
 if cls!='original_disclosure':available=max(available,now())
 RELATIONS.append({'relation_id':rid,'subject_id':sub,'predicate':pred,'object_id':obj,
  'claim':claim,'direction':direction,'category':category,'directness':directness,'business_scope':scope,
  'valid_from':valid_from,'valid_to':valid_to,'temporal_scope':'as disclosed in source period; no automatic assumption of perpetual continuation',
  'conditions':conditions or '仅限原文披露的业务与时间范围；未披露数量、份额、订单或排他性不得外推。',
  'evidence_class':cls,'status':status,'evidence_ids':ids,'source_ids':list(dict.fromkeys(ev['source_id'] for ev in evs)),
  'available_at':available,'derived_at':now() if cls!='original_disclosure' else None,
  'historical_eligible':cls=='original_disclosure' and all(ev['historical_eligible'] for ev in evs),
  'fact_edge_eligible':cls=='original_disclosure','review_status':'curated_with_matched_evidence'})

def prepare_sources():
 global SOURCES
 sources=load(ROOT/'output/sources.json')
 for s in sources:
  if s.get('access_status')!='archived_original_publisher_copy':continue
  if s.get('published_at'):
   s['sec_acceptance_api_raw']=s['published_at'];s['actual_public_at']=None
   s['public_time_status']='API acceptance retained; timezone/dissemination not independently verified; no intraday eligibility'
   # A primary-index/API discrepancy was found for MU FY2026. Do not pretend the
   # API's Z string is verified actual public time, or resolve it by guessing.
   s['published_at']=None
   s['available_at']=(parse_time(s['published_date']+'T00:00:00+00:00')+timedelta(days=1,hours=12)).isoformat()
   s['availability_basis']='SEC filing date, conservative world-wide end-of-day + accession-specific form/period copy; exact public-time audit pending'
   s['historical_eligible']=s.get('historical_version_verified',False)
  elif s.get('published_date') and s['kind'] not in ['technology','corrected_transcript','annual_bundle']:
   # End publication day in UTC-12 is later than the end of that date in any publishing jurisdiction.
   s['available_at']=(parse_time(s['published_date']+'T00:00:00+00:00')+timedelta(days=1,hours=12)).isoformat()
   s['availability_basis']='date only; publication day fully elapsed worldwide (UTC-12); no intraday eligibility'
   # Official dated releases are conservatively timed; historical content versions remain unverified.
   s['historical_eligible']=False
  else:
   s['available_at']=s['retrieved_at'];s['availability_basis']='mutable/undated/corrected/bundled source: retrieval time only'
   s['historical_eligible']=False
  SOURCES[s['source_id']]=s
  if s['kind']=='annual_bundle':
   s['include_in_rag']=False;s['processing_note']='Reference edition retained; standalone 10-K is canonical. Proxy/letter not backdated.'
 dump(ROOT/'output/source_catalog.json',sources)

def build():
 prepare_sources()
 for t,n in [('MU','美光 Micron'),('TSM','台积电 TSMC'),('NVDA','英伟达 NVIDIA'),('SKH','SK hynix'),('SAMSUNG','Samsung Electronics'),('AMD','AMD'),('INTC','Intel'),('GOOGL','Alphabet'),('AMZN','Amazon'),('MSFT','Microsoft'),('FOXCONN','Hon Hai / Foxconn'),('WISTRON','Wistron'),('FABRINET','Fabrinet')]:node(t,n,'Company',t if t in ['MU','TSM','NVDA','AMD','INTC','GOOGL','AMZN','MSFT'] else None)
 for id,n,k in [
 ('MU_CMBU','云存储业务 CMBU','Business'),('MU_CDBU','核心数据中心业务 CDBU','Business'),('MU_MCBU','移动及客户端业务 MCBU','Business'),('MU_AEBU','汽车及嵌入式业务 AEBU','Business'),
 ('NVDA_COMPUTE','Compute & Networking','Business'),('NVDA_GRAPHICS','Graphics','Business'),('NVDA_DC','NVIDIA Data Center 平台','Business'),
 ('TSM_FOUNDRY','晶圆代工','Business'),('TSM_ADV_PACK','先进封装与3DFabric','Business'),
 ('DRAM','DRAM','ProductCategory'),('NAND','NAND','ProductCategory'),('HBM','HBM','Technology'),('HBM3E','HBM3E','Technology'),('HBM4','HBM4','Technology'),('HBM4E','HBM4E / NVHBM','Technology'),
 ('MU_HBM3E','Micron HBM3E','Product'),('MU_HBM4','Micron HBM4','Product'),('DDR5','DDR5 server DRAM','ProductCategory'),('LPDDR','LPDDR','ProductCategory'),('SSD','Data center SSD','ProductCategory'),
 ('H200','NVIDIA H200','Product'),('BLACKWELL','NVIDIA Blackwell','Product'),('VERA_RUBIN','NVIDIA Vera Rubin','Product'),('CUDA','CUDA software ecosystem','Technology'),('NVLINK','NVLink / networking','Technology'),
 ('COWOS','TSMC CoWoS','Technology'),('SOIC','TSMC-SoIC','Technology'),('INFO','TSMC InFO','Technology'),('N4NP','TSMC custom 4NP process','Technology'),('EUV','EUV lithography','Technology'),
 ('SI_WAFER','Silicon wafers','Input'),('CHEM_GAS','Electronic chemicals and gases','Input'),('TOOLS','Manufacturing equipment / unnamed suppliers','Input'),('POWER_WATER','Electricity and water','Resource'),
 ('MU_YIELD','MU manufacturing yield / process complexity','Constraint'),('TSM_UTIL','TSMC capacity utilization / fixed cost absorption','Constraint'),('TSM_CAPACITY','TSMC leading-edge and packaging capacity','Constraint'),
 ('NVDA_CAPACITY','NVIDIA supply allocation / manufacturing lead times','Constraint'),('DC_POWER','Data center power / cooling / build-out','Constraint'),('DEMAND','AI / cloud customer demand','Demand'),
 ('TSM_ANON_LARGEST','TSMC 2025 largest customer — anonymous','AnonymousCustomer'),('NVDA_ANON','NVIDIA disclosed direct customer groups — anonymous','AnonymousCustomer'),
 ('OTHER_FOUNDRIES','Other foundry providers and IDMs — unnamed','CompetitorCategory'),('CUSTOM_ASIC','Internally designed AI accelerators / custom ASICs','TechnologyAlternative'),
 ('BIS_HBM_POLICY','2024 U.S. HBM export controls','Policy'),('BIS_H200_POLICY','2026 advanced computing case-by-case licensing','Policy'),('MU_CHINA','China critical infrastructure restrictions reported by Micron','Constraint'),
 ('HBM_SUPPLY','Qualified HBM availability','Constraint'),('PACKAGING_SUPPLY','Qualified advanced packaging availability','Constraint')]:node(id,n,k)
 # Business maps anchored to complete current filing sections, not the chemical standard schema.
 mu='MU_2026_10K';nv='NVDA_2026_10K';ts='TSM_2025_20F'
 for id,needle,page in [('MU_CMBU','CMBU is focused on',9),('MU_CDBU','CDBU is focused on',9),('MU_MCBU','MCBU is focused on',10),('MU_AEBU','AEBU is focused on',11)]:
  rel('MU','HAS_BUSINESS',id,ENTITIES[id]['name'],mu,needle,page,scope='FY2026 business-unit map',section='Item 1 / Products by Business Unit and Market')
 for owner,p,needle,page in [('MU_CMBU','HBM','HBM for all data center customers',9),('MU_CMBU','DDR5','DDR5',9),('MU_CDBU','SSD','SSDs',9),('MU_MCBU','LPDDR','LPDDR solutions',10),('MU_AEBU','DRAM','module DRAM',11)]:
  rel(owner,'OFFERS',p,'业务产品覆盖',mu,needle,page,scope='FY2026 disclosed product mix',section='Item 1 / Products by Business Unit and Market')
 for p in ['DRAM','NAND']:
  rel('MU','MANUFACTURES',p,'美光生产存储产品',mu,p,7,scope='Memory and storage portfolio',section='Item 1 / Overview')
 rel('MU','USES_TECHNOLOGY','EUV','1γ节点采用EUV，后续采用程度取决于路线执行',mu,'first DRAM node incorporating EUV',16,scope='DRAM process nodes; no named equipment supplier inferred',category='technical_dependency',section='Research and Development')
 rel('MU','CONSTRAINED_BY','MU_YIELD','制造良率、工艺复杂度、利用率影响制造成本',mu,'primary determinants of manufacturing cost',13,scope='DRAM/NAND manufacturing cost',category='constraint',section='Manufacturing')
 for target,needle in [('SI_WAFER','silicon wafers'),('CHEM_GAS','chemicals'),('TOOLS','manufacturing equipment')]:
  rel('MU','DEPENDS_ON_INPUT',target,'原料或设备是生产投入；未识别具体交易对手',mu,needle,14,scope='Memory manufacturing; supplier names not disclosed here',category='technical_dependency',section='Materials and suppliers')
 for target,needle in [('SKH','SK hynix'),('SAMSUNG','Samsung Electronics')]:
  rel('MU','COMPETES_WITH',target,'年报点名存储市场竞争者',mu,needle,12,scope='Memory/storage competition; not proof every product is interchangeable',category='competition',section='Competitive Conditions')
 rel('MU','CONSTRAINED_BY','MU_CHINA','年报披露2023年CAC决定对销售造成影响',mu,'May 2023 decision',12,scope='As reported FY2026; not a complete current legal assessment',category='policy_constraint',section='Competitive Conditions')
 # NVDA named suppliers: actual procurement language; do not extrapolate memory type/volume.
 for sup,needle in [('MU','Micron Technology'),('SKH','SK Hynix'),('SAMSUNG','Samsung')]:
  rel(sup,'SUPPLIES_TO','NVDA','英伟达年报披露向该公司采购memory',nv,'We purchase memory from',96,scope='Memory procurement, FY2026; exact generation, share and purchase amounts not disclosed',directness='direct_named_procurement',category='supply',section='Item 1 / Manufacturing',conditions='原文点名采购memory；不推断全部为HBM、不推断独家或各供应商份额。')
 rel('TSM','MANUFACTURES_FOR','NVDA','英伟达披露TSMC生产半导体晶圆',nv,'We utilize foundries',96,scope='Semiconductor wafer fabrication',directness='direct_named_manufacturing',category='supply',section='Item 1 / Manufacturing')
 for supplier in ['FOXCONN','WISTRON','FABRINET']:
  rel(supplier,'ASSEMBLES_FOR','NVDA','英伟达点名独立承包商执行组装测试封装',nv,'We engage with independent subcontractors',96,scope='Assembly/testing/packaging as disclosed; not each vendor performs every operation',directness='direct_named_subcontractor',category='supply',section='Manufacturing')
 for id in ['NVDA_COMPUTE','NVDA_GRAPHICS']:
  rel('NVDA','HAS_BUSINESS',id,'英伟达披露两个经营分部',nv,'Our two operating segments',124,scope='FY2026 operating segments',section='MD&A / Our Company and Our Businesses')
 rel('NVDA_COMPUTE','SERVES','NVDA_DC','计算与网络业务服务数据中心市场',nv,'Data Center',92,scope='Data center platform; market platform is not a separate reportable segment',section='Item 1 / Business')
 rel('NVDA','USES_TECHNOLOGY','CUDA','CUDA是英伟达加速计算的软件平台',nv,'CUDA development platform',92,scope='Full-stack accelerated computing platform',category='technical_dependency',section='Item 1 / Business')
 rel('NVDA','USES_TECHNOLOGY','COWOS','英伟达披露使用CoWoS半导体封装',nv,'We utilize CoWoS technology',96,scope='Semiconductor packaging, not an exclusive capacity allocation contract',category='technical_dependency',section='Manufacturing')
 rel('NVDA','CONSTRAINED_BY','NVDA_CAPACITY','生产交期与供应能力制约供需匹配',nv,'Long manufacturing lead times',103,scope='Manufacturing and demand estimation risks',category='constraint',section='Item 1A / Demand, Supply and Manufacturing')
 rel('NVDA','CONSTRAINED_BY','DC_POWER','电力和数据中心基础设施可能限制部署',nv,'infrastructure projects',104,scope='Customer deployment capacity; not proof of a specific outage',category='constraint',section='Item 1A / Risk Factors')
 for competitor,needle in [('AMD','Advanced Micro Devices'),('INTC','Intel Corporation'),('AMZN','Amazon'),('GOOGL','Alphabet'),('MSFT','Microsoft')]:
  rel('NVDA','COMPETES_WITH',competitor,'年报列示竞争者/内部自研芯片竞争',nv,needle,97,scope='Accelerated computing / custom chip competition as disclosed',category='competition',section='Item 1 / Competition')
 rel('NVDA','FACES_ALTERNATIVE','CUSTOM_ASIC','客户自研AI硬件形成竞争替代',nv,'internal teams designing hardware',97,scope='Workload-dependent substitution; qualification/software economics matter',category='competition',section='Competition')
 # Product-specific links: separate announcement/design stage from aggregate procurement facts.
 rel('MU','OFFERS','MU_HBM3E','美光HBM3E开始量产公告','MU_H200_2024','begun volume production',scope='HBM3E, Feb 2024',section='Announcement')
 rel('MU_HBM3E','ANNOUNCED_FOR','H200','Micron 24GB 8H HBM3E被宣布用于NVIDIA H200','MU_H200_2024','will be part of NVIDIA H200',scope='24GB 8H HBM3E / H200; announced design-in, not all H200 units',directness='named_product_design_in',category='product_design',status='announced_design_in',section='Announcement',valid_from='2024-02-26')
 rel('NVDA','OFFERS','H200','NVIDIA H200产品属于其数据中心平台','NVDA_H200','H200',scope='H200 product page at retrieval; mutable technical source',section='Product overview')
 rel('H200','REQUIRES_TECHNOLOGY','HBM3E','H200使用HBM3E显存','NVDA_H200','HBM3e',scope='H200 generation; not HBM4 or all future GPU generations',category='technical_dependency',section='Memory specifications')
 rel('MU','OFFERS','MU_HBM4','美光公告2026年Q1已开始HBM4批量出货','MU_HBM4_2026','volume shipment',scope='HBM4 36GB 12H, Q1 calendar 2026',status='issuer_reports_volume_shipment',section='Product announcement')
 rel('MU_HBM4','DESIGNED_FOR','VERA_RUBIN','Micron HBM4为NVIDIA Vera Rubin设计','MU_HBM4_2026','designed for NVIDIA Vera Rubin',scope='HBM4 36GB 12H; no undisclosed exclusive share',category='product_design',directness='named_product_design_in',section='Product announcement')
 rel('NVDA','OFFERS','VERA_RUBIN','英伟达季度公告称Vera Rubin已进入量产','NVDA_2027_Q2_RELEASE','Vera Rubin, now in full production',scope='Issuer statement August 26, 2026; production status is not proof of fulfilled customer orders',status='issuer_reports_full_production',section='Quarterly earnings release / CEO statement')
 rel('MU_HBM4','IMPLEMENTS','HBM4','Micron HBM4为HBM4技术的具体产品','MU_HBM4_2026','HBM4 36GB 12H',scope='36GB 12-high product; no universal interchangeability',category='technical_dependency',section='Product announcement')
 rel('MU','CO_DEVELOPS','HBM4E','MU披露与NVIDIA合作定制HBM4E/NVHBM','MU_2026_Q4_REMARKS','custom-HBM4E',4,scope='Next-generation implementation; future adoption, not present shipments',category='collaboration',status='management_forward_looking',section='Prepared remarks / HBM')
 rel('NVDA','OFFERS','BLACKWELL','Blackwell产品平台','NVDA_BLACKWELL','Blackwell',scope='Platform announced 18 March 2024',section='Product announcement')
 rel('BLACKWELL','MANUFACTURED_USING','N4NP','Blackwell使用TSMC定制4NP工艺','NVDA_BLACKWELL','custom-built 4NP TSMC process',scope='Blackwell generation only',category='technical_dependency',section='World’s Most Powerful Chip')
 rel('TSM','PROVIDES_PROCESS','N4NP','TSMC提供Blackwell定制4NP制造工艺','NVDA_BLACKWELL','custom-built 4NP TSMC process',scope='Blackwell generation',category='business',section='Blackwell technology')
 rel('MU','COLLABORATES_WITH','TSM','美光与TSMC在HBM/CoWoS兼容性上合作，不等于TSMC代工MU全部HBM逻辑','TSM_3DFABRIC_ALLIANCE','Micron has been working closely',scope='HBM generations / CoWoS compatibility',category='collaboration',directness='technical_collaboration_not_procurement',section='Alliance / Micron statement',conditions='仅证实兼容性协作；不是晶圆采购、供货量或代工订单证据。')
 # TSM business, upstream inputs, constraints and customer anonymity.
 rel('TSM','HAS_BUSINESS','TSM_FOUNDRY','TSMC提供晶圆代工服务',ts,'Our Foundry Services',24,scope='Dedicated foundry services',section='Item 4 / Business Overview')
 rel('TSM','HAS_BUSINESS','TSM_FOUNDRY','FY2024已披露的晶圆代工业务','TSM_2024_20F','Our Foundry Services',scope='FY2024 dedicated foundry services; historical snapshot',section='Item 4 / Business Overview')
 rel('TSM','HAS_BUSINESS','TSM_ADV_PACK','TSMC提供3DFabric先进堆叠和封装服务',ts,'3DFabric',24,scope='Advanced silicon stacking and packaging',section='Range of Services')
 for p,needle in [('COWOS','CoWoS'),('SOIC','SoIC'),('INFO','InFO')]:
  rel('TSM_ADV_PACK','OFFERS',p,'TSMC先进封装技术组合',ts,needle,24,scope='Different technologies for distinct use cases; no drop-in substitution implied',section='Range of Services')
 for p,needle in [('SI_WAFER','silicon wafers'),('CHEM_GAS','chemicals'),('TOOLS','equipment')]:
  rel('TSM','DEPENDS_ON_INPUT',p,'制造投入依赖，具体供货商未在所引段落点名',ts,needle,28 if p!='TOOLS' else 15,scope='Raw materials/equipment; unnamed supplier category',category='technical_dependency',section='Raw Materials / supplier risk')
 rel('TSM','CONSTRAINED_BY','POWER_WATER','电力供水中断可影响生产',ts,'water, electricity',17,scope='Manufacturing continuity; hypothetical risk, not observed outage',category='constraint',section='Risk factors / operational disruption')
 rel('TSM','CONSTRAINED_BY','TSM_UTIL','高固定成本使利用率影响单位成本',ts,'fixed manufacturing costs',34,scope='Capacity utilization and inventory unit cost',category='constraint',section='Operating and Financial Review')
 rel('TSM','SERVES','TSM_ANON_LARGEST','2025最大客户以匿名方式披露',ts,'Our largest customer in 2023, 2024 and 2025',14,scope='Largest customer concentration; do not identify it as Apple or NVIDIA',category='customer',directness='direct_anonymous_customer',section='Customer concentration',conditions='匿名客户保留匿名，不能以市场传闻识别公司；2025年度口径。')
 rel('TSM','COMPETES_WITH','OTHER_FOUNDRIES','TSMC披露与其他代工厂及IDM竞争',ts,'other foundry service providers',27,scope='Foundry process / geography-dependent competition',category='competition',section='Competition')
 node('NVDA_CUSTOMER_A_2025','NVIDIA FY2025 Direct Customer A — anonymous','AnonymousCustomer')
 rel('NVDA','SERVES','NVDA_CUSTOMER_A_2025','FY2025客户集中度披露匿名Direct Customer A','NVDA_2025_10K','Direct Customer A',41,scope='FY2025 only; 12% total revenue table; not a claim about FY2026 identity',category='customer',directness='direct_anonymous_customer',section='Note 2 / Revenue concentration',conditions='保持匿名；不能映射为Microsoft、Amazon或其他公司；不把FY2025客户代码当成跨年度同一实体。')
 for product,needle in [('DRAM','DRAM:'),('NAND','NAND:')]:
  rel('MU','MANUFACTURES',product,'FY2025已披露的存储产品业务','MU_2025_10K',needle,scope='FY2025 disclosed product portfolio; do not import later unit definitions',section='Item 1 / Products')
 node('NVDA_COMMITMENTS','NVIDIA supply/capacity and infrastructure commitments','Constraint')
 node('NVDA_H200_RESTRICTIONS','H200 licensing, PRC restrictions and excess inventory exposure','Constraint')
 rel('NVDA','CONSTRAINED_BY','NVDA_COMMITMENTS','季度报告披露供应、基础设施及合作生态战略承诺','NVDA_2027_Q2_10Q','strategic commitments across',17,scope='FY2027 Q2; contractual commitments are not all current-period expense or shipped orders',category='constraint',section='Note / Commitments and Contingencies')
 rel('NVDA','CONSTRAINED_BY','NVDA_H200_RESTRICTIONS','获得部分许可证并不确保对华H200销售或消化库存','NVDA_2027_Q2_10Q','restricted by the PRC government',27,scope='Quarter ended July 26, 2026; H200 program as reported',category='policy_constraint',section='MD&A / Export controls',conditions='区分许可、实际出货、需求、PRC限制及库存费用；不外推为所有产品或当前全部政策状态。')
 rel('NVDA','EXPOSED_TO','DEMAND','业务交付取决于客户需求估计与供需匹配',nv,'estimate customer demand accurately',103,scope='Customer demand risk; not a measured demand shock',category='constraint',section='Risk Factors / Supply and Demand')
 rel('TSM','REPORTS_OUTLOOK','TSM_CAPACITY','TSMC管理层披露产能扩张与设备供应规划','TSM_2026_Q3_CALL','tool supply is tight',4,scope='Management view October 2026; not completed capacity',category='constraint',status='management_forward_looking',section='CEO prepared remarks')
 rel('TSM','CONSTRAINED_BY','DC_POWER','产能规划考虑美国数据中心电力及建设情况','TSM_2026_Q3_CALL','power supply situation',5,scope='Capacity planning demand-side constraint; not TSM fabrication electricity itself',category='constraint',section='Capacity expansion strategy')
 # Policy text and conditional scenarios are kept separate from named supplier facts.
 rel('BIS_HBM_POLICY','REGULATES','HBM','2024规则引入特定HBM出口管制','FR_HBM_2024','high bandwidth memory',scope='2024-28270 text; ECCN/threshold/destination/end-use/exceptions determine applicability',category='policy_constraint',section='Federal Register / HBM controls',valid_from='2024-12-31',conditions='须核对技术参数、目的地、最终用户、许可例外及后续修订；本版不是完整当前EAR法律核验。')
 rel('BIS_H200_POLICY','REGULATES','H200','2026规则对符合条件的先进计算芯片实施逐案审查','FR_H200_2026','H200',scope='Rule 2026-00789; not blanket export permission',category='policy_constraint',section='Final Rule / License review policy',valid_from='2026-01-15',conditions='许可审查不等于获得许可证；受技术阈值、最终用途、客户合规和检测等条件约束；后续规则需单独核验。')
 # Scenario edges express a mechanism under assumptions, never a realized causal effect.
 rel('HBM_SUPPLY','MAY_CONSTRAIN','H200','已认证HBM不足可能约束对应H200交付','MU_H200_2024','will be part of NVIDIA H200',scope='Scenario: HBM supply restriction for this qualified design',category='impact_mechanism',cls='model_inference',direction='negative_for_available_output',conditions='假设库存缓冲不足、替代供应商未认证或无法及时切换，且其他瓶颈未先行限制产出。',extra_evidence=[{'sid':nv,'needle':'We purchase memory from','page':96,'section':'Manufacturing'}])
 rel('HBM_SUPPLY','MAY_CONSTRAIN','VERA_RUBIN','已认证HBM4不足可能约束相关Vera Rubin配置交付','MU_HBM4_2026','designed for NVIDIA Vera Rubin',scope='Scenario: qualified HBM4 design for Vera Rubin; not all platform configurations',category='impact_mechanism',cls='model_inference',direction='negative_for_available_output',conditions='仅针对依赖相关已认证HBM4的配置；假设库存不足、其他供应商未能补位且需求与其他组件不先行限产，不直接推断收入、利润或股价。',extra_evidence=[{'sid':'NVDA_2027_Q2_RELEASE','needle':'Vera Rubin, now in full production','section':'Quarterly product statement'}])
 rel('PACKAGING_SUPPLY','MAY_CONSTRAIN','NVDA','合格CoWoS产能变化可能影响英伟达交付能力',nv,'We utilize CoWoS technology',96,scope='Scenario: CoWoS availability changes',category='impact_mechanism',cls='model_inference',direction='conditional',conditions='产能须在相关工艺认证、良率、HBM/基板/晶圆与电力配套、客户需求和交期窗口内可用；扩产不等于即时利润增加。',extra_evidence=[{'sid':ts,'needle':'CoWoS','page':24,'section':'Advanced packaging'}])
 rel('HBM_SUPPLY','MAY_AFFECT','MU','HBM供应紧张对MU收入利润方向依赖其可供货能力与价格成本','MU_2026_10K','manufacturing yield',13,scope='Scenario only; no estimated stock return',category='impact_mechanism',cls='model_inference',direction='conditional',conditions='自身良率/产能受限时可能损失销量；有余量且合同允许重定价时可能受益；不自动推导股价。')
 rel('COWOS','HAS_POTENTIAL_ALTERNATIVE','INFO','封装技术路线可比较但不是无条件可替换',ts,'InFO',24,scope='Research comparison of packaging routes, not certified product migration',category='technology_alternative',cls='research_view',direction='conditional',conditions='需要重设计、封装尺寸/互连/HBM兼容性和良率验证；原文仅列技术组合，并未披露某GPU可直接切换。')
 # Edges for shortage/relaxation scenarios point through an explicit constrained resource.
 rel('NVDA','EXPOSED_TO','PACKAGING_SUPPLY','NVIDIA对合格封装供应的技术暴露',nv,'We utilize CoWoS technology',96,scope='Technology exposure, not a named contract',category='technical_dependency',directness='indirect_resource_dependency',section='Manufacturing')
 rel('H200','EXPOSED_TO','HBM_SUPPLY','H200对HBM3E可得性的技术暴露','NVDA_H200','HBM3e',scope='Technology exposure; supplier fungibility unverified',category='technical_dependency',directness='indirect_resource_dependency',section='H200 memory')
 dump(ROOT/'output/entities.json',list(ENTITIES.values()));dump(ROOT/'output/relations.json',RELATIONS)
 dump(ROOT/'output/evidence.json',list(EVIDENCE.values()));dump(ROOT/'output/curation_gaps.json',FAILED)
 # Structured exports use JSON cells for nested lists, retaining the full JSON artifacts too.
 for name,rows in [('entities',list(ENTITIES.values())),('relations',RELATIONS),('evidence',list(EVIDENCE.values())),('sources',list(SOURCES.values()))]:
  columns=list(dict.fromkeys(k for r in rows for k in r))
  with (ROOT/'output'/f'{name}.csv').open('w',encoding='utf-8-sig',newline='') as f:
   w=csv.DictWriter(f,fieldnames=columns);w.writeheader()
   for row in rows:w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in row.items()})
 print(json.dumps({'entities':len(ENTITIES),'relations':len(RELATIONS),'evidence':len(EVIDENCE),'curation_gaps':FAILED},ensure_ascii=False),flush=True)

if __name__=='__main__':build()
