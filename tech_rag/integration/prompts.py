"""The exact versioned prompts loaded by every framework role."""
from pathlib import Path
import hashlib

ROLES = ('news','bull','bear','research_manager','trader','aggressive',
         'conservative','neutral','portfolio_manager')
DIRECTORY = Path(__file__).with_name('prompts')

def load_prompt(role):
    if role not in ROLES:
        raise ValueError('unknown role')
    paths = [DIRECTORY/'common.txt', DIRECTORY/(role+'.txt')]
    text = '\n\n'.join(p.read_text(encoding='utf-8') for p in paths)
    return {'text':text,'sha256':hashlib.sha256(text.encode()).hexdigest(),
            'files':[str(p) for p in paths]}
