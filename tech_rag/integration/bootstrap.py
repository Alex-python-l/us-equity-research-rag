"""Load the pinned local official framework without dotenv discovery."""
from pathlib import Path
import sys
import subprocess
from unittest.mock import patch

VENDOR = Path(__file__).resolve().parents[1]/'vendor'/'TradingAgents'
UPSTREAM_COMMIT = 'e8f5503e9b86d5c2710b0c76b1f686703968c9df'

def bootstrap():
    from integration.source_config import configure_sources
    configure_sources()
    if not (VENDOR/'tradingagents'/'graph'/'setup.py').is_file():
        raise RuntimeError('Pinned official TradingAgents checkout is missing')
    actual=subprocess.run(['git','-C',str(VENDOR),'rev-parse','HEAD'],check=True,
                          capture_output=True,text=True).stdout.strip()
    if actual!=UPSTREAM_COMMIT:
        raise RuntimeError('TradingAgents commit differs from the reviewed pin; refusing an unversioned framework')
    sys.path.insert(0, str(VENDOR))
    # Upstream import normally searches .env and .env.enterprise. Credentials
    # for this integration may ONLY come from the user's explicit config path.
    with patch('dotenv.find_dotenv', return_value=''), patch('dotenv.load_dotenv', return_value=False):
        import tradingagents
        if VENDOR not in Path(tradingagents.__file__).resolve().parents:
            raise RuntimeError('A different TradingAgents package is already imported; start a fresh integration interpreter')
        from tradingagents.graph.trading_graph import TradingAgentsGraph
    return TradingAgentsGraph
