"""Seeded synthetic information-overlap experiment, never a market backtest.

The outcome is a generated business-impact index in arbitrary units. News and
momentum partly observe the same latent event, so counting observations as
independent certainty is intentionally shown as a naive comparison only.
"""
from pathlib import Path
import json
import random
import statistics


def run_numeric_experiment(seed=1729, n_events=96, reprints_per_event=2):
    for value, lo, hi in [(seed, 0, 2**32-1), (n_events, 8, 10000), (reprints_per_event, 0, 20)]:
        if type(value) is not int or not lo <= value <= hi:
            raise ValueError('Invalid synthetic experiment seed or size budget.')
    rng = random.Random(seed)
    rows = []
    for index in range(n_events):
        # Event shock causes both public-news and momentum observations.
        shock, independent_factor = rng.gauss(0, 1), rng.gauss(0, 1)
        news = shock + rng.gauss(0, .3)
        momentum = .8 * shock + .2 * independent_factor + rng.gauss(0, .25)
        factor = independent_factor + rng.gauss(0, .15)
        business_outcome = .7 * shock + .3 * independent_factor + rng.gauss(0, .2)
        verified_scope = index % 4 != 0
        rows.append(dict(event_id=f'SYNTH_EVENT_{index:05}', news_signal=news,
            momentum_signal=momentum, independent_factor=factor,
            synthetic_business_outcome=business_outcome,
            verified_scope=verified_scope, source_count=1 + reprints_per_event,
            factor_news_dependence='possible_same_latent_event',
            evidence_status='conditional_research_supported' if verified_scope else 'needs_more_evidence'))
    count = len(rows)
    accepted = sum(r['verified_scope'] for r in rows)
    news_observations = count * (1 + reprints_per_event)
    def metrics(predictions, units, **extra):
        residuals = [prediction - row['synthetic_business_outcome'] for prediction, row in zip(predictions, rows)]
        return dict(synthetic_prediction_mse=statistics.mean(e * e for e in residuals),
                    synthetic_prediction_mae=statistics.mean(abs(e) for e in residuals),
                    nominal_information_units=units, **extra)
    factors = [.7*r['momentum_signal'] + .3*r['independent_factor'] for r in rows]
    news_only = [.7*r['news_signal'] for r in rows]
    naive = [a+b for a,b in zip(factors, news_only)]
    # This is a fixed illustrative rule, not fitted to generated outcomes.
    # For verified scope use one event signal plus an independent factor;
    # for missing scope abstain from the event overlay, retain factor baseline.
    gated = [(.7*r['news_signal'] if r['verified_scope'] else 0) + .3*r['independent_factor'] for r in rows]
    ablations = dict(
        factors_only=metrics(factors, count*2, description='Momentum plus independent factor.'),
        news_only=metrics(news_only, news_observations, description='News observations; reprints retained as observations only.'),
        both_naive=metrics(naive, count*2+news_observations,
            description='Deliberately naive sum of potentially overlapping factor and news signals.',
            independence_assumption_valid=False),
        rag_gate=metrics(gated, count+accepted, independent_event_count=count,
            accepted_conditional_count=accepted, needs_more_evidence_count=count-accepted,
            execution_allowed=False, description='Deduplicate by event identity; abstain on ungrounded event scope.',
            confidence_combination='none; same-event agreement is not independent evidence'))
    return dict(schema_version='synthetic_numeric_ablation_v3', seed=seed,
        synthetic_only=True, real_market_returns_used=False, profitability_claimed=False,
        outcome='generated business-impact index; arbitrary units, not price or return',
        independent_event_count=count, news_observation_count=news_observations,
        reprints_per_event=reprints_per_event, possible_dependence_count=count,
        ablations=ablations, events=rows,
        limitations=[
            'Pure synthetic demonstration; no real prices, returns, point-in-time fundamental data or trading simulation.',
            'Shared latent event dependence is known by construction; real-market dependence requires separate investigation.',
            'The RAG gate here is a synthetic scope-eligibility rule, not a trained model or real retrieval evaluation.',
            'No calibration, statistical significance, causal attribution, expected profit or investment validity is claimed.',
        ])


def save_numeric_artifact(path, **kwargs):
    result = run_numeric_experiment(**kwargs)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return result
