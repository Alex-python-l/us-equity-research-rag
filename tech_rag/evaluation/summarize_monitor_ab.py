"""Produce a transparent report for the frozen one-round discovery experiment.

Manual source-relevance checks are explicitly separate from parser eligibility
and from fact verification. No model, market, or consensus calls occur here.
"""
from pathlib import Path
import csv,json,sys

def main(folder):
    p=Path(folder);r=json.loads((p/'RESULTS.json').read_text(encoding='utf-8'))
    bodies={f.stem.removeprefix('body_'):json.loads(f.read_text(encoding='utf-8')) for f in p.glob('body_*.json')}
    annotations={
        'https://finance.yahoo.com/news/hpe-updates-ai-factory-nvidia-100217079.html':
            dict(relevant_to=['NVDA'],theme='下游企业AI部署',basis='The HPE AI Factory with Nvidia integrates',note='报道明确涉及NVDA平台；新品与采用情况仍需HPE原始公告独立核实。'),
        'https://finance.yahoo.com/markets/stocks/articles/does-super-micros-big-quarter-073201865.html':
            dict(relevant_to=['NVDA'],theme='下游服务器需求',basis='Super Micro builds the servers and full racks',note='提到服务器、NVDA平台及预期差线索；属于二手报道，不能直接确认NVDA订单或收入。'),
        'https://finance.yahoo.com/technology/articles/asml-incredible-news-nvidia-stock-185202643.html':
            dict(relevant_to=['NVDA','MU'],theme='上游制造设备/扩产',basis='on Oct. 14',note='文章公开于10月21日，但正文引用10月14日业绩事件；新文章不等于新事件。'),
        'https://finance.yahoo.com/technology/ai/articles/3-ai-hardware-stocks-earnings-091141201.html':
            dict(relevant_to=['MU'],theme='存储及AI硬件',basis='supplying DRAM, NAND and high bandwidth memory',note='业务相关，但夹带筛选器/叙事营销且数字未经原始财报核实，不能当已验证新增事实。'),
        'https://finance.yahoo.com/markets/stocks/articles/greg-abel-says-every-point-093900458.html':
            dict(relevant_to=[],theme='无关/广告污染',basis='Berkshire Hathaway',note='主体是BNSF铁路和伯克希尔；NVDA只在推荐/广告文案，未形成可核实业务传导。'),
        'https://finance.yahoo.com/markets/stocks/articles/spacex-set-dubious-history-yet-092601796.html':
            dict(relevant_to=[],theme='无关/广告污染',basis='Missed Nvidia in 2009?',note='主体是SpaceX解禁；NVDA在推广文案，不构成目标公司的有效产业链新闻。'),
    }
    for url,a in annotations.items():
        body=next((b for b in bodies.values() if b['url']==url),None)
        if not body or a['basis'] not in body.get('body_text',''):raise ValueError('Annotation has no exact source support: '+url)
    dump=lambda name,x:(p/name).write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8')
    dump('MANUAL_RELEVANCE_AUDIT.json',dict(method='人工阅读本轮取得的原文；业务相关不等于事实核实',annotations=annotations))
    rows=[];total_rag_queries=[]
    for ticker,c in r['companies'].items():
        for arm,a in c['arms'].items():
            checked=[i for i in a['candidates'] if i.get('body_eligible')]
            related=[i for i in checked if ticker in annotations.get(i['url'],{}).get('relevant_to',[])]
            row=dict(ticker=ticker,arm=arm,queries=3,unique_candidates=a['metrics']['unique_candidates'],
                eligible_originals=len(checked),relevant_originals=len(related),
                relevance_denominator='仅已取得并通过定位/时间校验正文的候选',
                search_seconds=a['metrics']['search_seconds_sum'],new_facts_verified=None)
            rows.append(row)
        for search in c['arms']['B']['searches'][1:]:
            total_rag_queries.append(dict(ticker=ticker,query=search['query'],query_chars=len(search['query']),status=search['provider_status'],items=len(search['items'])))
    sdk_failures=sum(s['provider_status']=='provider_unavailable' for c in r['companies'].values() for a in c['arms'].values() for s in a['searches'])
    summary=dict(rows=rows,rag_extra_queries=total_rag_queries,rag_exclusive_candidates={t:c['overlap']['b_only'] for t,c in r['companies'].items()},
        sdk_failures=sdk_failures,network_discovery_attempts=r['real_news_http_attempts'],
        body_http_attempts=sum(b.get('calls',0) for b in bodies.values()),body_attempts=len(bodies),
        model_calls=0,provider_tokens=0,paid_native_search_used=False,
        conclusion='本轮当前实现的RAG上下游查询无检索增益；不能据此否定产业链知识本身。',
        causal_limit='四条多概念长query均零候选；长度/词项混杂/搜索语法适配是疑点，尚未以缩短query重新试验来证明单一原因。',
        next_change='将主题编译成多个短、单一事件/对象查询，并去除重复公司代码和并列供应商；另轮同预算对照，不改写本轮结果。',
        scope='本轮评估真实新闻发现与正文获取；未执行行情、历史一致预期、交易或持续调度，未验证收益效果。')
    dump('SUMMARY.json',summary)
    with (p/'comparison.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    lines=['金融RAG一轮A/B实测｜NVDA与MU｜2026-10-22','',
        '结论：本轮没有测出RAG增益。新增上下游查询4条均返回0候选，B组没有A组之外的新候选。',
        '“美高”按现有知识库确定为美光Micron(MU)。本实验不确认MU属于中型市值公司。','',
        '设置：冻结同一7天公开时间范围；每家公司每组3次逻辑查询，每次最多4条；每组最多核对4篇正文。',
        'A=股票代码+公司名+公司名stock；B=相同股票代码查询+原样RAG上游/下游search_query。',
        '共享相同股票代码查询与相同URL正文，避免重复获取；不重试，不启用定时计划，不调用付费原生搜索。',
        f'窗口UTC：{r["protocol"]["lookback_start"]} 至 {r["protocol"]["as_of"]}。','',
        '公司 | 组别 | 去重候选 | 正文取得并通过定位/时间校验 | 人工业务相关正文 | 查询耗时合计(秒)',
        '---|---|---:|---:|---:|---:']
    for row in rows:
        lines.append(f'{row["ticker"]} | {row["arm"]} | {row["unique_candidates"]} | {row["eligible_originals"]} | {row["relevant_originals"]} | {row["search_seconds"]}')
    lines += ['', '正文通过定位/时间校验，不代表新闻事实已核实。相关性检查仅覆盖取得的正文，不能当作全量精确率。',
        '两家公司B组各4条，全部属于A组候选子集，B独有候选均0。四条新增查询无provider错误，是成功返回空列表；正文阶段另有5个URL获取失败。','',
        'RAG原样新增查询：']
    for q in total_rag_queries:lines.append(f'- {q["ticker"]} ({q["query_chars"]}字符)：{q["query"]} → {q["items"]}条')
    lines += ['', '质检观察：',
        '- NVDA有效线索：HPE企业AI部署、Super Micro服务器需求、ASML制造设备；这些不能直接转为NVDA订单或收益结论。',
        '- MU有效线索：存储/AI硬件文章、ASML制造设备；数字需原始财报核实。',
        '- NVDA搜索混入BNSF铁路和SpaceX解禁；正文NVDA只是推广文案，应排除。',
        '- ASML文章较新但描述10月14日事件；需要继续追溯，不能因为系统本轮发现就认定信息新增。','',
        '问题定位：当前指南生成的方向与当前搜索provider之间缺少短query编译，直接拼接多个对象和信号词。',
        '本轮只能确认该组query的实测失败；没有追加优化后实验，不能声称改短已有效。',
        '建议下一步：每主题拆成短query，相关性初筛排除广告提及；再另做同预算对照。','',
        f'调用：12次逻辑查询，共享后{r["actual_sdk_queries"]}次SDK调用，{r["real_news_http_attempts"]}次发现HTTP尝试；11个唯一URL正文尝试，6个取得合格正文；模型调用0、付费tokens0。',
        '未测项：首次公开时间独立核实、真实新增事实、历史预期差、行情反应、股价预测/交易收益，均未宣称成功。',
        '统计边界：每公司仅一轮、新闻源单一、正文抽样且有网络失败、查询时间有秒级差异，不代表稳定或统计显著的结论。']
    (p/'REPORT.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main(sys.argv[1])
