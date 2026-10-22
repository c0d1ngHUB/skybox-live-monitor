# Blackwell health monitoring

The existing Skybox widget now includes a compact **BLACKWELL** row above the GPU cards. Click the row or **DETAILS** to expand the 120-second plot and current clocks. Hover for all counters, coverage information and the last 20 local events.

## Data and meaning

- UUID-bound to `GPU-2bc81341-8b1a-3a2e-679b-7a2bb75c6d9e`; indices are never used for health identity.
- Read-only `nvidia-smi -q -x` every 10 seconds. No GPU settings or service state changes. Unsupported fields remain unknown.
- ECC status and five aggregate SRAM/DRAM counters. Existing counts establish the initial baseline, not a new warning. Subsequent counter increases produce events, decreases establish a new baseline.
- PCIe AER endpoint and immediate upstream rootport totals are distinct from ECC. The summary is a sum of counter values, **not unique incidents**: endpoint/rootport may describe the same fault. Full counters are in the tooltip.
- All active clock-event reasons are exposed. A power cap with the 115 W efficiency profile is expected, not an error. Thermal events require at least 20 s of consecutive thermal-limited samples; this is sampled observation, not a continuous hardware guarantee.
- Kernel Xids are restricted to this GPU's PCI address. First journal cursor is a baseline; historical entries are not replayed as new. The collector does not elevate privileges. If the user cannot read the system kernel journal, **Xid is unknown**, not zero/healthy. No journal group memberships or sudo rules are installed.
- New ECC/AER/Xid/thermal events highlight the row for two minutes and remain in the bounded event list until displaced or the widget session changes. No Telegram notifications or separate alerting daemon are installed.

## Energy and history

The chart shows sampled board watts (violet, 0–145 W), GPU temperature (yellow, 0–100 °C) and utilization (cyan, 0–100%). Its elapsed-time axis covers the last 120 s; gaps are not joined.

Board energy is a trapezoidal approximation from ten-second samples. It excludes intervals longer than 25 s, failed GPU samples and missing power readings. It is **not a wall-meter or per-process measurement** and can miss short spikes. The tooltip shows the actually covered seconds. The trailing-minute average is time-weighted over covered intervals only; a partial window is explicitly labeled by covered seconds. If GPU telemetry becomes stale (>25 s), current values become unknown. Cumulative Wh remains labeled as historical since widget start.

State lives in `$XDG_CACHE_HOME/skybox-live-monitor/blackwell-health-<session-hash>.json` (default `~/.cache/...`, mode 0600). Each boot/widget session has its own atomically replaced file, so multiple widget instances cannot reset one another's counters. Files idle for more than 24 hours expire on subsequent collection. It is reset for a new widget session, GPU UUID or host boot. No long-term history/database, models, credentials or prompt text are stored.

## Validation and rollback

Run `pytest -q`, `qmllint contents/ui/BlackwellHealth.qml` and `git diff --check`. The suite includes parser/state-machine fixtures and a real Qt Quick test for click-to-expand, missing data, freshness and event warnings. Fixtures are synthetic; live counters must be checked independently.

Before deployment, source and installed widget were archived and file hashes checked at:
`~/.hermes/backups/skybox-blackwell-20261022-203409/`.

Deployment changes only `contents/ui/main.qml`, `contents/ui/BlackwellHealth.qml` and `contents/code/blackwell_health.py`. Reloading Plasma shell refreshes the desktop widget but does not log out or restart KWin, GPU drivers or KI services. Restore `main.qml` from `installed.tar.gz` to disable the added component; unused new helper/component files can remain. Then reload Plasma shell again. Source changes and the pre-existing modified Qwen plan are kept separate; no merge is authorized by this rollout.
