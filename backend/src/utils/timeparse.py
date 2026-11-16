from datetime import date, datetime, timezone


def parse_day(value: str) -> date:
    try:
        parsed = date.fromisoformat(str(value).strip())
    except ValueError:
        raise ValueError(f'日期须为YYYY-MM-DD：{value}')
    if parsed.isoformat() != str(value).strip():
        raise ValueError(f'日期须为YYYY-MM-DD：{value}')
    return parsed


def parse_datetime(value) -> datetime:
    """Parse a publisher timestamp and normalise it to naive UTC.

    Accepted forms are ISO-8601 (``T`` separator, optional ``Z``/offset) and the
    spreadsheet form ``YYYY-MM-DD HH:MM:SS``.
    """
    text = str(value).strip()
    if not text:
        raise ValueError('时间不能为空')
    candidate = text[:-1] + '+00:00' if text.endswith('Z') else text
    parsed = None
    for fmt in (None, '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            parsed = datetime.fromisoformat(candidate) if fmt is None else datetime.strptime(candidate, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        raise ValueError(f'时间须为ISO-8601或YYYY-MM-DD HH:MM:SS：{value}')
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def iso(value: datetime | None) -> str | None:
    return value.replace(microsecond=0).isoformat() if value else None
