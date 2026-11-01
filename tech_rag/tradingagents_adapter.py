"""Read-only TradingAgents/LangGraph context; machine evidence remains lossless."""
import json

def adapt_evidence_packet(packet,max_context_chars=None):
 packet=json.loads(json.dumps(packet,ensure_ascii=False))
 cap=packet['budget']['max_total_chars'] if max_context_chars is None else max_context_chars
 if isinstance(cap,bool) or not isinstance(cap,int) or cap<200:raise ValueError('max_context_chars must be an integer >=200')
 heading=(f"Company: {packet['company']['name']} ({packet['research_symbol']})\n"
  f"Analysis: {packet['analysis_time']} [{packet['temporal_mode']}]\n"
  f"Evidence status: {packet['status']}\nQuestion: {packet['question']}\n"
  "Research evidence only. Preserve conditions, unknowns and disclosure boundaries.")
 # Reserve a coverage footer even for tiny contexts.
 lines=[heading[:max(80,cap-180)]];used=len(lines[0]);rendered=[];rendered_paths=[];assertion_refs=set()
 def add(text,reserve=140):
  nonlocal used
  if used+2+len(text)>cap-reserve:return False
  lines.append(text);used+=2+len(text);return True
 order=list(dict.fromkeys([h['citation_id'] for h in packet['results']]+list(packet['citations'])))
 for ref in order:
  c=packet['citations'][ref]
  label=f"[{ref}] {c['title']} | page={c['pdf_page']} paragraph={c['paragraph']} chars=[{c['char_start']},{c['char_end']})\n{c['source_url']}\n"
  space=cap-used-2-len(label)-210
  if space<80:continue
  excerpt=c['text'][:min(1200,space)]
  shortened=len(excerpt)<len(c['text'])
  view=label+excerpt+('\n[Text view shortened; full cited span retained in evidence_map.]' if shortened else '')
  if add(view):rendered.append(ref)
 for rid,a in packet['assertion_map'].items():
  if not set(a['citation_ids'])<=set(rendered):continue
  text=f"Assertion {rid}: {a['subject_id']} --{a['predicate']}--> {a['object_id']} [{a['evidence_class']}; {a['directness']}]\nScope: {a['business_scope']}\nCondition: {a['conditions']}\nEvidence: "+','.join(a['citation_ids'])
  if add(text):assertion_refs.add(rid)
 for path in packet['paths']:
  if not all(s['relation_id'] in assertion_refs and set(s['citation_ids'])<=set(rendered) for s in path['steps']):continue
  text='Ordered path '+path['path_id']+': '+' ; '.join(f"{s['from_id']} --{s['display_relation']}--> {s['to_id']} (condition/type in {s['relation_id']})" for s in path['steps'])
  if add(text):rendered_paths.append(path['path_id'])
 for gap in packet['insufficiency_reasons']+packet['unknowns']:add('Gap: '+gap)
 truncated=len(rendered)<len(packet['citations']) or len(rendered_paths)<len(packet['paths']) or any(len(packet['citations'][ref]['text'])>1200 for ref in rendered)
 footer=f"Coverage: exhaustive=false; packet_truncated={packet['coverage']['truncated']}; text_truncated={truncated}; stop={packet['stop_reason']}."
 remaining=cap-used-2
 if remaining>0:lines.append(footer[:remaining])
 text='\n\n'.join(lines)
 return {'research_context':text,'evidence_packet':packet,'evidence_map':packet['citations'],
  'rendered_citation_ids':rendered,'rendered_path_ids':rendered_paths,'text_truncated':truncated,
  'omitted_citation_ids':[ref for ref in packet['citations'] if ref not in rendered],
  'context_chars':len(text),'max_context_chars':cap,'permissions':['research_read_only'],'llm_planning_executed':False}

def research_tool(ticker,question,as_of,budget=None,*,backend='local',config=None):
 """Future tool body; no database mutation or trade API."""
 from research import build_research_context
 return adapt_evidence_packet(build_research_context(ticker,question,as_of,budget,backend=backend,config=config))
