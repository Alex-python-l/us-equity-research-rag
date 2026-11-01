"""Explicit project data-provider settings; never returns secret values."""
from dataclasses import dataclass,field
from pathlib import Path
from datetime import datetime,timezone
import json,os
from dotenv import dotenv_values

DEFAULT_PATH=Path(__file__).resolve().parents[1]/'.env'
ALLOWED=('FRED_API_KEY','SEC_EDGAR_USER_AGENT','ALPHA_VANTAGE_API_KEY',
         'TYPESAFE_API_KEY','TYPESAFE_BASE_URL','TYPESAFE_DEFAULT_MODEL')

@dataclass
class Sources:
    path:Path
    values:dict=field(repr=False)
    def status(self):
        return {name:dict(configured=bool(self.values.get(key)),
            status='configured_not_verified' if self.values.get(key) else 'not_configured')
            for name,key in [('fred','FRED_API_KEY'),('sec_edgar','SEC_EDGAR_USER_AGENT'),
                             ('alpha_vantage','ALPHA_VANTAGE_API_KEY'),('typesafe','TYPESAFE_API_KEY')]}

def load_sources(path=DEFAULT_PATH):
    p=Path(path).resolve()
    raw=dotenv_values(p,interpolate=False,encoding='utf-8-sig') if p.is_file() else {}
    return Sources(p,{k:str(raw[k]).strip() for k in ALLOWED if raw.get(k)})

def configure_sources(path=DEFAULT_PATH):
    config=load_sources(path)
    for key,value in config.values.items():
        if not os.environ.get(key):os.environ[key]=value
    return Sources(config.path,{k:os.environ[k] for k in ALLOWED if os.environ.get(k)})

def probe_sources(config=None,*,get=None):
    """One bounded request per supplied non-LLM provider; no retries/body logs."""
    import requests
    c=config or configure_sources();out=c.status();request=get or requests.get
    for name,key,url,params in [
        ('fred','FRED_API_KEY','https://api.stlouisfed.org/fred/series',{'series_id':'GDP','file_type':'json'}),
        ('sec_edgar','SEC_EDGAR_USER_AGENT','https://data.sec.gov/submissions/CIK0001045810.json',{}),
        ('alpha_vantage','ALPHA_VANTAGE_API_KEY','https://www.alphavantage.co/query',{'function':'OVERVIEW','symbol':'NVDA'})]:
        value=c.values.get(key)
        if not value:continue
        args=dict(params)
        if name=='fred':args['api_key']=value
        if name=='alpha_vantage':args['apikey']=value
        response=None
        try:
            user_agent=c.values.get('SEC_EDGAR_USER_AGENT','USResearch') if name=='sec_edgar' else 'US Equity Research Workspace'
            response=request(url,params=args,headers={'User-Agent':user_agent},timeout=(8,20))
            code=response.status_code;data=response.json()
            if code in (401,403):status='authentication_or_access_denied'
            elif code==429:status='rate_limited'
            elif code!=200:status='provider_error'
            elif isinstance(data,dict) and any(k in data for k in ('Information','Note')):status='limited_or_entitlement'
            elif isinstance(data,dict) and any(k in data for k in ('Error Message','error_message')):status='provider_rejected'
            else:
                valid=isinstance(data,dict) and bool(data.get({'fred':'seriess','sec_edgar':'cik','alpha_vantage':'Symbol'}[name]))
                status='verified' if valid else 'unexpected_response'
            out[name]=dict(configured=True,status=status,http_status=code,checked_at=datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            out[name]=dict(configured=True,status='network_or_format_failure',error_type=type(exc).__name__)
        finally:
            if response is not None and hasattr(response,'close'):response.close()
    # TypeSafe is not probed: configuring another provider never authorizes
    # paid social screening calls.
    return out

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--probe',action='store_true');p.add_argument('--output',type=Path)
    args=p.parse_args();c=configure_sources();result=probe_sources(c) if args.probe else c.status()
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
