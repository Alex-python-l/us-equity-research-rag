"""Source-only CI selection. No keys, real corpus, model downloads or paid calls."""
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tech_rag/tests'))
sys.path.insert(0,str(ROOT/'tech_rag'))
PATTERNS=('test_rag_benchmark.py','test_rag_package.py','test_knowledge_versions.py',
          'test_event_analysis.py','test_monitor_schedule.py','test_source_config.py','test_monitor_reliability.py')

if __name__=='__main__':
    suite=unittest.TestSuite()
    for pattern in PATTERNS:suite.addTests(unittest.defaultTestLoader.discover(str(ROOT/'tech_rag/tests'),pattern=pattern))
    with patch.object(socket.socket,'connect',side_effect=AssertionError('Network forbidden in source-only CI')):
        result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
