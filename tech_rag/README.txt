DeepSeek Pro原生联网双路新闻 v6｜2026-10-20

正式run_research在线auto已接Pro原生web_search（产业）+Yahoo证券查询（公司），前置RAG一次/统一原文时间核验；显式legacy保留旧对照。
先读RESULTS_V6_20261020.txt与RUN_NATIVE_NEWS_V6.txt；独立产物native_news_v6_20261020/DELIVERY_MANIFEST.json。
实际首个MU请求215秒ConnectionError、usage未知，停止后续provider；真实A/B质量0/6完成，不能拿六个prepared/替身结果宣称效果已测。
账户与/models只读200、Pro列出，不代表原生生成已成功。budget.json unknown不得改0/重置/自动重试。
公司Yahoo各3候选，A产业6查询0候选，6份共用公司正文已核查；多为二级评论，不是6个已证明产业事件。
275项回归通过。旧原油/v2/v3/v4/v5及5026块/152向量保留，无新embedding/服务重启/写图/远程/交易操作。

---------------- 以下为v5及更早历史 ----------------

RAG前置产业链新闻研究 v5｜2026-10-19

先读RESULTS_V5_20261019.txt与RUN_INDUSTRY_V5.txt；独立产物industry_v5_20261019/DELIVERY_MANIFEST.json。
正式run_research已改前置RAG→产业搜索/正文核验→研究/审核两角色，无新闻后通用RAG。
本轮24真实请求/113879 tokens已结束，不重复运行付费driver。三公司背景、双语任务、原始正文、真实六个共享新闻A/B、漏斗与原子审阅均保存。
结论局部有效：未证明扩大有效新闻覆盖，MU产品对照有用，TSM局部退化、NVDA无明确增益；源先非专家评分A/B为MU2/3、TSM2/1、NVDA2/2（每案0–4），小样本不证明策略/收益。
256项回归通过，这是工程检查数，非研究准确率。旧v4失败/报告、原油、5026块/152向量保留。
Finance本轮不可达，显式local完成，不沿用v4在线成功冒充新后端通过；无服务重启/切库/写图/LEAN/券商/远程操作。

---------------- 以下为v4/v3/v2历史 ----------------

科技RAG与TradingAgents效果审查 v4｜2026-10-19

最新实测报告RESULTS_V4_20261019.txt；运行说明RUN_EFFECT_V4.txt；产物effect_v4_20261018/DELIVERY_MANIFEST.json。
真实MU/TSM/NVDA A/B共6完整9角色轨迹：63实际请求/358548 provider tokens，失败支路计费保留；不得重复提交已完成批次。
结论局部有效，未证明稳定整体增益：旧24统一44->42/55、已曝光14为11->14/32、源先新12为12->14/22。
发现并修复分页游标/线程FTS/实收上下文/父证据溢出/谨慎表述误拒/发布方口径标签；最新162项回归通过。
新口径/谨慎边界修复仅离线验证，尚无修复后真实LLM质量对照。无no_signal/回查率代替质量分。
MU/TSM中间自由报告未逐原子穷尽，已逐判断和最终资料项评分；NVDA有全部原子审阅。小样本不证明收益。
Finance本轮只读5592节点/16105边/82断言；原始5026块/152向量、原油和v2/v3报告保留。没有LEAN/券商/下单/远程操作。
统一只读接口仍是research.build_research_context；无需重新建环境/下载BGE/全量embedding。

---------------- 以下为v3/v2/v1历史，当前状态优先以上述v4说明为准 ----------------

科技产业链RAG与TradingAgents证据约束集成 v3｜2026-10-18

最新运行入口：RUN_RESEARCH_V3.txt；实测结果与限制：RESULTS_V3_20261018.txt。
官方TradingAgents已固定commit并真实接入，提供integration/framework.py的run_research与integration/run.py统一CLI。
真实图模型替身：MU/TSM/NVDA在local和Finance/neo4j均已运行；实际DeepSeek：MU两次有界在线验证，原始失败保留。
最终126项测试通过，78份保存运行按最终guard复核；Finance新增75研究审计节点/115边，原始82知识断言和旧图属性保留。
新留出题召回12/32→11/32退化、全部单包仍有截断，完整失败见结果文件。不能把工程通过当产业链完备或策略收益。
本轮没有LEAN/券商/订单/远程服务器操作；原油、原始文档和152条文档向量保留。下方仅为旧v2/v1历史快照。

---------------- 以下为v2及v1历史说明，当前状态以上述v3结果为准 ----------------

科技产业链统一研究检索 v2｜2026-10-18

最新入口：RUN_RESEARCH_V2.txt。下面保留2026-10-17 v1历史快照；当前计数、结果与限制以output/release_verification_20261018.json及新说明为准。

新增全部原文关键词检索、时点内BM25、统一research API/CLI、精确证据与原文图连接、规则式有界Agent、TradingAgents只读适配器。
统一接口：research.build_research_context(ticker, question, as_of, budget)。CLI：python tech_rag/cli.py research MU 'HBM供应约束' --as-of current。
使用现有financial_rag/.venv、本机BGE-M3和152条文档向量；无LLM/交易依赖。Finance仍为既定neo4j库，凭据只读tech_rag/.env。

完整示例、JSON/CSV评测及失败案例、时间/预算/引用规范和Neo4j只读核验命令见RUN_RESEARCH_V2.txt。
独立问题集evaluation/questions_v2.json已先冻结；内部一致性不冒称产业事实准确率，未把“切分入库”当作整个分析系统完成。

---------------- 以下为v1历史说明，最新状态由以上v2说明覆盖 ----------------

以公司为入口的科技产业链 RAG v1｜2026-10-17

一、当前实际完成情况
已完成本地资料整理、关系建模、真实embedding、公司路径检索、证据回查、独立新闻工具和有界Agent运行。
已实际写入用户随后明确指定并配置的Neo4j Desktop Finance实例，database=neo4j。
Finance ID：164b5ace-03b4-45cd-bc0f-ac9c533d6ef4；版本Enterprise 2026.09.0；Bolt localhost:7687；HTTP localhost:7474。
读取服务端neo4j_home验证了实例身份。Desktop Query已实际显示13家公司、15条公司间关系；这只是公司关系投影。
全数据集为5,423节点、5,582条图关系；其中业务知识断言82条，其余包含来源、证据和文本结构连接，不能混称为5,582条产业因果关系。
重复导入后节点/关系数及内容指纹一致。向量索引ONLINE，自匹配得分1.0；实际数据库向量与本地矩阵逐项一致。
本地和Neo4j后端各119项检查通过；另13项数据库回读、向量和时点查询审计通过。
内部路由默认7688与已有原油实例冲突，已备份Finance配置，仅改路由监听/广播到127.0.0.1:7689。
此前对test-db的两行临时配置已按原始哈希完整恢复；它保持停止。本轮未清空旧数据，未修改原油资料、原油向量或原有化工代码。
Finance由用户在Desktop创建并指定；本导入器未另建DBMS，未执行CREATE/DROP DATABASE。首次启动时Neo4j自行初始化默认库。

已取得27条资料记录，26份可读取正文：
- MU：FY2026、FY2025两份10-K；FY2026 Q3 10-Q；Q4准备发言；HBM3E/H200和HBM4/Vera Rubin合作公告及产品资料。
- NVDA：FY2026、FY2025两份独立版10-K；FY2027 Q2最新10-Q（实际期末2026-07-26）；官方最新季度材料、产品资料。
- TSM：2025、2024两份20-F；2026 Q3官方业绩材料及电话会；3DFabric联盟和CoWoS技术资料。
- 政策：2024 HBM管制联邦公报原文、2026 H200许可政策联邦公报原文及BIS说明。
- 补充：15份SEC申报元数据，含8-K/6-K；SEC文档下载403与部分正文缺口完整保留。
NVDA合订年报作为参考版本保留，但不进入RAG；使用独立10-K避免把后来发布的代理材料/股东信回填。

结构与向量：
- 65个实体，其中63个参与审核关系，另2个为无审核边候选。
- 82条关系：77条原文披露、4条条件性模型推断、1条技术路线研究比较。
- 70处证据；原PDF物理页码或HTML段落、规范化字符偏移、原文片段、源文件SHA256可回查。
- 文档按页/章节上下文与句群切为5,026块。复杂跨页表格尚未全部结构化。
- 优先向量化82条关系卡片与70处审核证据，共152条、1024维；其他文本块未声称全部embedding。
- 复用financial_rag/.venv与已有本地BGE-M3，没有再次下载模型。模型指纹和断点缓存已保存。

二、运行环境与CLI
在工作区根目录使用PowerShell：
  $env:PYTHONIOENCODING='utf-8'

公司入口（本地已实测；分析时间必须给出）：
  financial_rag\.venv\Scripts\python.exe tech_rag\cli.py --out tech_rag\output\query_MU.json company MU --as-of current --question '存储与数据中心上下游' --page-size 20
  financial_rag\.venv\Scripts\python.exe tech_rag\cli.py company TSM --as-of current --question '先进封装与制造约束'
  financial_rag\.venv\Scripts\python.exe tech_rag\cli.py company NVDA --as-of current --question 'AI计算与数据中心供应链'
current表示当前采集知识集；显式ISO时间（必须带时区）使用严格历史过滤，例如：
  financial_rag\.venv\Scripts\python.exe tech_rag\cli.py company MU --as-of '2026-10-08T23:59:59+00:00'

关系返回主要业务、直接上下游、技术依赖、竞争替代、成本/产能/需求/政策约束及有序路径。
路径每一步保留原始主体—关系—客体；逆向遍历标明方向和显示用逆关系，不把方向丢失成相似节点列表。
结果含next_cursor。下一页使用完全相同参数并加 --cursor '返回的cursor'。
current分页会冻结首个请求的分析时间；覆盖范围、max_hops、路径枚举上限、分页与枚举截断均显式返回。
默认只深度研究三家公司，不能把关联节点当成已完成全行业研究。

证据、向量检索与条件情景：
  financial_rag\.venv\Scripts\python.exe tech_rag\cli.py search 'NVIDIA的存储供应商与CoWoS封装依赖' --as-of current
  financial_rag\.venv\Scripts\python.exe tech_rag\cli.py evidence EV_具体编号 --as-of current
  financial_rag\.venv\Scripts\python.exe tech_rag\cli.py scenario NVDA hbm_shortage --as-of current
  financial_rag\.venv\Scripts\python.exe tech_rag\cli.py scenario TSM packaging_capacity --as-of current

独立新闻与Agent：
  financial_rag\.venv\Scripts\python.exe tech_rag\cli.py news MU --as-of current --query '近期产品与扩产'
  financial_rag\.venv\Scripts\python.exe tech_rag\cli.py agent MU '近期HBM供应受限对上下游的影响' --as-of current --session research_mu --max-rounds 3
  financial_rag\.venv\Scripts\python.exe tech_rag\cli.py agent TSM '先进封装产能变化' --as-of current --session research_tsm --max-rounds 3
v1 Agent是已运行的规则路由与有界工具编排器，不是已配置LLM规划器。
每轮检查实体、证据、缺口；动态问题另调用独立新闻工具；发现新实体后有界扩展；跨轮证据会重核时点。
state/agent.sqlite3保存会话，历史查询不能直接复用后来知道的实体和证据。
新闻工具已实测SEC事件元数据在线刷新，但不是完整市场实时新闻系统；元数据候选未经正文核验不自动生成新供货边。

三、已配置的Finance连接与后续运行
当前tech_rag/.env已由用户配置并实测可连接Finance的neo4j库；凭据不在说明或交付压缩包中。
日常先在Desktop启动Finance，再执行下列命令；当前实例已运行。Finance与test-db均使用7687，不同时启动二者。
tech_rag/.env.example给出TECH_NEO4J_*字段；也可以显式指定已有安全配置文件中的NEO4J_*字段。
以后需要重新配置时，本机可运行以下辅助脚本，以掩码输入密码，并将配置文件权限限制为当前用户：
  powershell -NoProfile -ExecutionPolicy Bypass -File tech_rag\configure_neo4j.ps1
脚本只保存本地配置，不新建或修改Neo4j实例、用户或密码。辅助脚本仅做过语法检查；当前驱动的真实凭据连接已通过。
程序要求显式数据库名；密码按字面值解析，不展开其中的环境变量表达式。
本模块不会默认读取financial_rag/.env作为目标，不启动或创建任何DBMS/数据库。

运行示例（本轮均已有相应真实执行结果）：
  financial_rag\.venv\Scripts\python.exe tech_rag\cli.py --config tech_rag\.env --out tech_rag\output\neo4j_probe.json probe
  financial_rag\.venv\Scripts\python.exe tech_rag\cli.py --backend neo4j --config tech_rag\.env company MU --as-of current
  financial_rag\.venv\Scripts\python.exe tech_rag\cli.py --backend neo4j --config tech_rag\.env company TSM --as-of current
  financial_rag\.venv\Scripts\python.exe tech_rag\cli.py --backend neo4j --config tech_rag\.env company NVDA --as-of current
  financial_rag\.venv\Scripts\python.exe tech_rag\cli.py --backend neo4j --config tech_rag\.env agent MU '近期HBM供应受限' --as-of current --session research_mu --max-rounds 3
重新验证：
  financial_rag\.venv\Scripts\python.exe tech_rag\validate.py --backend neo4j --config tech_rag\.env
  financial_rag\.venv\Scripts\python.exe tech_rag\audit_neo4j.py --config tech_rag\.env
数据变更后才需要重复导入（本轮已验证幂等，日常查询不必导入）：
  financial_rag\.venv\Scripts\python.exe tech_rag\cli.py --config tech_rag\.env import --confirm-existing-target

写入采用Tech*标签、dataset_id=tech_company_v1与复合唯一键，所有匹配限定本数据集。
关系以TechAssertion/TechEvidence/TechSource保存完整证据，并创建带类型和证据类别的可视关系。
独立向量标签TechCompanyV1KeyRecord；索引tech_bgem3_5617a9f6_1024_v1。
导入器使用幂等MERGE，不含清空、删除或CREATE DATABASE；导入前后检查非Tech节点计数。
Neo4j语句已在Finance 2026.09.0上执行。现有版本支持db.index.vector.queryNodes，同时提示该接口deprecated；本轮自匹配实测成功。
Desktop操作：Finance → Connect → Query；顶栏确认Instance: Finance、Database: neo4j。
执行output/desktop_company_graph.cypher查看公司间关系；desktop_queries.cypher另含公司、供应、技术合作及证据子图查询。
Desktop已实际运行公司图查询，output/qa/Finance_graph.png与desktop_validation.json保留证明。

四、交付文件
工作区根目录：科技产业链RAG试点_MU_TSM_NVDA_2026-10-17.zip；包含原文、结构化数据、代码、向量/缓存及验证结果。
归档复用现有本机环境；不含.env、实例配置备份、venv或模型权重。CRC/SHA256见output/package_report.json。
output/manifest.json：规模及真实执行状态。
output/source_catalog.json、sources.csv：资料清单、时间依据、原文链接、报告期、哈希。
output/sec_sources.json、sec_filings.csv：SEC申报清单和原始接受时间字段；8-K/6-K缺口不隐瞒。
output/company_profiles.json、company_MU.json、company_TSM.json、company_NVDA.json、company_coverage.csv：公司关系档案。
output/company_dossier_MU.txt、company_dossier_TSM.txt、company_dossier_NVDA.txt：可阅读的业务地图及逐条出处。
output/company_profiles_neo4j.json、company_*_neo4j.json：实际数据库后端返回的三公司结果。
output/entities.json/csv、relations.json/csv、evidence.json/csv：可后续导入的结构化实体/关系/证据。
output/chunks.jsonl：5,026块全部文字；output/key_records.jsonl、embedded_records.jsonl、embeddings.npy：152个优先索引记录及实际向量。
output/model_provenance.json、state/embedding_cache.sqlite3：模型指纹与可续跑缓存。
output/validation_local.json、validation_neo4j.json：两个后端各119项检查通过。
output/scenario_validation.json、agent_validation_examples.json、semantic_query_demo.json：真实路径、跨轮和向量查询结果。
output/neo4j_import_report.json：executed=true的实际入库记录；neo4j_import_first.json保留首次记录。
output/neo4j_idempotency_report.json：重复导入前后5,423节点/5,582关系一致。
output/neo4j_readback_audit.json：13项数据库回读与实际语义查询审计；semantic_neo4j_demo.json、semantic_historical_neo4j_demo.json保留当前/历史向量结果。
output/desktop_target_status.json、desktop_port_fix.json、desktop_validation.json：最终Finance身份/连接、端口修复和Desktop图谱展示核验。
output/gaps.json/csv、public_time_audit.json：资料与公开时间审计缺口。
numeric/：独立数值来源清单、已视觉复核的静态财报数值样例；没有混入连续行情或事后收益。
raw/、text/：原文件与逐页/段正文。部分PDF复核图在output/qa/。

五、时间、关系与已测边界
SEC接受时间不等于已验证的全球首次公开时间；已发现MU索引页与API原值不一致，保留原值而不猜时区。
可用于历史查询的原文事实按已匹配的报告版本与披露日期保守放行，日期级策略为原日期之后36小时UTC。
未核验历史版本的网页、修订电话会、产品页在历史模式保守排除；不以报告期末替代公开日期。
研究比较和模型推断的可用时间不早于创建时点，历史情景不足时返回insufficient，不回填当前推断。
具名memory采购、产品design-in、制造工艺依赖和技术联盟关系分别建模；没有把供应商份额、独家订单和客户身份补造为事实。
匿名客户保留发行人和年度范围；NVDA FY2025 Customer A不映射为Microsoft/Amazon，也不当成跨年度同一客户。
HBM供应约束和封装产能变化的路径保留认证、库存缓冲、其他零件、良率、需求、价格合同等条件，不估计股票收益。
119项检查包括三公司入口、全部文本块与证据回查、方向与路径连续性、分页及截断、直接/间接区分、未来披露/实体元数据排除和跨轮证据防泄漏。
这些是工程和数据一致性验证，不是独立人工标注的产业链完备率、因果准确率或量化回测结论。
当前尚未完成：LLM规划与自然语言综合生成、完整新闻正文扩展、全部表格口径重建、完整政策修订、盘中公开时间及独立产业准确率/回测核验。

六、与原仓库的复用关系
已阅读GraphRetrievalEngine/src/agent/router.py、src/retrieval/graph_walker.py以及ChemQAPage/backend/app/services/retrieval_orchestrator.py。
原Router化学领域提示词、GraphWalker化学品/Standard种子及无类型邻居扩散不直接复用为科技事实。
复用其路由—检索—证据检查的编排思路；新tools.py按公司、关系类型、方向与时间约束返回路径。
向量直接复用financial_rag/model.py及本机BGE-M3 ONNX缓存；未把原油或化工索引混入科技检索。
前端与原化工编排器尚未直接接线，不把“已切分”或“已embedding”当作完整产业链系统上线。
