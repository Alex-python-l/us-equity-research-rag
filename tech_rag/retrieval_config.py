"""Versioned engineering hypotheses, not calibrated confidence weights."""
from dataclasses import dataclass, asdict, fields
import hashlib
import json
import math

@dataclass(frozen=True)
class RetrievalConfig:
    keyword_weight: float = 1.5
    detail_keyword_weight: float = 3.0
    dense_weight: float = 1.0
    graph_weight: float = 1.5
    detail_graph_weight: float = 0.5
    session_weight: float = 1.0
    rrf_k: float = 60.0
    novelty: float = 0.055
    location_penalty: float = 0.004
    facet_weight: float = 1.5
    facet_k: float = 20.0
    ticker_preference: float = 1.25
    diversify: bool = True
    channels: tuple = ('keyword', 'reviewed_dense', 'graph')

    @classmethod
    def from_value(cls, value=None):
        if isinstance(value, cls):
            value=asdict(value)
        data = dict(value or {})
        if set(data) - {f.name for f in fields(cls)}:
            raise ValueError('Unknown retrieval configuration')
        if 'channels' in data:
            data['channels'] = tuple(data['channels'])
        result = cls(**data)
        for field in fields(cls):
            v = getattr(result, field.name)
            if field.name in ('channels', 'diversify'):
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1000:
                raise ValueError('Invalid retrieval coefficient: ' + field.name)
        if not isinstance(result.diversify, bool) or not result.channels or len(set(result.channels)) != len(result.channels):
            raise ValueError('Invalid retrieval switches')
        if set(result.channels) - {'keyword', 'reviewed_dense', 'graph'}:
            raise ValueError('Unknown retrieval channel')
        return result

    def weights(self, intent):
        detail = intent == 'disclosure_detail'
        return dict(keyword=self.detail_keyword_weight if detail else self.keyword_weight,
                    reviewed_dense=self.dense_weight, graph=self.detail_graph_weight if detail else self.graph_weight,
                    session=self.session_weight)

    @property
    def version(self):
        return 'RC_' + hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:20]
