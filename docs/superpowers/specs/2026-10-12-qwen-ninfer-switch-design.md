# Umbau: Qwen 3.8 von llama.cpp auf NInfer (harter Umstieg)

Datum: 2026-10-12
Status: Entwurf zur Freigabe

## Ziel

Das lokale Qwen-3.8-27B-Modell läuft künftig auf der **NInfer**-Engine (Docker,
`ghcr.io/iamwavecut/ninfer-all:latest`) statt auf llama.cpp. Der vorhandene
**Skybox-Button „Qwen 3.8 starten/stoppen"** im Live Monitor soll dabei exakt
weiter funktionieren wie bisher — inklusive GPU-Handoff an Hindsight und
Suspend/Resume. llama.cpp wird als Live-Pfad **nicht** weiterbetrieben (Wrapper
bleibt als Rollback-Artefakt archiviert).

Nutzerentscheidungen (2026-10-12):
1. Harter Umstieg — kein Live-Fallback auf llama.cpp.
2. Der Button startet/stoppt künftig NInfer als Standard.
3. Kein Boot-Autostart — die Unit fährt **nur** per Knopfdruck (bzw.
   `qwen38-start`) hoch; der Docker-Container folgt dem Unit-Lifecycle (`--rm`).

## Ausgangslage (verifiziert)

Kette heute:
`QwenControl.qml` → `qwen_control.py` (liest `http://127.0.0.1:11437/props`,
ruft `~/.local/bin/qwen38-start|stop`) → systemd-User-Unit
`qwen38-llama-server.service` (ExecStartPre `free-blackwell-for-qwen`) →
Gateway `:11435` (Hermes) / `:11438` (Hindsight) mit GPU-Handoff
(`handoff.py`: wartet auf `is_sleeping`, startet `hindsight-ollama`,
reserviert die Qwen-Session solange `:11437/props` antwortet).

Wichtige Verträge, die der Umbau einhalten muss:
- **Port 11437** bleibt die Qwen-Serving-Adresse (Gateway `backend_url`,
  `qwen_control.py`-Probe, `qwen38-start` Readiness).
- **`/props` mit `is_sleeping`** muss weiter existieren (NInfer liefert es —
  verifiziert).
- **`handoff.py`** muss unverändert bleiben: es prüft nur, ob `:11437/props`
  antwortet, und wartet auf `is_sleeping`.
- `free-blackwell-for-qwen` (ExecStartPre) bleibt unverändert — es ist
  engine-agnostisch (stoppt Ollama-Dienste, wartet auf ≥20 GiB frei).
- `qwen38-stop` startet `hindsight-ollama` wieder — bleibt unverändert.

Verifizierte NInfer-Parameter (2026-10-12):
- Image `sha256:19b95534e0c54e6106f6931c757731294a1dce8bdc68e4c06132273d906fd24b`
- Artefakt `~/models/ninfer/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-DFlash2-ninfer-v3.ninfer`
  (20 437 571 568 B, SHA256 `cc96e6b0…`)
- Startflags: `--max-context 196608 --kv-capacity auto --kv-headroom-mib 192
  --kv-dtype rk4v4 --gdn-state-fp16 --spec dflash2 --draft-tokens 5
  --lm-head-draft --vision --vision-residency overlay --vision-max-merged 12288
  --model-suspend --sleep-idle-seconds 600`
- Volume `ninfer-cache` (Device-Profil), Port `127.0.0.1:11437:8080`,
  `--gpus '"device=2"'` (PRO 4000), `--ulimit memlock=-1`.
- ECC bleibt aus (bestätigt).

## Komponenten

### Neu: `~/.local/bin/qwen38-ninfer` (Docker-Wrapper)
Startet den Container und wartet auf Serving-Readiness.
- `docker rm -f qwen38-ninfer` (Aufräumen verwaister Container)
- `docker run -d --name qwen38-ninfer --rm --gpus '"device=2"'
  -p 127.0.0.1:11437:8080 --ulimit memlock=-1
  -v /home/m3kky/models/ninfer:/models:ro -v ninfer-cache:/cache
  <image>@<digest> serve /models/<artefakt> <flags>`
- Readiness-Schleife auf `GET /health == 200` mit Timeout (Start ~8 s warm,
  ~60 s kalt) — die Unit-Startzeit muss dazu passen.
- Wird von der Unit als `ExecStart` genutzt; **nicht** blockierend als
  Vordergrundprozess, sondern Start + Readiness + exit (Type=oneshot).

> Design-Entscheidung: Die Unit wird `Type=oneshot` mit
> `RemainAfterExit=yes`. `ExecStart` = Wrapper (startet Container + wartet
> ready), `ExecStop` = `docker stop qwen38-ninfer` (Container-Lifecycle an
> die Unit gebunden). Damit ist `systemctl --user start|stop` weiterhin die
> einzige Steuerfläche — genau wie heute.

### Neu: `~/.config/systemd/user/qwen38-ninfer.service`
```
[Unit]
Description=Qwen3.8 27B via NInfer (Blackwell, Docker)
After=network.target docker.service
ConditionPathExists=/home/m3kky/models/ninfer/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-DFlash2-ninfer-v3.ninfer
ConditionPathExists=/home/m3kky/.local/bin/qwen38-ninfer

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/home/m3kky/.local/bin/free-blackwell-for-qwen
ExecStart=/home/m3kky/.local/bin/qwen38-ninfer
ExecStop=/usr/bin/docker stop --time 60 qwen38-ninfer
TimeoutStartSec=300
TimeoutStopSec=180

[Install]
WantedBy=default.target
```
`disabled` (kein Autostart) — entspricht Nutzerwunsch 3 und dem heutigen
Zustand von `qwen38-llama-server.service`.

### Geändert: `~/.local/bin/qwen38-start`
Startet `qwen38-ninfer.service` statt `qwen38-llama-server.service`;
Readiness-Probe bleibt `:11437/props`; Gateway-Start bleibt.

### Geändert: `~/.local/bin/qwen38-stop`
Stoppt `qwen38-ninfer.service` statt `qwen38-llama-server.service`; der Rest
(Hindsight-Ollama zurückholen, Port-11439-Check) bleibt identisch.

### Geändert: `qwen_control.py`
- Statusprobe liest `qwen38-ninfer.service` statt `qwen38-llama-server.service`.
- `/props`-Probe (`is_sleeping`) bleibt unverändert — NInfer liefert das Feld.
- Fehlermeldungen/Detailtexte nennen die neue Unit.

### Unverändert
- `QwenControl.qml` (Button-Oberfläche, Polling, Lock, Request-IDs).
- `free-blackwell-for-qwen`, `handoff.py`, `config.toml`, Gateway-Units,
  `wait-qwen-loaded`, `hindsight-wait-qwen-ready`.

### Archiviert (Rollback, nicht live)
- `~/.local/bin/qwen38-llama-server` → `.bak-ninfer-switch-<ts>`
- `~/.config/systemd/user/qwen38-llama-server.service` → `.bak-…`
- Drop-in-Verzeichnis `qwen38-llama-server.service.d/` verbleibt als Backup.

## Datenfluss

Start (Button/CLI):
`QwenControl.qml` → `qwen_control.py start` (Lock) → `qwen38-start` →
`systemctl start qwen38-ninfer` → `ExecStartPre free-blackwell-for-qwen`
(stoppt Ollama, wartet ≥20 GiB) → `qwen38-ninfer` startet Container, wartet
`:11437/health=200` → `qwen38-start` prüft `:11437/props` → startet
`qwen38-gateway` → `qwen_control.py` verifiziert `running/sleeping`.

Stop:
`qwen_control.py stop` → `qwen38-stop` → `systemctl stop qwen38-ninfer`
→ `ExecStop docker stop` (Container beendet, VRAM frei) →
`systemctl start hindsight-ollama` → Port-11439-Check.

Handoff (unverändert): Gateway fragt `:11437/props`; solange erreichbar,
bleibt die Qwen-Session reserviert; geht sie auf `is_sleeping`, holt der
Gateway `hindsight-ollama` zurück.

## Fehlerbehandlung

- **Container startet nicht / Image fehlt:** Wrapper bricht mit Exit≠0 ab,
  Unit `failed`, `qwen_control.py` meldet Klartextfehler (Exit-Code +
  letzte Logzeilen) — gleiches Muster wie heute.
- **Readiness-Timeout:** `docker logs` der letzten Zeilen in stderr,
  Exit≠0, Button bleibt gesperrt + Fehler im Tooltip.
- **Verwaister Container:** `docker rm -f qwen38-ninfer` vor jedem Start.
- **Doppelklick:** bestehender `fcntl`-Lock in `qwen_control.py` bleibt.

## Testing

- Anpassung `tests/test_qwen_control.py`: erwartete Unit heißt
  `qwen38-ninfer.service`.
- Neue Tests analog zu den bestehenden (gefakte `systemctl`-Ausgabe,
  ephemerer Loopback-Listener): Start/Stop-Zyklus gegen die neue Unit,
  Fehlertext bei Startup-Fehler.
- `tests/test_qwen_button.py`, `test_qwen_stop_no_nimble.py`,
  `test_qwen_stop_readiness.py` bleiben sinngemäß gültig (Nimble-Freiheit
  unberührt).
- **Realer Live-Test** (wie beim Button-Einbau): Button einmal starten und
  einmal stoppen, echte Unit/Container/`/props`/Hindsight-API zurücklesen,
  Nimble-`ActiveEnterTimestamp` vorher/nachher vergleichen, Ausgangszustand
  wiederherstellen, Screenshot des Labels.

## Rollback

1. `systemctl --user stop qwen38-ninfer; systemctl --user disable qwen38-ninfer`
2. `~/.local/bin/qwen38-llama-server.bak-ninfer-switch-<ts>` zurückspielen,
   Unit-Backup zurückholen, `daemon-reload`.
3. Gateway/Handoff/Config wurden nicht angefasst → kein Rollback nötig.
4. Vollständige Snapshots unter `~/.hermes/backups/ninfer-switch-<ts>/`.

## Nicht im Scope

- ECC (bleibt aus, bestätigt).
- Änderungen an Gateway-Logik, Handoff, Ollama-Diensten, Boot-Einstellungen.
- Umbau des Tune-Samplers (Artefakt empfiehlt temp 1.0/0.95/20; Wrapper
  nutzt 0.6) — nur als Hinweis, nicht Teil dieses Umbaus.
