# MOSS-TTS on/off control

The KI-DIENSTE (AI services) header shows one MOSS button, left of the Qwen button. It replaces the Nimble v3 button
(Nimble was decommissioned on 2026-11-02).

MOSS-TTS v1.5 BF16 is the `/voice` speech output of Hermes, not an LLM. It is owned by
`hermes-gpu-scheduler.service`:

- Since 2026-11-07 the scheduler runs in **resident mode** (`moss_resident: true` in
  `~/.local/state/moss-tts/scheduler-config.json`): while the unit runs, MOSS stays loaded on the Blackwell
  (about 16.4 GiB, load about 7-10 s) and a voice reply starts without loading. During a Qwen session
  (`qwen38-ninfer.service` not inactive, also while Qwen sleeps) or when free VRAM drops below 1 GiB (a game
  grows), the scheduler unloads MOSS and loads it again afterwards by itself.
- Stopping the unit ends MOSS and frees its memory completely. Without resident mode (older scheduler) MOSS
  loads per reply and the idle unit holds no VRAM; the helper handles both.
- The scheduler API listens on `127.0.0.1:8097` (`GET /status`, with `moss_resident`, `moss_loaded` and
  `moss.state`/`moss.reason`/`moss.vram_mib` in resident mode). Hermes talks to it through the command
  provider `moss-v15-bf16` (`scripts/moss_tts_client.py` of `~/src/hermes-gpu-scheduler`).

## Button

| Label | Meaning | Click |
| --- | --- | --- |
| `● MOSS STOPPEN` | the unit runs (resident mode: MOSS is in memory) | wait until no voice job is running (up to 150 s), stop the unit, confirm no MOSS process is left |
| `◐ MOSS LÄDT …` | resident mode: the unit runs, MOSS is loading | stop, as above |
| `◐ MOSS WARTET (QWEN)` / `◐ MOSS WARTET` | resident mode: MOSS is unloaded for Qwen or because memory is short; it loads by itself later | stop, as above |
| `○ MOSS STARTEN` | the unit is stopped, no MOSS in memory | start the unit, wait until `/status` answers (up to 40 s) and, in resident mode, until MOSS is loaded (up to 90 s) or the scheduler names the reason it waits |
| `◐ MOSS STOPPT …` / `◐ MOSS STARTET …` | switching | button disabled |
| `? MOSS UNBEKANNT` | state cannot be read | button disabled, status is re-checked every 5 s |
| `▲ MOSS FEHLER · …` | last action failed, or MOSS could not be loaded (retried after about a minute) | tooltip carries the reason; the real state is shown again |

The leading symbol is the same state vocabulary as the service rows (● running, ○ off, ◐ switching,
▲ error, ? unknown); the border/text colour only repeats it.

The tooltip also names a VRAM shortage: when MOSS still has to load and less than `moss_required_free_mib`
(default 19000 MiB, currently 17500, read from `~/.local/state/moss-tts/scheduler-config.json`) is free and no
voice job is running, for example because Qwen holds the Blackwell. A loaded MOSS holds its own memory, so a low
free figure is then no shortage.

While MOSS is off, `/voice` answers arrive as text only. The Hermes log shows `Auto voice reply TTS failed`, and
there is no fallback TTS provider. The Blackwell is completely free.

## Runtime

- `contents/ui/MossControl.qml` owns the button and polls every 5 s through four rotating executable-source names.
- `contents/code/moss_control.py status|start|stop` is the JSON-only bridge. It prints exactly one JSON object
  (`mode` `on`/`off`/`unknown`, `ready`, `busy`, `state`, `free_mib`, `blocker`, `note`, `ok`, `error`) and exits
  with 0 only when `ok` is true. It uses only `systemctl --user` and the loopback API, no sudo.
- `stop` never interrupts a voice reply. It waits (up to 150 s) until all of this is false, then stops the unit; if the
  wait runs out it reports an error and leaves the service on:
  - the API reports `current_job` or a queue;
  - the scheduler's MOSS child exists — except in resident mode, where a loaded MOSS child is the normal idle
    state (with a silent API it still counts, because nothing proves it is idle);
  - a Hermes TTS client (`moss_tts_client.py`) is still alive. It collects a job's result only every 5 s, so stopping
    the scheduler right after the job ended would leave it polling a dead API until its 900 s timeout and lose the
    reply (found in a live test on 2026-11-02);
  - the scheduler changed state less than 8 s ago (`ts` in `~/.local/state/moss-tts/scheduler-state.json`). A long
    reply is several jobs in a row with short gaps between the chunks.

  Processes are matched by exact argv (`<python> …/hermes_gpu_scheduler/moss_service.py`, `<python> …/moss_tts_client.py`,
  see `moss_proc.py`), not by substring: a shell, editor or `grep` that merely mentions a script must not block a stop.
  The one remaining gap is a reply that starts in the few milliseconds between the last check and `systemctl stop`:
  it is cancelled and Hermes falls back to a text reply.
- The scheduler unit has `Restart=on-failure`, so a clean `systemctl stop` does not respawn it. The scheduler
  unloads MOSS on SIGTERM, and systemd's default `KillMode=control-group` also ends a leftover MOSS child of the
  unit. After the stop the helper waits up to 15 s until no MOSS process is left and reports the free Blackwell
  memory; a surviving MOSS process is reported as an error.
- Autostart (`systemctl --user is-enabled hermes-gpu-scheduler.service` is `enabled`) is not changed. After a login
  MOSS is on again.

## Tests

`tests/test_moss_control.py` covers the helper against a fake scheduler API and a fake `systemctl`, the placement in
`main.qml` and the real Qt button through `qmltestrunner`. No production service is touched.
