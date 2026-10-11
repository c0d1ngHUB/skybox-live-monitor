# Skybox Live Monitor

![Skybox Live Monitor](screenshots/dashboard-preview.jpg)

A compact, vertical real-time system dashboard for **KDE Plasma 6**. It is designed for a dedicated status display and shows CPU, GPU, VRAM, RAM, network throughput, disk space, and the most relevant active processes.

## Highlights

- CPU temperature display (like GPU card — no percentage, no progress bar)
- CPU and RAM cards label their thread count, so a per-process 52% next to an
  aggregate 4% CPU reading is unambiguous
- Independent telemetry for every NVIDIA GPU (0, 1, 2): utilization, temperature,
  VRAM, power and per-GPU compute processes (up to four largest consumers). The
  three GPU cards sit side by side in one row, with the live GPU model name and
  used VRAM in decimal GB always visible (the bar still shows percent filled;
  hover for exact MiB and total capacity). CPU and RAM share the row below
- Compact CPU and RAM process summaries (up to four largest consumers)
- Narrow cards show compact M/G memory figures so process names remain readable;
  hover a row for the full process name or the source reading (RAM: unrounded
  RSS in KiB; GPU: unrounded VRAM in MiB)
- GPU utilization is sampled every 500 ms via a lightweight `nvidia-smi` query;
  VRAM, temperature, power and process lists remain on their two-second cadence.
  Each GPU card shows **PEAK 30s**, the highest observed whole-GPU utilization
  in the rolling window, without holding the current percentage above idle.
  Missing/stale samples show `--`, and GPU UUID changes reset the window.
  The `hindsight-api` process carries the descriptive **EMBED / RERANK** role
  label (not an activity indicator or process-specific utilization measurement).
- Two-minute multi-GPU and network history charts, with one shared unit per
  network panel so axis and live value always agree; the panels use independent
  auto-scales, called out above the charts
- Header shows the age of the oldest successful CPU-process, GPU, or network
  poll (not the separately polled services, disk, or other system sensors)
- Disk usage in `df` semantics, including the filesystem reserve
- NVIDIA VRAM telemetry via `nvidia-smi`
- Longest completed Hermes run across the default and named profiles;
  assistant tool-call preambles and unfinished turns do not count as completion
- GPU and network history show gaps when readings fail or expire; unavailable
  network readings are labelled rather than repeated as new measurements
- Default-route interface detection supports gateway, VPN and PPP routes and
  validates the interface against local sysfs
- Service cards return to UNKNOWN on helper failures or after 60 seconds without
  a completed service poll; root disk capacity comes exclusively from the same
  statvfs reading as its used/free values
- Hindsight observation health: the card reports the quality signals that
  survive a bank rebuild — **MERGE** (share of observations that merged more than
  one raw fact; higher is better, because consolidation that never merges only
  copies raw facts), **BACKLOG** (raw facts that no observation references and that
  carry no `consolidated_at`; must stay 0 — the remaining unreferenced-but-processed
  facts are an upstream bookkeeping artifact), **SCOPE n Δ d** (distinct observation
  scopes and their trend — `observation_scopes` comes from the source fact's tags
  and consolidation only merges inside one scope), **DRIFT n** (scopes whose tag set
  only wraps an existing one: a tag permutation that splits a pool which already
  existed) and **LAG** (how far the newest observation trails the newest raw fact).
  **UNTAG p%** is the share of observations sitting in the single untagged catch-all
  scope, so the scope count is never read without the concentration behind it.
  Thresholds arrive in the helper payload. The color is healthy only when MERGE
  reaches its target, the backlog is 0, LAG is small and the scope count did not
  grow into tag drift between two valid polls — a rising count without new drift is
  a new topic and stays healthy; an unknown trend is muted. The tooltip adds the
  consolidation throughput of the payload's window (LAG alone stays near zero as
  long as any recent fact got consolidated), the all-time tagless/single-proof
  percentages and a deterministic sample audit of the unreferenced facts
  (`contents/code/hindsight_observation_health.py --orphan-audit` prints the sampled
  rows). The frozen pre-reset reference (1009 observations, 37.6% merges) was dropped
  in favour of a rolling merge trend of the card's own polls, which is memory only
  and restarts with the widget.
- Manual **Qwen 3.8 start/stop button** in the AI SERVICES header. It reads the
  real Qwen server unit (not the always-on gateway), treats a sleeping model as
  still ON, and calls the existing `~/.local/bin/qwen38-start` / `qwen38-stop`
  scripts for the Blackwell GPU handoff. The button is disabled while switching
  or when status is unknown; failures are visible on the button and explained
  in its tooltip. Stopping interrupts Qwen requests and restores Hindsight only;
  the decommissioned Nimble service is neither started nor probed.
  Boot settings and gateway configuration are not changed.
- No telemetry, no external network requests, and no credentials

## Requirements

- KDE Plasma 6 (Wayland or X11)
- NVIDIA GPU and `nvidia-smi` for GPU/VRAM metrics (other panels still work without it)

## Install

```bash
kpackagetool6 --type Plasma/Applet --install .
```

To update an existing installation:

```bash
kpackagetool6 --type Plasma/Applet --upgrade .
```

Then add **Skybox Vertical System Dashboard** from Plasma's *Add Widgets* dialog.

For a development refresh:

```bash
rm -rf ~/.cache/qmlcache
systemctl --user restart plasma-plasmashell.service
```

## Tests

```bash
python3 -m pytest -q
node --check contents/code/monitor_logic.js
qmllint contents/ui/main.qml contents/ui/QwenControl.qml
```

The Python regression suite is the main test suite and covers GPU telemetry error contracts, process parsing, and QML integration. `qmllint` may report unresolved Plasma types when run outside Plasma's import environment; run the widget inside Plasma as the final integration check.

## License

MIT. See [LICENSE](LICENSE).