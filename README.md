# Skybox Live Monitor

A compact, vertical real-time system dashboard for **KDE Plasma 6**. It is designed for a dedicated status display and shows CPU, GPU, VRAM, RAM, network throughput, disk space, and the most relevant active processes.

## Highlights

- Incident-oriented GPU/VRAM alerts with the two largest GPU consumers
- Compact CPU, GPU, and RAM process summaries
- Five-minute CPU/GPU and network history charts
- NVIDIA VRAM telemetry via `nvidia-smi`
- A deliberately scoped two-click **Unload Ollama** control for local Ollama models only
- No telemetry, no external network requests, and no credentials

## Requirements

- KDE Plasma 6
- NVIDIA GPU and `nvidia-smi` for GPU/VRAM metrics (other panels still work without it)
- `curl`, `jq`, and `ollama` are needed only for the optional unload control

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
systemctl --user restart plasma-plasmashell.service
```

## Safety of “Unload Ollama”

The control requires two clicks within five seconds. It queries the local Ollama API and stops only models currently served by the local Ollama daemon. It does **not** terminate arbitrary GPU processes, containers, system services, or remote workloads.

## Tests

```bash
python3 tests/test_dashboard_source.py
python3 tests/test_monitor_behavior.py
python3 tests/test_hermes_think_time.py
node --check contents/code/monitor_logic.js
qmllint contents/ui/main.qml
```

The Python behavior suite executes the actual network-detection and Ollama commands in isolated temporary environments. `qmllint` may report unresolved Plasma types when run outside Plasma's import environment; run the widget inside Plasma as the final integration check.

## License

MIT. See [LICENSE](LICENSE).
