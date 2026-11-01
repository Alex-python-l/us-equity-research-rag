"""Explicit primary-event identity and append-only revisions; no NLP identity guesses."""
from .governance import (_copy, _hash, _intact, _issue, _record_issues, _seal,
                         _strings, _time, review_ledger)


def register_event(shared, event):
    """Deduplicate by (original_source_id, original_event_id).

    reporter, reprint_source_id, agent agreement and wrapper metadata never form
    event identity or increase its count. A body/primary-version change appends a
    revision. Unknown primary identity is retained as a review issue only.
    """
    issues = []
    try:
        e = _copy(event)
        if not isinstance(e, dict):
            raise ValueError('event must be a mapping')
        for field in ['original_source_id', 'original_event_id', 'text', 'available_at']:
            if not isinstance(e.get(field), str) or not e[field].strip():
                raise ValueError('explicit original identity/body/time required')
        if not _strings(e.get('evidence_ids')) or not e['evidence_ids']:
            raise ValueError('verified evidence required')
        if not _strings(e.get('information_channels', [])):
            raise ValueError('channels must be string list')
        if _time(e['available_at']) > _time(shared['analysis_time']):
            raise ValueError('future event')
        for eid in e['evidence_ids']:
            evidence = shared['evidence_registry'].get(eid)
            if not evidence or not _intact(evidence):
                raise ValueError('missing or modified event evidence')
            if _time(e['available_at']) < _time(evidence['available_at']):
                raise ValueError('event predates evidence')
        if not any(shared['evidence_registry'][eid].get('source_id') == e['original_source_id'] for eid in e['evidence_ids']):
            raise ValueError('original source must be anchored by primary evidence')
    except (ValueError, TypeError, KeyError, OverflowError):
        issues.append(_issue('unverified_event_identity_or_evidence',
                             'Do not count summaries, agents, unverified metadata or future events as independent evidence.'))
        _record_issues(shared, issues)
        return {'event': None, 'issues': issues, 'duplicate': False}
    event_id = 'EVT_' + _hash([e['original_source_id'], e['original_event_id']])[:24]
    versions = shared['event_registry'].get(event_id, [])
    # Only actual primary information participates in version identity.
    fields = ['original_source_id', 'original_event_id', 'text', 'available_at',
              'source_sha256', 'primary_version', 'revision', 'subject', 'valid_time']
    content = {key: e.get(key) for key in fields}
    content['primary_evidence_versions'] = sorted({
        (shared['evidence_registry'][eid]['source_id'], shared['evidence_registry'][eid]['source_sha256'])
        for eid in e['evidence_ids']
        if shared['evidence_registry'][eid]['source_id'] == e['original_source_id']})
    content_hash = _hash(content)
    if any(not _intact(v) for v in versions):
        issues.append(_issue('ledger_integrity_violation', registry='event_registry', record_id=event_id))
        _record_issues(shared, issues)
        return {'event': None, 'issues': issues, 'duplicate': False}
    if versions and versions[-1].get('content_hash') == content_hash:
        return {'event': _copy(versions[-1]), 'issues': [], 'duplicate': True}
    record = dict(content, event_id=event_id, evidence_ids=list(dict.fromkeys(e['evidence_ids'])),
                  information_channels=e.get('information_channels', []),
                  status='registered', version=len(versions)+1, content_hash=content_hash,
                  previous_version_hash=versions[-1]['record_hash'] if versions else None)
    record = _seal(record)
    shared['event_registry'].setdefault(event_id, []).append(record)
    review_ledger(shared)
    return {'event': _copy(record), 'issues': issues, 'duplicate': False}
