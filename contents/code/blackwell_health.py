#!/usr/bin/env python3
"""Read-only Blackwell health snapshots. Session-local, bounded cache; no sudo.

Ten-second samples estimate board energy, not system or per-process energy.
Gaps >25 s and failed polls are excluded, never interpolated as healthy zeros.
ECC aggregates and PCIe AER totals have separate baselines and may overlap
between endpoint/rootport: their deltas are not a count of unique incidents.
"""
from __future__ import annotations
import argparse
import json
import math
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

UUID = 'GPU-2bc81341-8b1a-3a2e-679b-7a2bb75c6d9e'
ECC_FIELDS = ('sram_correctable', 'dram_correctable', 'sram_uncorrectable_parity',
              'sram_uncorrectable_secded', 'dram_uncorrectable')
MAX_GAP = 25

def capture(argv):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=4, check=False)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None

def numeric(text):
    try:
        value = float(str(text).split()[0])
        return value if math.isfinite(value) and value >= 0 else None
    except (ValueError, IndexError):
        return None

def parse_gpu(xml, uuid):
    root = ET.fromstring(xml)
    gpu = next((g for g in root.findall('gpu') if g.findtext('uuid') == uuid), None)
    if gpu is None:
        raise ValueError('Target GPU absent')
    def num(path):
        return numeric(gpu.findtext(path))
    reasons = gpu.find('clocks_event_reasons')
    if reasons is None:
        reasons = gpu.find('clocks_throttle_reasons')
    flags = {} if reasons is None else {
        re.sub(r'^clocks_(event|throttle)_reason_', '', e.tag): e.text for e in reasons}
    thermal_flags = [flags.get(k) for k in ('hw_thermal_slowdown', 'sw_thermal_slowdown')]
    thermal = True if 'Active' in thermal_flags else (False if all(v == 'Not Active' for v in thermal_flags) else None)
    bdf = gpu.get('id', '').lower()
    if re.fullmatch(r'[0-9a-f]{8}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]', bdf):
        bdf = bdf[4:]
    if not re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]', bdf):
        bdf = None
    mode = gpu.findtext('ecc_mode/current_ecc')
    return {'uuid': uuid, 'bdf': bdf, 'name': gpu.findtext('product_name'),
            'ecc_mode': mode if mode in ('Enabled', 'Disabled') else None,
            'ecc': {k: num('ecc_errors/aggregate/' + k) for k in ECC_FIELDS},
            'reasons_complete': bool(flags) and all(v in ('Active', 'Not Active') for v in flags.values()),
            'thermal': thermal, 'reasons': [k for k, v in flags.items() if v == 'Active'] if flags else None,
            'sm_mhz': num('clocks/sm_clock'), 'memory_mhz': num('clocks/mem_clock'),
            'power_w': num('gpu_power_readings/average_power_draw'),
            'limit_w': num('gpu_power_readings/current_power_limit'),
            'temperature_c': num('temperature/gpu_temp'), 'utilization': num('utilization/gpu_util')}

def read_aer(device):
    counters = {}
    for scope, directory, files in (
        ('gpu', device, {'correctable': ('aer_dev_correctable', 'TOTAL_ERR_COR'),
                         'nonfatal': ('aer_dev_nonfatal', 'TOTAL_ERR_NONFATAL'),
                         'fatal': ('aer_dev_fatal', 'TOTAL_ERR_FATAL')}),
        ('root', device.parent, {'correctable': ('aer_rootport_total_err_cor', None),
                                'nonfatal': ('aer_rootport_total_err_nonfatal', None),
                                'fatal': ('aer_rootport_total_err_fatal', None)})):
        for kind, (filename, total) in files.items():
            value = None
            try:
                text = (directory / filename).read_text()
                if total:
                    value = next((numeric(line.split()[-1]) for line in text.splitlines() if line.startswith(total + ' ')), None)
                else:
                    value = numeric(text)
            except OSError:
                pass
            counters[scope + '/' + kind] = value
    return counters

def xid_events(rows, bdf):
    if not bdf:
        return []
    address = bdf.rsplit('.', 1)[0]
    result = []
    for row in rows:
        text = str(row.get('MESSAGE', ''))
        if re.search(r'\bXid\s*\(PCI:' + re.escape(address) + r'\)', text, re.I):
            result.append({'kind': 'xid', 'time': float(row.get('__REALTIME_TIMESTAMP', 0)) / 1e6,
                           'text': text[:240], 'cursor': row.get('__CURSOR')})
    return result

def journal(cursor=None):
    # Empty kernel journal does not prove visibility: report unknown rather than zero Xids.
    args = ['journalctl', '--system', '-k', '-b', '--no-pager', '-q', '-o', 'json']
    args += ['--after-cursor', cursor] if cursor else ['-n', '1']
    output = capture(args)
    if output is None or (not cursor and not output.strip()):
        return None
    try:
        return [json.loads(line) for line in output.splitlines() if line.strip()]
    except (ValueError, TypeError):
        return None

def advance(old, reading, now, boot, session):
    uuid = reading['uuid'] if reading else old.get('uuid', UUID)
    same = old.get('boot') == boot and old.get('session') == session and old.get('uuid') == uuid
    state = json.loads(json.dumps(old)) if same else {
        'boot': boot, 'session': session, 'uuid': uuid, 'started': now,
        'energy_wh': 0, 'covered_s': 0, 'events': [], 'new_faults': 0,
        'history': [], 'counters': {}, 'thermal_since': None, 'thermal_reported': False}
    def event(kind, text):
        state['events'].append({'time': now, 'kind': kind, 'text': text})
    prev = state.get('current')
    dt = now - state.get('stamp', now)
    contiguous = state.get('available') and isinstance(prev, dict) and reading is not None and 0 < dt <= MAX_GAP
    if contiguous and prev is not None and reading is not None and prev.get('power_w') is not None and reading.get('power_w') is not None:
        state['energy_wh'] += (prev['power_w'] + reading['power_w']) / 2 * dt / 3600
        state['covered_s'] += dt
    if reading is None:
        if state.get('available'):
            event('measurement', 'GPU-Messung ausgefallen; Werte unbekannt')
        state['thermal_since'] = None
        state['thermal_reported'] = False
    else:
        if same and state.get('available') is False:
            event('recovery', 'GPU-Messung wieder verfügbar')
        for group in ('ecc', 'aer'):
            for key, value in reading.get(group, {}).items():
                if value is None:
                    continue
                ident = group + '/' + key
                before = state['counters'].get(ident)
                if before is not None and value > before:
                    delta = value - before
                    state['new_faults'] += delta
                    event(group, ident + ' +' + str(int(delta)))
                elif before is not None and value < before:
                    event('reset', ident + ': Zähler zurückgesetzt; neue Basis')
                state['counters'][ident] = value
        if reading.get('thermal') is True:
            if not contiguous or state.get('thermal_since') is None:
                state['thermal_since'] = now
                state['thermal_reported'] = False
            if now - state['thermal_since'] >= 20 and not state.get('thermal_reported'):
                event('thermal', 'Thermische Drosselung seit mindestens 20 s beobachtet')
                state['thermal_reported'] = True
        else:
            state['thermal_since'] = None
            state['thermal_reported'] = False
    if reading and reading.get('bdf'):
        state['target_bdf'] = reading['bdf']
    state['history'] = [h for h in state['history'] if h['time'] >= now - 120][-13:]
    state['history'].append({'time': now, 'power': reading.get('power_w') if reading else None,
                             'temperature': reading.get('temperature_c') if reading else None,
                             'load': reading.get('utilization') if reading else None})
    # Time-weighted average of covered portions of the trailing minute.
    joules = seconds = 0
    for a, b in zip(state['history'], state['history'][1:]):
        duration = b['time'] - a['time']
        if duration <= 0 or duration > MAX_GAP or a['power'] is None or b['power'] is None:
            continue
        left = max(a['time'], now - 60)
        if b['time'] <= left:
            continue
        left_power = a['power'] + (b['power'] - a['power']) * (left - a['time']) / duration
        joules += (left_power + b['power']) / 2 * (b['time'] - left)
        seconds += b['time'] - left
    state.update({'current': reading, 'available': reading is not None, 'stamp': now,
                  'average_w': joules / seconds if seconds and reading is not None else None,
                  'average_covered_s': seconds, 'events': state['events'][-20:]})
    return state

def collect(state, session):
    now = time.time()
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    output = capture(['nvidia-smi', '-i', UUID, '-q', '-x'])
    try:
        reading = parse_gpu(output, UUID) if output else None
    except (ET.ParseError, ValueError):
        reading = None
    if reading:
        device = Path('/sys/bus/pci/devices') / reading['bdf'] if reading['bdf'] else None
        reading['aer'] = read_aer(device.resolve()) if device and device.exists() else {}
    result = advance(state, reading, now, boot, session)
    cursor = result.get('journal_cursor')
    rows = journal(cursor)
    result['journal_available'] = rows is not None and bool(result.get('target_bdf'))
    if rows is not None and result.get('target_bdf'):
        # First cursor is baseline only: never replay old Xids as new events.
        # Keep the last validated identity even if GPU telemetry itself fails.
        if cursor:
            events = xid_events(rows, result['target_bdf'])
            result['events'] = (result['events'] + events)[-20:]
        if rows:
            result['journal_cursor'] = rows[-1].get('__CURSOR')
    return result

def state_path(cache, session, boot):
    import hashlib
    identity = hashlib.sha256((boot + '\0' + session).encode()).hexdigest()[:32]
    return cache / ('blackwell-health-' + identity + '.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', default='cli')
    args = parser.parse_args()
    cache = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'skybox-live-monitor'
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    path = state_path(cache, args.session, boot)
    # Active sessions update their mtime every poll; expire only idle session files.
    for expired in cache.glob('blackwell-health-*.json'):
        try:
            if expired != path and time.time() - expired.stat().st_mtime > 86400:
                expired.unlink()
        except OSError:
            pass
    try:
        state = json.loads(path.read_text())
        if not isinstance(state, dict) or state.get('version') != 1:
            state = {}
    except (OSError, ValueError):
        state = {}
    try:
        result = collect(state, args.session)
        result['version'] = 1
        temporary = path.with_suffix('.' + str(os.getpid()) + '.new')
        temporary.write_text(json.dumps(result, allow_nan=False))
        temporary.chmod(0o600)
        temporary.replace(path)
        print(json.dumps(result, allow_nan=False, separators=(',', ':')))
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({'available': False, 'error': 'Gesundheitsmessung unbekannt'}))
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
