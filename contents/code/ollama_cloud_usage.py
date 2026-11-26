#!/usr/bin/env python3
"""Adapt the existing Hermes quota watcher's cache for the desktop widget.

Only reads usage figures and reset times. No network, credentials or writes.
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


WARN, CRITICAL = 75, 90


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value)
        return int(date.timestamp()) if date.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def read_quota(path):
    out: dict[str, Any] = dict(available=False, stamp=None, session=None, weekly=None, tone='unknown',
                              thresholds=dict(warn_pct=WARN, critical_pct=CRITICAL), reason='bad_payload')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return out | {'reason': 'cache_missing'}
    except (OSError, ValueError, RecursionError):
        return out
    if not isinstance(data, dict):
        return out
    history = data.get('history')
    if not isinstance(history, list) or not history or not isinstance(history[-1], dict):
        return out
    sample = history[-1]
    stamp = timestamp(sample.get('t'))
    if stamp is None:
        return out
    alerts = data.get('alerted')
    used = []
    for name, key in (('session', 's'), ('weekly', 'w')):
        remaining = sample.get(key)
        if isinstance(remaining, bool) or not isinstance(remaining, (int, float)) or not 0 <= remaining <= 100:
            continue
        entry = alerts.get(name) if isinstance(alerts, dict) else None
        reset = timestamp(entry.get('reset')) if isinstance(entry, dict) else None
        pct = round(100 - remaining)
        out[name] = {'pct': pct, 'resets_at': reset}
        used.append(pct)
    if used:
        highest = max(used)
        out.update(available=True, stamp=stamp, reason=None,
                   tone='critical' if highest >= CRITICAL else 'warn' if highest >= WARN else 'ok')
    return out


if __name__ == '__main__':
    home = Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes').expanduser()
    print(json.dumps(read_quota(home / 'state/ollama_quota_watch.json'), allow_nan=False))
