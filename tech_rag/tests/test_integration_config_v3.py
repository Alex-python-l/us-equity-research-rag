import sys,unittest,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

class SecureConfigTests(unittest.TestCase):
 def test_tagged_secret_and_repr_redacted(self):
  from integration.secure_config import parse_llm_config
  secret='sk-fakeTESTabcdefgh0123456789'
  c=parse_llm_config('这是DeepSeek的密钥：'+secret)
  self.assertEqual(c.provider,'deepseek');self.assertEqual(c.api_key,secret)
  self.assertNotIn(secret,repr(c));self.assertNotIn(secret,str(c.safe_metadata()))
 def test_no_implicit_key_provider_or_insecure_url(self):
  from integration.secure_config import parse_llm_config
  with self.assertRaises(ValueError):parse_llm_config('sk-unlabelledabcdefgh0123456789')
  with self.assertRaises(ValueError):parse_llm_config('DEEPSEEK_API_KEY=sk-fake0123456789\nLLM_BASE_URL=http://bad.example')
 def test_standard_dotenv_is_explicit_and_no_env_fallback(self):
  from integration.secure_config import parse_llm_config
  c=parse_llm_config('DEEPSEEK_API_KEY="sk-fakeTESTabcdefgh0123456789"\nLLM_MODEL=deepseek-flash')
  self.assertEqual(c.model,'deepseek-flash')
  with self.assertRaises(ValueError):parse_llm_config('LLM_PROVIDER=deepseek')

if __name__=='__main__':unittest.main()
