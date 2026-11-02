# MOSS-TTS on/off control

The AI SERVICES header shows one MOSS button, left of the Qwen button. It replaces the Nimble v3 button
(Nimble was decommissioned on 2026-11-02).

MOSS-TTS v1.5 BF16 is the `/voice` speech output of Hermes, not an LLM. It is owned by
`hermes-gpu-scheduler.service`:

- The unit itself is idle-cheap: no VRAM, about 25 MB RAM. For each voice reply the scheduler drains Hindsight,
  starts a MOSS process on the Blackwell (about 17 GB, load about 4 s), generates the audio and ends the process
  again.
- The scheduler API listens on `127.0.0.1:8097` (`GET /status`). Hermes talks to it through the command
  provider `moss-v15-bf16` (`scripts/moss_tts_client.py` of `~/src/hermes-gpu-scheduler`).

## Button

| Label | Meaning | Click |
| --- | --- | --- |
| `MOSS beenden` | the unit runs | wait until no voice job is running (up to 150 s), then stop the unit |
| `MOSS starten` | the unit is stopped | start the unit and wait until `/status` answers (up to 40 s) |
| `MOSS beendet …` / `MOSS startet …` | switching | button disabled |
| `MOSS · unbekannt` | state cannot be read | button disabled, status is re-checked every 5 s |
| `Fehler · …` | last action failed | tooltip carries the reason; the real state is shown again |

The tooltip also names a VRAM shortage: when less than `moss_required_free_mib` (default 19000 MiB, read from
`~/.local/state/moss-tts/scheduler-config.json`) is free and no voice job is running, for example because Qwen
holds the Blackwell. A voice reply would then fail.

While MOSS is off, `/voice` answers arrive as text only. The Hermes log shows `Auto voice reply TTS failed`, and
there is no fallback TTS provider. The Blackwell is completely free.

## Runtime

- `contents/ui/MossControl.qml` owns the button and polls every 5 s through four rotating executable-source names.
- `contents/code/moss_control.py status|start|stop` is the JSON-only bridge. It prints exactly one JSON object
  (`mode` `on`/`off`/`unknown`, `ready`, `busy`, `state`, `free_mib`, `blocker`, `note`, `ok`, `error`) and exits
  with 0 only when `ok` is true. It uses only `systemctl --user` and the loopback API, no sudo.
- `stop` never interrupts a running voice job: if one is still running after the wait, it reports an error and
  leaves the service on. A job counts as running when the API reports `current_job` or a queue, or when a
  `moss_service.py` process of the scheduler exists (the scheduler's own orphan test), so a silent API cannot
  cause an interrupted job. The one remaining gap is a job accepted in the few milliseconds between the idle check
  and `systemctl stop`: it is cancelled and Hermes falls back to a text reply.
- The scheduler unit has `Restart=on-failure`, so a clean `systemctl stop` does not respawn it. systemd's default
  `KillMode=control-group` also ends a leftover MOSS child of the unit.
- Autostart (`systemctl --user is-enabled hermes-gpu-scheduler.service` is `enabled`) is not changed. After a login
  MOSS is on again.

## Tests

`tests/test_moss_control.py` covers the helper against a fake scheduler API and a fake `systemctl`, the placement in
`main.qml` and the real Qt button through `qmltestrunner`. No production service is touched.
