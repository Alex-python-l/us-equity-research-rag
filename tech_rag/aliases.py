"""Deterministic research identity and fixed bilingual vocabulary (no learned identity)."""
import re

COMPANIES = {
 'MU': {'entity_id':'MU','name':'Micron Technology, Inc.','name_zh':'美光', 'aliases':['MU','Micron','美光','美光科技'],
        'security':{'symbol':'MU','exchange':'NASDAQ','instrument_type':'common_stock'}},
 'TSM': {'entity_id':'TSM','name':'Taiwan Semiconductor Manufacturing Company Limited','name_zh':'台积电', 'aliases':['TSM','TSMC','台积电','台積電'],
         'security':{'symbol':'TSM','exchange':'NYSE','instrument_type':'ADS','note':'ADS; separate from TWSE ordinary shares 2330'}},
 'NVDA': {'entity_id':'NVDA','name':'NVIDIA Corporation','name_zh':'英伟达', 'aliases':['NVDA','NVIDIA','英伟达','輝達','辉达'],
          'security':{'symbol':'NVDA','exchange':'NASDAQ','instrument_type':'common_stock'}}}

def resolve_company(value):
 value=value.strip()
 for symbol,c in COMPANIES.items():
  if value.casefold() in [a.casefold() for a in c['aliases']]:
   return {'company':{k:v for k,v in c.items() if k!='security'},'research_symbol':symbol,
           'selected_security':dict(c['security']) if value.upper()==symbol else None,
           'security_resolution':'explicit_US_symbol' if value.upper()==symbol else 'company_only'}
 raise ValueError('Unsupported company/security. Use MU/美光, TSM/台积电, NVDA/英伟达; 2330 is a separate security.')

# Fixed query-side translations do not inspect future corpus vocabulary or create graph facts.
VOCABULARY = {
 '美光':'micron','台积电':'tsmc','台積電':'tsmc','英伟达':'nvidia','辉达':'nvidia',
 '存储':'memory dram nand','数据中心':'data center','主要业务':'business revenue segment','业务':'business segment',
 '直接上游':'suppliers manufacturing','供应商':'suppliers foundries subcontractors','客户':'customers',
 '匿名':'unnamed customer','供货':'supply supplier','合作':'collaboration alliance','份额':'share percentage',
 '读取速度':'sequential read bandwidth','顺序读取':'sequential read','带宽':'bandwidth','容量':'capacity',
 '先进封装':'advanced packaging cowos','封装':'packaging','产能':'capacity','供应受限':'supply constrained',
 '短缺':'shortage supply','受限':'constrained limited','替代':'alternative','技术':'technology',
 '定价':'pricing','需求':'demand','成本':'cost','政策':'policy export restrictions','限制':'restrictions',
 '制裁':'sanctions restrictions','出口':'export','收入':'revenue','占比':'percentage revenue',
 '工艺':'process technology','中介层':'interposer','线宽':'line width','间距':'pitch spacing',
 '量产':'volume production','送样':'samples sampling','扩产':'capacity expansion','供应':'supply',
 '合同':'contract','毛利率':'gross margin','库存':'inventory','晶圆':'wafer','代工':'foundry',
 '采购':'procurement','制造':'manufacturing','报告':'report','涨价':'pricing increase',
 '全部':'all','单个':'single','每个':'per','功耗':'power','良率':'yield','增长':'growth',
 '证据':'evidence','合作伙伴':'partners','营收':'revenue','运输':'shipping','供应链':'supply chain',
 '内存':'memory','具名':'named','伙伴':'partner supplier','采购':'purchase procurement',
 '经营分部':'operating segment compute networking graphics','分部':'segment','业务单元':'business unit',
 '匿名客户':'unnamed customer concentration','未具名':'unnamed','直接客户':'direct customer',
 '逻辑芯片':'logic die','逻辑':'logic','自制':'in-house manufactured','合作':'collaboration collaborate',
 '认证':'qualification qualified','更换':'replace substitution','结构':'structure interposer',
 '面积上限':'reticle size area','最小':'minimum','三星':'samsung','海力士':'SK Hynix',
 '鸿海':'Hon Hai','纬创':'Wistron','日月光':'ASE','矽品':'SPIL',
 '集成':'integration','电容':'capacitor','硅':'silicon','重布线':'redistribution RDL'}

def expand_query(text):
 extras=[]
 for word,english in VOCABULARY.items():
  if word in text:extras.append(english)
 # Company aliases are identities only in this fixed map, never inferred from vectors.
 for symbol,c in COMPANIES.items():
  if any(re.search(r'(?<![A-Za-z0-9])'+re.escape(a)+r'(?![A-Za-z0-9])',text,re.I) for a in c['aliases'] if a.isascii()):
   extras.append({'MU':'micron','TSM':'tsmc','NVDA':'nvidia'}[symbol])
 return text+' '+' '.join(dict.fromkeys(extras))
