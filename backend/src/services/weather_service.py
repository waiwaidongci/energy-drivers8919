"""Forecast library: publications, per-building station history and day selections.

Design notes
------------
* A forecast issue is immutable once imported.  Revisions and late deliveries are
  new rows keyed by ``(station, target_day, published_at, revision)``; the
  publisher's original ``published_at`` is never rewritten.
* Withdrawals are recorded separately and mark the issue as withdrawn.  The
  original publication metadata stays intact.
* A building-day selection uses the station assignment valid on that day and the
  latest non-withdrawn issue published *strictly before the previous day's
  00:00*.  When nothing qualifies the selection is stored as ``missing``; the
  observed temperature is never substituted.
* Every materialised selection stores ``forecast_version`` and
  ``assignment_version``.  Research runs copy the exact rows into an immutable
  snapshot, so imports, withdrawals, late revisions or assignment edits made
  concurrently cannot change a study that already started.
"""
import csv
import hashlib
import io
import json
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from src.models import (
    BuildingDay, BuildingDayForecast, BuildingStationAssignment,
    ForecastRunSnapshot, ForecastSnapshotDay, WeatherForecastIssue,
    WeatherForecastWithdrawal, WeatherLibraryState, WeatherStation,
)
from src.schemas.weather import AssignmentInput, StationInput
from src.utils.timeparse import iso, parse_datetime, parse_day

SELECTION_RULE = '按当日归属气象站，取前一日00:00前已发布且未撤回的最新一期预报'
MAX_EVIDENCE_CANDIDATES = 12


def scoped_filters(pairs, first, second, max_clauses=120):
    """Index-friendly batches equivalent to ``(first, second) IN pairs``.

    Contiguous days are merged per station/building, so a library update (or a
    snapshot) touches only the indexed scopes named by the caller and never
    scans the whole forecast table.  Clauses are returned in bounded batches to
    avoid a single enormous OR expression for very large imports.
    """
    grouped = defaultdict(list)
    for name, day in pairs:
        grouped[name].append(day)
    clauses = []
    for name in sorted(grouped):
        days = sorted(grouped[name])
        start = previous = days[0]
        for day in days[1:]:
            if parse_day(day) - parse_day(previous) == timedelta(days=1):
                previous = day
                continue
            clauses.append(and_(first == name, second >= start, second <= previous))
            start = previous = day
        clauses.append(and_(first == name, second >= start, second <= previous))
    if not clauses:
        return []
    return [or_(*clauses[start:start + max_clauses])
            for start in range(0, len(clauses), max_clauses)]


# ---------------------------------------------------------------- library state
def ensure_state(db) -> WeatherLibraryState:
    """Lock the singleton version row so imports, withdrawals and run acceptance
    are serialised against each other.  Uses a savepoint so that losing a race to
    create the row never discards the caller's earlier work."""
    state = db.get(WeatherLibraryState, 1, with_for_update=True)
    if state is None:
        try:
            with db.begin_nested():
                db.add(WeatherLibraryState(id=1))
                db.flush()
        except IntegrityError:
            pass
        state = db.get(WeatherLibraryState, 1, with_for_update=True)
        if state is None:
            raise HTTPException(503, '气象资料库版本状态初始化失败，请重试')
    return state


def _bump(state, forecast=False, assignment=False):
    if forecast:
        state.forecast_version += 1
    if assignment:
        state.assignment_version += 1
    state.updated_at = datetime.utcnow()


def read_state(db):
    state = db.get(WeatherLibraryState, 1)
    if state is None:
        state = ensure_state(db)
        db.commit()
    return state


def summary(db):
    state = read_state(db)
    counts = dict(db.execute(
        select(BuildingDayForecast.status, func.count())
        .group_by(BuildingDayForecast.status)
    ).all())
    return {
        'forecast_version': state.forecast_version,
        'assignment_version': state.assignment_version,
        'forecast_issue_count': state.forecast_issue_count,
        'station_count': db.scalar(select(func.count()).select_from(WeatherStation)),
        'issue_count': db.scalar(select(func.count()).select_from(WeatherForecastIssue)),
        'withdrawn_count': db.scalar(select(func.count()).select_from(WeatherForecastIssue)
                                     .where(WeatherForecastIssue.status == 'withdrawn')),
        'assignment_count': db.scalar(select(func.count()).select_from(BuildingStationAssignment)),
        'selected_day_count': db.scalar(select(func.count()).select_from(BuildingDayForecast)),
        'issued_day_count': counts.get('issued', 0),
        'missing_day_count': counts.get('missing', 0),
        'rule': SELECTION_RULE,
    }


# ---------------------------------------------------------------------- stations
def list_stations(db):
    return db.scalars(select(WeatherStation).order_by(WeatherStation.code)).all()


def create_station(db, data: StationInput):
    if db.scalar(select(WeatherStation).where(WeatherStation.code == data.code)):
        raise HTTPException(409, '气象站代码已存在')
    item = WeatherStation(**data.model_dump())
    db.add(item)
    db.commit()
    return item


# ---------------------------------------------------------------------- imports
def _forecast_csv_rows(text: str, source_file: str):
    """Yield validated forecast rows lazily so large files never sit in memory twice."""
    reader = csv.DictReader(io.StringIO(text))
    headers = {(h or '').strip() for h in (reader.fieldnames or [])}
    required = {'station_code', 'target_day', 'published_at', 'temperature_c'}
    if not required.issubset(headers):
        raise HTTPException(422, f'预报CSV缺少必需列：{", ".join(sorted(required))}（可选 received_at、revision、status）')
    now = datetime.utcnow()
    errors, count = [], 0
    for index, raw in enumerate(reader, start=2):
        raw = {(k or '').strip(): (v or '').strip() for k, v in raw.items()}
        try:
            station = raw.get('station_code', '')
            if not station:
                raise ValueError('station_code 为空')
            target = parse_day(raw.get('target_day', ''))
            published = parse_datetime(raw.get('published_at', ''))
            received = parse_datetime(raw['received_at']) if raw.get('received_at') else now
            revision = int(raw.get('revision') or 0)
            if revision < 0:
                raise ValueError('revision 不能为负')
            status = (raw.get('status') or 'issued').lower()
            if status not in ('issued', 'withdrawn'):
                raise ValueError('status 只能是 issued 或 withdrawn')
            temperature = float(raw.get('temperature_c', ''))
            if not (-90 <= temperature <= 70):
                raise ValueError('temperature_c 超出 -90–70°C')
            if received < published:
                raise ValueError('received_at 不能早于 published_at')
            count += 1
            yield {
                'station_code': station, 'target_day': target.isoformat(),
                'published_at': published, 'received_at': received,
                'revision': revision, 'temperature_c': temperature,
                'status': status, 'source_file': source_file,
            }
        except (ValueError, TypeError) as exc:
            errors.append(f'第{index}行：{exc}')
            if len(errors) >= 8:
                break
    if errors:
        raise HTTPException(422, '预报CSV校验失败：' + '；'.join(errors))
    if not count:
        raise HTTPException(422, '预报CSV没有可导入的数据行')


def _issue_key(row):
    return (row['station_code'], row['target_day'], row['published_at'], row['revision'])


def import_forecasts(db, data, actor=''):
    """Import a forecast file in bounded batches.

    Only the station-days named in the file are read; the forecast table is
    never scanned in full, and the many-row case is inserted in bulk.
    """
    state = ensure_state(db)
    stations = {s.code for s in db.scalars(select(WeatherStation)).all()}
    batch, inserted, unchanged, accepted = {}, 0, 0, 0
    changed_pairs, conflicts, missing_targets = set(), [], []
    withdrawals = []

    def flush_batch():
        nonlocal inserted, unchanged, batch
        if not batch:
            return
        known = _existing_issues(db, {(r['station_code'], r['target_day']) for r in batch.values()})
        new_rows = []
        for key, row in batch.items():
            issue = known.get(key)
            if row['status'] == 'withdrawn':
                if issue is None:
                    missing_targets.append(
                        f'{row["station_code"]} {row["target_day"]} {iso(row["published_at"])} r{row["revision"]}')
                    continue
                if issue.status != 'withdrawn':
                    issue.status = 'withdrawn'
                    issue.withdrawn_at = row['received_at']
                    changed_pairs.add((issue.station_code, issue.target_day))
                    withdrawals.append({
                        'issue_id': issue.id, 'occurred_at': row['received_at'],
                        'received_at': datetime.utcnow(), 'reason': '随同批预报CSV撤回', 'actor': actor,
                    })
                continue
            if issue is not None:
                if abs(issue.temperature_c - row['temperature_c']) > 1e-9:
                    conflicts.append(f'{row["station_code"]} {row["target_day"]} {iso(row["published_at"])} r{row["revision"]}')
                else:
                    unchanged += 1
                continue
            new_rows.append(row)
            changed_pairs.add((row['station_code'], row['target_day']))
        if new_rows:
            db.execute(WeatherForecastIssue.__table__.insert(),
                       [{**row, 'withdrawn_at': None} for row in new_rows])
            db.flush()
            inserted += len(new_rows)
        batch = {}

    for row in _forecast_csv_rows(data.csv_text, data.source_file):
        accepted += 1
        if not row['station_code'].strip():
            db.rollback()
            raise HTTPException(422, '气象站代码不能为空白')
        if row['station_code'] not in stations:
            db.rollback()
            raise HTTPException(422, f'未登记的气象站：{row["station_code"]}')
        key = _issue_key(row)
        prior = batch.get(key)
        if prior is not None:
            if abs(prior['temperature_c'] - row['temperature_c']) > 1e-9 or prior['status'] != row['status']:
                conflicts.append(f'{row["station_code"]} {row["target_day"]} {iso(row["published_at"])} r{row["revision"]}')
            continue
        batch[key] = row
        if len(batch) >= 5000:
            flush_batch()
            if conflicts:
                break
    flush_batch()

    if conflicts or missing_targets:
        db.rollback()
        parts = []
        if conflicts:
            parts.append('同一发布时间与修订号的预报温度不一致，请用新revision：' + '；'.join(conflicts[:5]))
        if missing_targets:
            parts.append('撤回行找不到对应预报期次：' + '；'.join(missing_targets[:5]))
        raise HTTPException(409, '；'.join(parts))

    if withdrawals:
        db.execute(WeatherForecastWithdrawal.__table__.insert(), withdrawals)
    db.flush()
    if inserted or changed_pairs:
        _bump(state, forecast=True)
        state.forecast_issue_count += inserted
        db.flush()
        refreshed = refresh_station_days(db, changed_pairs, state)
    else:
        refreshed = 0
    db.commit()
    return {
        'accepted_rows': accepted, 'inserted': inserted, 'unchanged': unchanged,
        'withdrawals': len(withdrawals), 'refreshed_building_days': refreshed,
        'forecast_version': state.forecast_version, 'assignment_version': state.assignment_version,
    }


# -------------------------------------------------------------------- withdrawals
def withdraw_issue(db, issue_id, data, actor=''):
    state = ensure_state(db)
    issue = db.get(WeatherForecastIssue, issue_id, with_for_update=True)
    if not issue:
        db.rollback()
        raise HTTPException(404, '预报期次不存在')
    if issue.status == 'withdrawn':
        db.rollback()
        raise HTTPException(409, '该预报已经撤回')
    occurred = parse_datetime(data.occurred_at) if data.occurred_at else datetime.utcnow()
    if occurred < issue.published_at:
        db.rollback()
        raise HTTPException(422, '撤回时间不能早于原发布时间')
    issue.status = 'withdrawn'
    issue.withdrawn_at = occurred
    db.add(WeatherForecastWithdrawal(
        issue_id=issue.id, occurred_at=occurred, received_at=datetime.utcnow(),
        reason=data.reason, actor=actor,
    ))
    db.flush()
    _bump(state, forecast=True)
    db.flush()
    refreshed = refresh_station_days(db, {(issue.station_code, issue.target_day)}, state)
    db.commit()
    return {'issue_id': issue.id, 'status': issue.status, 'published_at': iso(issue.published_at),
            'refreshed_building_days': refreshed, 'forecast_version': state.forecast_version}


def list_issues(db, station='', day='', status='', offset=0, limit=50):
    query = select(WeatherForecastIssue)
    if station:
        query = query.where(WeatherForecastIssue.station_code == station)
    if day:
        query = query.where(WeatherForecastIssue.target_day == day)
    if status in ('issued', 'withdrawn'):
        query = query.where(WeatherForecastIssue.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(WeatherForecastIssue.station_code, WeatherForecastIssue.target_day,
                                      WeatherForecastIssue.published_at.desc(), WeatherForecastIssue.revision.desc())
                      .offset(offset).limit(limit)).all()
    return {'total': total, 'rows': [{
        'id': r.id, 'station_code': r.station_code, 'target_day': r.target_day,
        'published_at': iso(r.published_at), 'received_at': iso(r.received_at),
        'revision': r.revision, 'temperature_c': r.temperature_c, 'status': r.status,
        'withdrawn_at': iso(r.withdrawn_at), 'source_file': r.source_file,
    } for r in rows]}


def _existing_issues(db, pairs):
    """Load all issues for the given (station, target_day) pairs only."""
    found = {}
    pairs = sorted(set(pairs))
    if not pairs:
        return found
    for scope in scoped_filters(pairs, WeatherForecastIssue.station_code,
                                WeatherForecastIssue.target_day):
        for issue in db.scalars(select(WeatherForecastIssue).where(scope)).all():
            found[(issue.station_code, issue.target_day, issue.published_at, issue.revision)] = issue
    return found


# --------------------------------------------------------------------- selections
def _cutoff(target_day: str) -> datetime:
    return datetime.combine(parse_day(target_day) - timedelta(days=1), datetime.min.time())


def _assignment_for(assignments, building_code, day):
    candidates = [a for a in assignments
                  if a.building_code == building_code and a.valid_from <= day
                  and (a.valid_to is None or a.valid_to >= day)]
    return max(candidates, key=lambda a: (a.valid_from, a.id)) if candidates else None


def _load_assignments(db, building_codes):
    if not building_codes:
        return []
    return db.scalars(select(BuildingStationAssignment)
                      .where(BuildingStationAssignment.building_code.in_(list(building_codes)))
                      .order_by(BuildingStationAssignment.building_code,
                                BuildingStationAssignment.valid_from)).all()


def _issues_for_pairs(db, pairs):
    """Load only issues that could win for each (station, day) cutoff."""
    result = defaultdict(list)
    pairs = sorted(set(pairs))
    for start in range(0, len(pairs), 120):
        chunk = pairs[start:start + 120]
        stations = {s for s, _ in chunk}
        cutoff_by_day = {d: _cutoff(d) for _, d in chunk}
        max_cutoff = max(cutoff_by_day.values())
        wanted = set(chunk)
        rows = db.scalars(select(WeatherForecastIssue).where(
            WeatherForecastIssue.station_code.in_(stations),
            WeatherForecastIssue.target_day.in_(set(cutoff_by_day)),
            WeatherForecastIssue.published_at < max_cutoff,
        ).order_by(WeatherForecastIssue.published_at.desc(), WeatherForecastIssue.revision.desc())).all()
        for issue in rows:
            key = (issue.station_code, issue.target_day)
            if key in wanted and issue.published_at < cutoff_by_day[issue.target_day]:
                result[key].append(issue)
    return result


def _resolve(station, day, issues, cutoff):
    candidates = issues.get((station, day), [])
    evidence = {
        'rule': SELECTION_RULE,
        'station_code': station,
        'cutoff_at': iso(cutoff),
        'observed_value_used': False,
        'candidate_count': len(candidates),
        'candidates': [{
            'issue_id': i.id, 'published_at': iso(i.published_at), 'received_at': iso(i.received_at),
            'revision': i.revision, 'temperature_c': i.temperature_c, 'status': i.status,
            'excluded': 'withdrawn' if i.status == 'withdrawn' else None,
        } for i in candidates[:MAX_EVIDENCE_CANDIDATES]],
    }
    chosen = next((i for i in candidates if i.status != 'withdrawn'), None)
    if chosen is None:
        evidence['missing_reason'] = 'withdrawn_only' if candidates else 'no_forecast_before_cutoff'
        return None, evidence
    evidence['chosen'] = {
        'issue_id': chosen.id, 'published_at': iso(chosen.published_at),
        'received_at': iso(chosen.received_at), 'revision': chosen.revision,
        'temperature_c': chosen.temperature_c,
        'late': bool(chosen.received_at > cutoff),
    }
    return chosen, evidence


def _apply_selection(row, station, assignment, chosen, evidence, state):
    row.station_code = station
    row.assignment_id = assignment.id if assignment else None
    row.cutoff_at = _cutoff(row.day)
    if chosen is None:
        row.issue_id = None
        row.temperature_c = None
        row.published_at = row.received_at = None
        row.revision = None
        row.status = 'missing'
        row.missing_reason = evidence.get('missing_reason') or 'no_forecast_before_cutoff'
    else:
        row.issue_id = chosen.id
        row.temperature_c = chosen.temperature_c
        row.published_at = chosen.published_at
        row.received_at = chosen.received_at
        row.revision = chosen.revision
        row.status = 'issued'
        row.missing_reason = None
    row.evidence = evidence
    row.forecast_version = state.forecast_version
    row.assignment_version = state.assignment_version
    row.resolved_at = datetime.utcnow()


def _missing_assignment_evidence(day):
    return {'rule': SELECTION_RULE, 'cutoff_at': iso(_cutoff(day)), 'station_code': None,
            'observed_value_used': False, 'missing_reason': 'no_station_assignment',
            'candidate_count': 0, 'candidates': []}


def _assignment_evidence(assignment):
    return None if assignment is None else {
        'id': assignment.id, 'station_code': assignment.station_code,
        'valid_from': assignment.valid_from, 'valid_to': assignment.valid_to,
    }


def _update_row(row, assignments, issues, state):
    assignment = _assignment_for(assignments, row.building_code, row.day)
    if assignment is None:
        _apply_selection(row, None, None, None, _missing_assignment_evidence(row.day), state)
        return
    chosen, evidence = _resolve(assignment.station_code, row.day, issues, _cutoff(row.day))
    evidence['assignment'] = _assignment_evidence(assignment)
    _apply_selection(row, assignment.station_code, assignment, chosen, evidence, state)


def _load_rows(db, pairs):
    found = {}
    pairs = sorted(set(pairs))
    if not pairs:
        return found
    for scope in scoped_filters(pairs, BuildingDayForecast.building_code,
                                BuildingDayForecast.day):
        for row in db.scalars(select(BuildingDayForecast).where(scope)).all():
            found[(row.building_code, row.day)] = row
    return found


def _issue_pairs_for(assignments, building_days):
    pairs = set()
    for building, day in building_days:
        assignment = _assignment_for(assignments, building, day)
        if assignment:
            pairs.add((assignment.station_code, day))
    return pairs


def refresh_station_days(db, pairs, state):
    """Re-resolve only materialised building-days whose station-day is in ``pairs``."""
    pairs = {(s, d) for s, d in pairs if s}
    if not pairs:
        return 0
    affected = []
    for scope in scoped_filters(list(pairs), BuildingDayForecast.station_code,
                                BuildingDayForecast.day):
        affected.extend(db.scalars(select(BuildingDayForecast).where(scope)).all())
    if not affected:
        return 0
    issues = _issues_for_pairs(db, pairs)
    assignments = _load_assignments(db, {r.building_code for r in affected})
    count = 0
    for row in affected:
        assignment = _assignment_for(assignments, row.building_code, row.day)
        if assignment is None or (assignment.station_code, row.day) not in pairs:
            # The building moved away from this station-day; the assignment
            # change that moved it recomputes this row.
            continue
        _update_row(row, assignments, issues, state)
        count += 1
    db.flush()
    return count


def materialize_days(db, building_days, state):
    """Ensure a selection row exists for each supplied building-day (no global scan)."""
    if not building_days:
        return 0
    pairs = sorted({(r['building_code'], r['day']) if isinstance(r, dict)
                    else (r.building_code, r.day) for r in building_days})
    existing = _load_rows(db, pairs)
    missing = [p for p in pairs if p not in existing]
    if not missing:
        return 0
    assignments = _load_assignments(db, {b for b, _ in missing})
    issues = _issues_for_pairs(db, _issue_pairs_for(assignments, missing))
    for building, day in missing:
        row = BuildingDayForecast(building_code=building, day=day, cutoff_at=_cutoff(day))
        _update_row(row, assignments, issues, state)
        db.add(row)
    db.flush()
    return len(missing)


# --------------------------------------------------------------------- assignments
def _overlaps(db, data: AssignmentInput, ignore_id=None):
    query = select(BuildingStationAssignment).where(
        BuildingStationAssignment.building_code == data.building_code)
    if ignore_id is not None:
        query = query.where(BuildingStationAssignment.id != ignore_id)
    for current in db.scalars(query).all():
        if data.valid_to and current.valid_from > data.valid_to:
            continue
        if current.valid_to and current.valid_to < data.valid_from:
            continue
        return current
    return None


def _merge_ranges(ranges):
    ordered = sorted((start, end or '9999-12-31') for start, end in ranges)
    merged = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _in_ranges(day, merged):
    return any(start <= day <= end for start, end in merged)


def _refresh_building_ranges(db, building_code, ranges, state):
    """Recompute *only* one building's existing selections inside the day ranges."""
    merged = _merge_ranges(ranges)
    if not merged:
        return 0
    rows, days = [], set()
    for start_day, end_day in merged:
        rows.extend(db.scalars(select(BuildingDayForecast).where(
            BuildingDayForecast.building_code == building_code,
            BuildingDayForecast.day >= start_day,
            BuildingDayForecast.day <= end_day)).all())
        days.update(db.scalars(select(BuildingDay.day).where(
            BuildingDay.building_code == building_code,
            BuildingDay.day >= start_day,
            BuildingDay.day <= end_day)).all())
    existing = {(r.building_code, r.day): r for r in rows}
    absent = [(building_code, day) for day in days if (building_code, day) not in existing]
    if absent:
        materialize_days(db, [{'building_code': b, 'day': d} for b, d in absent], state)
        rows = [r for r in _load_rows(db, [(building_code, d) for d in days]).values()]
    assignments = _load_assignments(db, {building_code})
    issues = _issues_for_pairs(db, _issue_pairs_for(assignments, {(building_code, d) for d in days}))
    affected = [r for r in rows if _in_ranges(r.day, merged)]
    for row in affected:
        _update_row(row, assignments, issues, state)
    db.flush()
    return len(affected)


def list_assignments(db, building=''):
    query = select(BuildingStationAssignment)
    if building:
        query = query.where(BuildingStationAssignment.building_code == building)
    return db.scalars(query.order_by(BuildingStationAssignment.building_code,
                                     BuildingStationAssignment.valid_from)).all()


def _check_assignment(db, data, ignore_id=None):
    if data.valid_to and data.valid_to < data.valid_from:
        raise HTTPException(422, '归属结束日不能早于开始日')
    if not db.scalar(select(WeatherStation).where(WeatherStation.code == data.station_code)):
        raise HTTPException(422, '气象站未登记，请先建立站点')
    clash = _overlaps(db, data, ignore_id=ignore_id)
    if clash:
        raise HTTPException(409, f'与已有归属重叠（{clash.valid_from} 至 {clash.valid_to or "未定"}），请先调整旧区间')


def create_assignment(db, data: AssignmentInput):
    state = ensure_state(db)
    try:
        _check_assignment(db, data)
    except HTTPException:
        db.rollback()
        raise
    item = BuildingStationAssignment(**data.model_dump())
    db.add(item)
    db.flush()
    _bump(state, assignment=True)
    db.flush()
    refreshed = _refresh_building_ranges(db, data.building_code, [(data.valid_from, data.valid_to)], state)
    db.commit()
    return {'assignment': item, 'recomputed_building_days': refreshed,
            'assignment_version': state.assignment_version}


def update_assignment(db, assignment_id, data: AssignmentInput):
    state = ensure_state(db)
    item = db.get(BuildingStationAssignment, assignment_id, with_for_update=True)
    if not item:
        db.rollback()
        raise HTTPException(404, '归属记录不存在')
    if data.building_code != item.building_code:
        db.rollback()
        raise HTTPException(422, '归属记录不能改挂到另一栋楼，请新建并收束旧区间')
    try:
        _check_assignment(db, data, ignore_id=assignment_id)
    except HTTPException:
        db.rollback()
        raise
    old_range = (item.valid_from, item.valid_to)
    item.station_code = data.station_code
    item.valid_from = data.valid_from
    item.valid_to = data.valid_to
    item.note = data.note
    item.updated_at = datetime.utcnow()
    db.flush()
    _bump(state, assignment=True)
    db.flush()
    refreshed = _refresh_building_ranges(db, item.building_code,
                                         [old_range, (data.valid_from, data.valid_to)], state)
    db.commit()
    return {'assignment': item, 'recomputed_building_days': refreshed,
            'assignment_version': state.assignment_version}


def delete_assignment(db, assignment_id):
    state = ensure_state(db)
    item = db.get(BuildingStationAssignment, assignment_id, with_for_update=True)
    if not item:
        db.rollback()
        raise HTTPException(404, '归属记录不存在')
    day_range = (item.valid_from, item.valid_to)
    building = item.building_code
    db.delete(item)
    db.flush()
    _bump(state, assignment=True)
    # If a later, still-valid interval now covers these days it wins; otherwise
    # the affected days correctly become "no_station_assignment" (still recorded
    # as missing, never back-filled with the observed temperature).
    db.flush()
    refreshed = _refresh_building_ranges(db, building, [day_range], state)
    db.commit()
    return {'deleted': assignment_id, 'recomputed_building_days': refreshed,
            'assignment_version': state.assignment_version}


# ------------------------------------------------------------------------- listing
def _observed_temperatures(db, pairs):
    observed = {}
    pairs = sorted(set(pairs))
    if not pairs:
        return observed
    for scope in scoped_filters(pairs, BuildingDay.building_code, BuildingDay.day):
        for code, day, temperature in db.execute(select(
                BuildingDay.building_code, BuildingDay.day, BuildingDay.temperature_c)
                .where(scope)).all():
            observed.setdefault((code, day), temperature)
    return observed


def list_days(db, building='', start='', end='', status='', offset=0, limit=50):
    query = select(BuildingDayForecast)
    if building:
        query = query.where(BuildingDayForecast.building_code == building)
    if start:
        query = query.where(BuildingDayForecast.day >= start)
    if end:
        query = query.where(BuildingDayForecast.day <= end)
    if status in ('issued', 'missing'):
        query = query.where(BuildingDayForecast.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(BuildingDayForecast.building_code, BuildingDayForecast.day)
                      .offset(offset).limit(limit)).all()
    observed = _observed_temperatures(db, [(r.building_code, r.day) for r in rows])
    return {'total': total, 'rows': [{
        'building_code': r.building_code, 'day': r.day, 'station_code': r.station_code,
        'status': r.status, 'missing_reason': r.missing_reason,
        'forecast_temperature_c': r.temperature_c,
        'observed_temperature_c': observed.get((r.building_code, r.day)),
        'published_at': iso(r.published_at), 'received_at': iso(r.received_at),
        'revision': r.revision, 'cutoff_at': iso(r.cutoff_at),
        'forecast_version': r.forecast_version, 'assignment_version': r.assignment_version,
    } for r in rows]}


def list_missing(db, building='', offset=0, limit=50):
    query = select(BuildingDayForecast).where(BuildingDayForecast.status == 'missing')
    if building:
        query = query.where(BuildingDayForecast.building_code == building)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(BuildingDayForecast.building_code, BuildingDayForecast.day)
                      .offset(offset).limit(limit)).all()
    return {'total': total, 'rows': [{
        'building_code': r.building_code, 'day': r.day,
        'missing_reason': r.missing_reason, 'station_code': r.station_code,
        'cutoff_at': iso(r.cutoff_at),
    } for r in rows]}


def trace(db, building, day):
    parse_day(day)
    building = building.strip()
    row = db.scalar(select(BuildingDayForecast).where(
        BuildingDayForecast.building_code == building, BuildingDayForecast.day == day))
    if not row:
        raise HTTPException(404, '该楼宇日尚无预报选取记录，请先导入楼宇日数据')
    observed = db.scalar(select(BuildingDay.temperature_c).where(
        BuildingDay.building_code == building, BuildingDay.day == day).limit(1))
    issues = []
    if row.station_code:
        issues = [{
            'id': i.id, 'published_at': iso(i.published_at), 'received_at': iso(i.received_at),
            'revision': i.revision, 'temperature_c': i.temperature_c, 'status': i.status,
            'before_cutoff': i.published_at < row.cutoff_at,
            'withdrawn_at': iso(i.withdrawn_at), 'source_file': i.source_file,
        } for i in db.scalars(select(WeatherForecastIssue).where(
            WeatherForecastIssue.station_code == row.station_code,
            WeatherForecastIssue.target_day == day,
        ).order_by(WeatherForecastIssue.published_at.desc(), WeatherForecastIssue.revision.desc())).all()]
    withdrawals = []
    if row.issue_id:
        withdrawals = [{
            'issue_id': w.issue_id, 'occurred_at': iso(w.occurred_at),
            'received_at': iso(w.received_at), 'reason': w.reason, 'actor': w.actor,
        } for w in db.scalars(select(WeatherForecastWithdrawal)
                              .where(WeatherForecastWithdrawal.issue_id == row.issue_id)).all()]
    assignments = list_assignments(db, building)
    return {
        'building_code': building, 'day': day,
        'selection': {
            'status': row.status, 'missing_reason': row.missing_reason,
            'station_code': row.station_code, 'assignment_id': row.assignment_id,
            'issue_id': row.issue_id, 'forecast_temperature_c': row.temperature_c,
            'published_at': iso(row.published_at), 'received_at': iso(row.received_at),
            'revision': row.revision, 'cutoff_at': iso(row.cutoff_at),
            'forecast_version': row.forecast_version,
            'assignment_version': row.assignment_version,
            'resolved_at': iso(row.resolved_at),
        },
        'observed_temperature_c': observed,
        'observed_note': '实测气温仅作事后对照；缺报楼宇日不会以实测值替代预报进入研究。',
        'evidence': row.evidence,
        'issue_candidates': issues,
        'withdrawals': withdrawals,
        'assignments': [{
            'id': a.id, 'station_code': a.station_code, 'valid_from': a.valid_from,
            'valid_to': a.valid_to, 'note': a.note,
        } for a in assignments],
        'assignment_covering_day': next(({
            'id': a.id, 'station_code': a.station_code, 'valid_from': a.valid_from,
            'valid_to': a.valid_to,
        } for a in assignments if a.valid_from <= day and (a.valid_to is None or a.valid_to >= day)), None),
    }


# ------------------------------------------------------------------------ snapshots
def _snapshot_payload(rows):
    normalized = sorted(([
        r['building_code'], r['day'], r['energy_kwh'],
        [r['features'][f] for f in sorted(r['features'])],
        r['station_code'], r['issue_id'], r['temperature_c'],
        iso(r['published_at']), r['revision'], r['status'], iso(r['cutoff_at']),
    ] for r in rows), key=lambda r: (r[0], r[1]))
    return hashlib.sha256(json.dumps(normalized, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def build_snapshot(db, run, dataset):
    """Freeze forecast + assignment versions and copy the run's rows.

    Called inside the run-creation transaction; the snapshot is what the worker
    reads, so concurrent imports/withdrawals cannot alter an accepted study.
    """
    existing = db.scalar(select(ForecastRunSnapshot).where(ForecastRunSnapshot.run_id == run.id))
    if existing:
        return existing
    state = ensure_state(db)
    days = db.scalars(select(BuildingDay).where(BuildingDay.dataset_id == dataset.id)
                      .order_by(BuildingDay.building_code, BuildingDay.day)).all()
    materialize_days(db, days, state)
    db.flush()
    selections = _load_rows(db, [(d.building_code, d.day) for d in days])
    # Missing-forecast building-days are excluded from modelling, never replaced
    # by the observed temperature.  The split is computed on usable rows only so
    # that every recorded row id is one the model actually saw.
    needs_forecast = 'temperature_c' in run.config['features']
    usable = [d for d in days
              if not needs_forecast or selections[(d.building_code, d.day)].status == 'issued']
    from collections import Counter
    from src.services.splitting import split_buildings
    if needs_forecast:
        short = [building for building, count in Counter(d.building_code for d in usable).items() if count < 6]
        if short:
            raise HTTPException(422, '以下楼宇可用预报不足6天，先在预报资料库补齐：' + '、'.join(sorted(short)[:6]))
    # The train/sealed partition is always computed over the dataset's buildings,
    # not over the surviving rows: a missing forecast must never silently move a
    # building to the other side of the split.
    split = split_buildings([{'id': d.id, 'building_code': d.building_code, 'day': d.day,
                              'energy_kwh': d.energy_kwh} for d in days])
    if needs_forecast:
        # Keep the building partition identical whether or not days are missing,
        # but only advertise row ids the model can actually use.
        usable_ids = {d.id for d in usable}
        split = {**split,
                 'train_row_ids': [i for i in split['train_row_ids'] if i in usable_ids],
                 'test_row_ids': [i for i in split['test_row_ids'] if i in usable_ids]}
    snapshot = ForecastRunSnapshot(
        run_id=run.id, dataset_id=dataset.id,
        forecast_version=state.forecast_version, assignment_version=state.assignment_version,
        config=run.config, split=split, counts={}, fingerprint='',
    )
    db.add(snapshot)
    db.flush()
    payloads = []
    for day_row in days:
        selection = selections[(day_row.building_code, day_row.day)]
        features = {f: getattr(day_row, f) for f in run.config['features']}
        if needs_forecast:
            # temperature is forecast-only: missing days stay null and are excluded.
            features['temperature_c'] = selection.temperature_c
        db.add(ForecastSnapshotDay(
            snapshot_id=snapshot.id, source_day_id=day_row.id,
            building_code=day_row.building_code, day=day_row.day,
            energy_kwh=day_row.energy_kwh, features=features,
            station_code=selection.station_code, assignment_id=selection.assignment_id,
            issue_id=selection.issue_id, temperature_c=selection.temperature_c,
            published_at=selection.published_at, received_at=selection.received_at,
            revision=selection.revision, cutoff_at=selection.cutoff_at,
            status=selection.status, missing_reason=selection.missing_reason,
            evidence=selection.evidence or {},
        ))
        payloads.append({
            'building_code': day_row.building_code, 'day': day_row.day,
            'energy_kwh': day_row.energy_kwh, 'features': features,
            'station_code': selection.station_code, 'issue_id': selection.issue_id,
            'temperature_c': selection.temperature_c,
            'published_at': selection.published_at, 'revision': selection.revision,
            'status': selection.status, 'missing_reason': selection.missing_reason,
            'cutoff_at': selection.cutoff_at,
        })
    missing = [p for p in payloads if p['status'] == 'missing']
    excluded = [p for p in missing] if needs_forecast else []
    snapshot.counts = {
        'dataset_rows': len(payloads), 'rows': len(usable),
        'issued_rows': len(payloads) - len(missing), 'missing_rows': len(missing),
        'excluded_rows': len(excluded),
        'missing_building_days': [{'building_code': p['building_code'], 'day': p['day'],
                                   'reason': p['missing_reason']} for p in missing][:500],
        'missing_truncated': len(missing) > 500,
    }
    snapshot.fingerprint = _snapshot_payload(payloads)
    db.flush()
    return snapshot


def snapshot_rows(db, snapshot, features):
    """Frozen rows for modelling; only rows with an issued forecast are usable."""
    rows = []
    query = select(ForecastSnapshotDay).where(ForecastSnapshotDay.snapshot_id == snapshot.id)
    if 'temperature_c' in features:
        # A study that uses temperature must never fall back to the observed
        # value for a missing forecast day; those rows stay out of modelling.
        query = query.where(ForecastSnapshotDay.status == 'issued')
    for record in db.scalars(query.order_by(ForecastSnapshotDay.building_code,
                                            ForecastSnapshotDay.day)).all():
        rows.append({
            'id': record.source_day_id, 'building_code': record.building_code,
            'day': record.day, 'energy_kwh': record.energy_kwh,
            **{f: record.features[f] for f in features},
            'forecast_issue_id': record.issue_id, 'forecast_station_code': record.station_code,
            'forecast_published_at': iso(record.published_at),
            'forecast_received_at': iso(record.received_at),
            'forecast_revision': record.revision, 'forecast_cutoff_at': iso(record.cutoff_at),
            'forecast_status': record.status, 'forecast_missing_reason': record.missing_reason,
        })
    return rows


def snapshot_info(snapshot):
    return {
        'forecast_version': snapshot.forecast_version,
        'assignment_version': snapshot.assignment_version,
        'snapshot_fingerprint': snapshot.fingerprint,
        'counts': snapshot.counts,
    }


def run_forecast_evidence(db, run_id, offset=0, limit=100):
    """Paginated frozen rows for one run, for audit and export."""
    snapshot = db.scalar(select(ForecastRunSnapshot).where(ForecastRunSnapshot.run_id == run_id))
    if snapshot is None:
        raise HTTPException(404, '该研究尚未锁定预报版本')
    total = snapshot.counts.get('dataset_rows', 0)
    records = db.scalars(select(ForecastSnapshotDay).where(ForecastSnapshotDay.snapshot_id == snapshot.id)
                         .order_by(ForecastSnapshotDay.building_code, ForecastSnapshotDay.day)
                         .offset(offset).limit(limit)).all()
    uses_temperature = 'temperature_c' in (snapshot.config or {}).get('features', [])
    return {
        **snapshot_info(snapshot), 'run_id': run_id, 'total': total,
        'uses_temperature': uses_temperature,
        'rows': [{
            'building_code': r.building_code, 'day': r.day, 'status': r.status,
            'missing_reason': r.missing_reason, 'station_code': r.station_code,
            'issue_id': r.issue_id, 'temperature_c': r.temperature_c,
            'published_at': iso(r.published_at), 'received_at': iso(r.received_at),
            'revision': r.revision, 'cutoff_at': iso(r.cutoff_at),
            'used_in_model': r.status == 'issued' or not uses_temperature,
            'feature_temperature_used': uses_temperature and r.status == 'issued',
        } for r in records],
    }
