"""Deterministic context selection from already verified evidence only."""
import re

def terms(text):
    return {x.upper() for x in re.findall(r'[A-Za-z]+\d+[A-Za-z0-9]*|\d+(?:GB|TB)|CoWoS[-\s]?[SRL]',text,re.I)}

def select_evidence(registry,question,required=(),limit=6):
    selected={};wanted=terms(question);covered=set()
    for key in required:
        if key in registry:
            selected[key]=registry[key];covered|=terms(registry[key].get('text',''))
    candidates={k:v for k,v in registry.items() if k not in selected}
    while candidates and len(selected)<limit:
        def rank(key):
            value=candidates[key];hits=terms(value.get('text',''))&wanted
            return (-len(hits-covered),-len(hits),len(value.get('text','')),key)
        key=min(candidates,key=rank);value=candidates.pop(key)
        selected[key]=value;covered|=terms(value.get('text',''))
    return selected
