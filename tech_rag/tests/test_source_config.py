import json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

class SourceConfigTests(unittest.TestCase):
    def test_only_explicit_allowlist_and_no_secrets_in_status(self):
        from integration.source_config import load_sources
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'.env';p.write_text('FRED_API_KEY=synthetic-secret\nTECH_NEO4J_PASSWORD=do-not-load\nPATH=bad\nTYPESAFE_API_KEY=\n',encoding='utf-8')
            c=load_sources(p)
            self.assertEqual(c.values['FRED_API_KEY'],'synthetic-secret')
            self.assertNotIn('PATH',c.values);self.assertNotIn('TECH_NEO4J_PASSWORD',c.values)
            self.assertNotIn('synthetic-secret',repr(c)+json.dumps(c.status()))

    def test_environment_priority_and_missing_file(self):
        from integration.source_config import load_sources,configure_sources
        with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,{'FRED_API_KEY':'process-secret'},clear=True):
            p=Path(td)/'.env';p.write_text('FRED_API_KEY=file-secret\nSEC_EDGAR_USER_AGENT=Research contact@example.test\n',encoding='utf-8')
            configure_sources(p)
            self.assertEqual(os.environ['FRED_API_KEY'],'process-secret')
            self.assertEqual(os.environ['SEC_EDGAR_USER_AGENT'],'Research contact@example.test')
            self.assertFalse(load_sources(Path(td)/'absent').status()['fred']['configured'])

    def test_probe_never_returns_provider_error_body_or_credential_url(self):
        from integration.source_config import load_sources,probe_sources
        class Response:
            status_code=200
            def json(self):return {'Information':'limited secret-value https://example.test?apikey=secret-value'}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'.env';p.write_text('ALPHA_VANTAGE_API_KEY=secret-value\n',encoding='utf-8')
            result=probe_sources(load_sources(p),get=lambda *a,**k:Response())
            self.assertEqual(result['alpha_vantage']['status'],'limited_or_entitlement')
            self.assertNotIn('secret-value',json.dumps(result))

if __name__=='__main__':unittest.main()
