# Skybox Live Monitor

![Skybox Live Monitor](screenshots/dashboard-preview.jpg)

A compact, vertical real-time system dashboard for **KDE Plasma 6**. It is designed for a dedicated status display and shows CPU, GPU, VRAM, RAM, network throughput, disk space, and the most relevant active processes.

## Highlights

- CPU temperature display (like GPU card — no percentage, no progress bar)
- CPU and RAM cards label their thread count, so a per-process 52% next to an
  aggregate 4% CPU reading is unambiguous
- Independent telemetry for every NVIDIA GPU (0, 1, 2): utilization, temperature,
  VRAM, power and per-GPU compute processes (up to four largest consumers). The
  three GPU cards sit side by side in one row; CPU and RAM share the row below
- Compact CPU and RAM process summaries (up to four largest consumers)
- Two-minute multi-GPU and network history charts, with one shared unit per
  network panel so axis and live value always agree
- Disk usage in `df` semantics, including the filesystem reserve
- NVIDIA VRAM telemetry via `nvidia-smi`
- Longest Hermes run across the default and named profiles
- Hindsight observation health: the card leads with the **scope distribution**
  (how many observation scopes exist, how many observations reached the shared
  untagged one) and the untagged share among rows created after the
  `observation_scopes="shared"` switch. The all-time tagless/single-proof
  percentages stay in the tooltip: they are diluted by every legacy row and
  moved only ~1.3 points per 20 new untagged rows, so they cannot show a fix.
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
qmllint contents/ui/main.qml
```

The Python regression suite is the main test suite and covers GPU telemetry error contracts, process parsing, and QML integration. `qmllint` may report unresolved Plasma types when run outside Plasma's import environment; run the widget inside Plasma as the final integration check.

## License

MIT. See [LICENSE](LICENSE).