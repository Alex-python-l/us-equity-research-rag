"""Explicit local LLM config. Never discovers credentials or mutates environment."""
from dataclasses import dataclass,field
from pathlib import Path
from urllib.parse import urlsplit
import json,re

@dataclass(frozen=True)
class LLMConfig:
 provider:str
 model:str
 base_url:str
 api_key:str=field(repr=False)
 config_path:str|None=None
 def safe_metadata(self):
  return {'provider':self.provider,'model':self.model,'base_url':self.base_url,'config_path':self.config_path,
          'credential_configured':bool(self.api_key),'credential_values_recorded':False}

def parse_llm_config(text,path=None):
 values={}
 if text.strip().startswith('{'):
  try:values={str(k).upper():v for k,v in json.loads(text).items()}
  except Exception:raise ValueError('Invalid JSON LLM configuration') from None
 else:
  for line in text.splitlines():
   m=re.fullmatch(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*',line)
   if m:values[m[1].upper()]=m[2].strip('"\'')
 provider=values.get('LLM_PROVIDER') or values.get('PROVIDER')
 if not provider and ('DEEPSEEK_API_KEY' in values or 'deepseek' in text.lower()):provider='deepseek'
 if provider!='deepseek':raise ValueError('LLM provider must be explicitly identified; supported integration provider is deepseek')
 key=values.get('DEEPSEEK_API_KEY') or values.get('LLM_API_KEY') or values.get('API_KEY')
 if not key:
  matches=re.findall(r'\bsk-[A-Za-z0-9_-]{16,}',text)
  if len(matches)==1 and not values:key=matches[0]
 if not isinstance(key,str) or len(key)<16:raise ValueError('Explicit LLM credential missing or malformed')
 base=values.get('LLM_BASE_URL') or values.get('DEEPSEEK_BASE_URL') or 'https://api.deepseek.com'
 url=urlsplit(base)
 if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:
  raise ValueError('LLM base URL must be HTTPS without embedded credentials/query/fragment')
 model=values.get('LLM_MODEL') or values.get('MODEL') or 'deepseek-flash'
 return LLMConfig(provider,str(model),base.rstrip('/'),key,str(Path(path).resolve()) if path else None)

def load_llm_config(path):
 if not path:raise ValueError('An explicit authorized local LLM config path is required')
 p=Path(path).expanduser().resolve()
 if not p.is_file():raise ValueError('Specified LLM configuration file is missing')
 return parse_llm_config(p.read_text(encoding='utf-8-sig'),p)
