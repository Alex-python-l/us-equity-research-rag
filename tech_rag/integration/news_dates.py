"""Offline publisher-header date verification; never invent exact public time."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import hashlib
from pathlib import Path
from integration.news_body import _soup,parse_stamp


def reverify_header_date(article,as_of,window_start):
    out=deepcopy(article)
    if not out.get('body_acquired') or not out.get('raw_path') or not out.get('raw_sha256'):return out
    raw=Path(out['raw_path']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=out['raw_sha256']:raise ValueError('Original HTML hash mismatch')
    soup=_soup(raw);header=soup.select_one('.post-info .date')
    time_tag=soup.select_one('.wp-block-post-date time[datetime]')
    if not header and not time_tag:return out
    label=(header or time_tag).get_text(' ',strip=True)
    try:day=datetime.strptime(label,'%B %d, %Y').replace(tzinfo=timezone.utc)
    except ValueError:return out
    earliest=day-timedelta(hours=14);latest=day+timedelta(days=1,hours=12)
    observed=[]
    if time_tag:
        stamp=parse_stamp(time_tag.get('datetime'))
        if stamp:observed.append(dict(origin='publisher_header_time_datetime',value=stamp.isoformat()))
    for key in ('published_at','modified_at'):
        if out.get(key):observed.append(dict(origin='previous_'+key,value=out[key]))
    if any(parse_stamp(s['value'])>parse_stamp(as_of) for s in observed):return out
    conflict=bool(time_tag and out.get('modified_at') and parse_stamp(time_tag.get('datetime'))
                  and parse_stamp(out['modified_at'])<parse_stamp(time_tag.get('datetime')))
    out.update(published_date=day.date().isoformat(),publication_precision='publisher_rendered_day',
        actual_public_at=None,published_at=latest.isoformat(),
        publication_interval={'earliest':earliest.isoformat(),'latest_exclusive':latest.isoformat()},
        availability_basis='conservative_end_of_publisher_day_with_unknown_timezone',
        exact_publication_time_unknown=True,observed_original_time_metadata=observed,
        source_clock_chronology_conflict=conflict,
        publication_date_locator={'selector':'.post-info .date' if header else '.wp-block-post-date time',
                                  'text':label,'raw_sha256':out['raw_sha256']},
        header_date_reparsed_offline=True,historical_eligible=False,as_of_eligible=False)
    out['eligible']=bool(earliest>=parse_stamp(window_start) and latest<=parse_stamp(as_of)
                         and out.get('body_complete') and out.get('title'))
    out['source_verified']=True
    out['status']='body_verified_day_precision' if out['eligible'] else 'candidate_body_date_interval_outside_window'
    out['unknowns']=list(dict.fromkeys([*out.get('unknowns',[]),
        'Exact publication instant/timezone is unverified; publisher date and conservative UTC interval only.',
        *(['Publisher header and modification clocks conflict; do not treat either as exact public/event time.'] if conflict else [])]))
    return out
