"""Validated, timezone-aware schedules backed only by real exchange calendars."""
from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
import math
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
KINDS = ('search', 'fundamental', 'guide_maintenance')


class CalendarUnavailable(ValueError):
    pass


def utc_datetime(value=None):
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError as exc:
            raise ValueError('timestamp must be ISO 8601 with timezone') from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('timestamp requires timezone')
    return value.astimezone(UTC)


def default_config(ticker, now=None):
    symbol = str(ticker).strip().upper()
    from aliases import COMPANIES
    exchange = COMPANIES.get(symbol, {}).get('security', {}).get('exchange', 'NASDAQ')
    config = {
        'ticker': symbol, 'market': 'XNYS' if exchange == 'NYSE' else 'XNAS', 'display_timezone': 'Asia/Shanghai',
        'lifecycle': {'start_at': utc_datetime(now).isoformat(), 'end_at': None},
        'analysis': {'event': {'enabled': True, 'trading_days': [0, 1, 5]},
                     'fundamental': {'enabled': True, 'cadences': ['monthly', 'quarterly']}},
        'cadence': {'trading_minutes': 30, 'off_hours_minutes': 120, 'close_delay_minutes': 60,
                    'guide_weekday': 0, 'guide_time': '09:00', 'guide_timezone': 'Asia/Shanghai',
                    'llm_min_minutes': 30},
        'retrieval': {'lookback_hours': 72, 'overlap_minutes': 120},
        'sources': ['yahoo'], 'budgets': {'requests_per_day': 80, 'llm_calls_per_day': 6},
        'followup': {'trading_days': [1, 5], 'after_disclosure': True},
        'notification': {'mode': 'in_app'}, 'acceleration': {'enabled': False},
    }
    return validate_config(config)


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer in [{minimum}, {maximum}]')


def _boolean(value, name):
    if type(value) is not bool:
        raise ValueError(f'{name} must be boolean')


def _days(value, name):
    if not isinstance(value, list) or not value or len(value) != len(set(value)):
        raise ValueError(f'{name} must be a nonempty unique list')
    for day in value:
        _integer(day, name, 0, 252)


def validate_config(config):
    """Return a detached, normalized config; never couple cadence and analysis windows."""
    if not isinstance(config, dict):
        raise ValueError('config must be an object')
    c = deepcopy(config)
    try:
        if not isinstance(c['ticker'], str) or not re.fullmatch(r'[A-Z0-9][A-Z0-9.^=-]{0,19}', c['ticker'].strip().upper()):
            raise ValueError('ticker is invalid')
        c['ticker'] = c['ticker'].strip().upper()
        if c['market'] not in ('XNAS', 'XNYS'):
            raise ValueError('market must be XNAS or XNYS')
        ZoneInfo(c['display_timezone'])
        start = utc_datetime(c['lifecycle']['start_at'])
        c['lifecycle']['start_at'] = start.isoformat()
        end = c['lifecycle']['end_at']
        if end is not None:
            end = utc_datetime(end)
            if end <= start:
                raise ValueError('lifecycle end must follow start')
            c['lifecycle']['end_at'] = end.isoformat()
        a, f = c['analysis']['event'], c['analysis']['fundamental']
        _boolean(a['enabled'], 'event.enabled')
        _boolean(f['enabled'], 'fundamental.enabled')
        _days(a['trading_days'], 'event.trading_days')
        if not isinstance(f['cadences'], list) or not f['cadences'] or len(set(f['cadences'])) != len(f['cadences']) or any(x not in ('monthly', 'quarterly') for x in f['cadences']):
            raise ValueError('fundamental.cadences must select monthly/quarterly')
        cadence = c['cadence']
        for key in ('trading_minutes', 'off_hours_minutes', 'llm_min_minutes'):
            _integer(cadence[key], key, 1, 10080)
        _integer(cadence['close_delay_minutes'], 'close_delay_minutes', 0, 1440)
        _integer(cadence['guide_weekday'], 'guide_weekday', 0, 6)
        if not isinstance(cadence['guide_time'], str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', cadence['guide_time']):
            raise ValueError('guide_time requires HH:MM')
        ZoneInfo(cadence['guide_timezone'])
        _integer(c['retrieval']['lookback_hours'], 'lookback_hours', 1, 8760)
        _integer(c['retrieval']['overlap_minutes'], 'overlap_minutes', 0, 525600)
        for key in ('requests_per_day', 'llm_calls_per_day'):
            _integer(c['budgets'][key], key, 0, 100000)
        if not isinstance(c['sources'], list) or not c['sources'] or any(x != 'yahoo' for x in c['sources']) or len(c['sources']) != len(set(c['sources'])):
            raise ValueError('sources must select supported source yahoo')
        _days(c['followup']['trading_days'], 'followup.trading_days')
        _boolean(c['followup']['after_disclosure'], 'after_disclosure')
        if c['notification']['mode'] != 'in_app':
            raise ValueError('notification mode must be in_app')
        _boolean(c['acceleration']['enabled'], 'acceleration.enabled')
        if c['acceleration']['enabled']:
            raise ValueError('event acceleration is not connected')
        if 'topic_ids' in c:
            ids = c['topic_ids']
            if (not isinstance(ids, list) or not 1 <= len(ids) <= 30
                    or any(not isinstance(topic_id, str) or not 1 <= len(topic_id) <= 100
                           or topic_id != topic_id.strip() for topic_id in ids)
                    or len(set(ids)) != len(ids)):
                raise ValueError('topic_ids must contain 1-30 unique nonempty string IDs of at most 100 characters')
        if 'topic_priorities' in c:
            priorities = c['topic_priorities']
            if (not isinstance(priorities, dict)
                    or any(not isinstance(topic_id, str) or not 1 <= len(topic_id) <= 100
                           or topic_id != topic_id.strip() or priority not in ('高', '中', '低')
                           for topic_id, priority in priorities.items())):
                raise ValueError('topic_priorities must map valid topic IDs to 高, 中, or 低')
    except (KeyError, TypeError, ZoneInfoNotFoundError) as exc:
        raise ValueError(f'invalid or missing configuration: {exc}') from exc
    return c


def _load_exchange_calendars():
    import exchange_calendars
    return exchange_calendars


def _calendar(config, around):
    try:
        lib = _load_exchange_calendars()
        requested = config['market']
        # exchange-calendars documents NASDAQ as a US-equity alias where a
        # separate XNAS implementation is unavailable. Expose that substitution.
        names = lib.get_calendar_names()
        resolved = requested if requested in names else 'XNYS'
        calendar = lib.get_calendar(resolved, start=f'{around.year - 2}-01-01', end=f'{around.year + 3}-12-31')
        actual = getattr(calendar, 'name', resolved)
        metadata = {'ready': True, 'requested': requested, 'resolved': actual,
                    'library': 'exchange_calendars', 'version': getattr(lib, '__version__', 'unknown'),
                    'alias': requested != actual,
                    'note': ('XNAS uses the XNYS US equity session calendar' if requested != actual else '')}
        return calendar, metadata
    except Exception as exc:
        raise CalendarUnavailable(f'real exchange calendar unavailable: {exc}') from exc


def calendar_status(market='XNAS', now=None):
    try:
        return _calendar({'market': market}, utc_datetime(now))[1]
    except CalendarUnavailable as exc:
        return {'ready': False, 'requested': market, 'resolved': None, 'reason': str(exc)}


def _sessions(calendar, around):
    frame = calendar.schedule.loc[(around - timedelta(days=14)).date().isoformat():
                                   (around + timedelta(days=370)).date().isoformat()]
    opened = 'open' if 'open' in frame.columns else 'market_open'
    closed = 'close' if 'close' in frame.columns else 'market_close'
    return [(utc_datetime(row[opened].to_pydatetime()), utc_datetime(row[closed].to_pydatetime()))
            for _, row in frame.iterrows()]


def _next(config, kind, after, calendar):
    start = utc_datetime(config['lifecycle']['start_at'])
    # An execution at lifecycle start is allowed, but an execution at "after"
    # is already consumed. Use one microsecond to retain both boundaries.
    floor = max(after, start - timedelta(microseconds=1))
    cadence = config['cadence']
    sessions = _sessions(calendar, floor)
    if kind == 'fundamental':
        if not config['analysis']['fundamental']['enabled']:
            return None
        delta = timedelta(minutes=cadence['close_delay_minutes'])
        candidate = next((close + delta for _, close in sessions if close + delta > floor), None)
    elif kind == 'guide_maintenance':
        tz = ZoneInfo(cadence['guide_timezone'])
        local = floor.astimezone(tz)
        target_day = local.date() + timedelta(days=(cadence['guide_weekday'] - local.weekday()) % 7)
        target_time = time.fromisoformat(cadence['guide_time'])
        candidate = datetime.combine(target_day, target_time, tzinfo=tz).astimezone(UTC)
        if candidate <= floor:
            candidate = datetime.combine(target_day + timedelta(days=7), target_time, tzinfo=tz).astimezone(UTC)
    elif kind == 'search':
        trading = timedelta(minutes=cadence['trading_minutes'])
        off = timedelta(minutes=cadence['off_hours_minutes'])
        candidate = None
        previous_close = None
        for opened, closed in sessions:
            if closed <= floor:
                previous_close = closed
                continue
            if opened <= floor < closed:
                steps = math.floor((floor - opened) / trading) + 1
                candidate = min(opened + steps * trading, closed)
            else:
                anchor = previous_close or datetime.combine(floor.date(), time(), tzinfo=UTC)
                steps = math.floor((floor - anchor) / off) + 1
                candidate = min(anchor + steps * off, opened)
            break
    else:
        raise ValueError(f'unsupported schedule kind: {kind}')
    if candidate is None:
        raise CalendarUnavailable('no future session within the calendar horizon')
    end = config['lifecycle']['end_at']
    if end and candidate >= utc_datetime(end):
        return None
    return candidate


def next_run(config, kind, after=None):
    config = validate_config(config)
    after = utc_datetime(after)
    calendar, _ = _calendar(config, max(after, utc_datetime(config['lifecycle']['start_at'])))
    value = _next(config, kind, after, calendar)
    return value.isoformat() if value else None


def followup_schedule(config, anchor_iso):
    """Count sessions strictly after the disclosure's New York market date.

    Review times use the actual session close plus close_delay_minutes, including
    early closes. A day-zero review already past disclosure moves to the next
    session; duplicate dates collapse. No guessed weekday calendar is permitted.
    """
    config = validate_config(config)
    anchor = utc_datetime(anchor_iso)
    calendar, metadata = _calendar(config, anchor)
    anchor_date = anchor.astimezone(ZoneInfo('America/New_York')).date()
    sessions = _sessions(calendar, anchor)
    future = [(opened, closed) for opened, closed in sessions
              if opened.astimezone(ZoneInfo('America/New_York')).date() > anchor_date]
    same = [(opened, closed) for opened, closed in sessions
            if opened.astimezone(ZoneInfo('America/New_York')).date() == anchor_date]
    delay = timedelta(minutes=config['cadence']['close_delay_minutes'])
    start = utc_datetime(config['lifecycle']['start_at'])
    end = utc_datetime(config['lifecycle']['end_at']) if config['lifecycle']['end_at'] else None
    times = set()
    for day in config['followup']['trading_days']:
        if day == 0:
            eligible = [closed + delay for _, closed in same + future if closed + delay >= anchor]
            candidate = eligible[0] if eligible else None
        else:
            candidate = future[day - 1][1] + delay if len(future) >= day else None
        if candidate is None:
            raise CalendarUnavailable('followup exceeds available exchange session horizon')
        if candidate >= start and (end is None or candidate < end):
            times.add(candidate.isoformat())
    return {'times': sorted(times), 'calendar': metadata, 'anchor_market_date': anchor_date.isoformat(),
            'policy': 'Nth exchange session after anchor market date; actual close plus close_delay_minutes'}


def followup_times(config, anchor_iso):
    return followup_schedule(config, anchor_iso)['times']


def preview_schedule(config, now=None, count=3):
    config = validate_config(config)
    _integer(count, 'count', 1, 100)
    now = utc_datetime(now)
    try:
        calendar, metadata = _calendar(config, max(now, utc_datetime(config['lifecycle']['start_at'])))
        candidates = {kind: _next(config, kind, now, calendar) for kind in KINDS}
        rows = []
        while len(rows) < count:
            available = [(at, kind) for kind, at in candidates.items() if at]
            if not available:
                break
            at, kind = min(available)
            rows.append({'kind': kind, 'at_utc': at.isoformat(),
                         'at_display': at.astimezone(ZoneInfo(config['display_timezone'])).isoformat()})
            candidates[kind] = _next(config, kind, at, calendar)
        return {'next_runs': rows, 'calendar': metadata, 'errors': []}
    except CalendarUnavailable as exc:
        return {'next_runs': [], 'calendar': {'ready': False, 'requested': config['market'],
                'resolved': None, 'reason': str(exc)}, 'errors': [str(exc)]}
