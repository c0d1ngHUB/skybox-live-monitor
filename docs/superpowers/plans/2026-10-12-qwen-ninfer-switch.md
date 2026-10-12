# Qwen 3.8 → NInfer Engine Switch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline; no subagent dispatch available in this session). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the local Qwen3.8-27B serving path from llama.cpp to the NInfer Docker engine, keeping the Skybox "Qwen 3.8 starten/stoppen" button, the priority gateway, and GPU handoff working unchanged.

**Architecture:** A new Docker wrapper (`~/.local/bin/qwen38-ninfer`) plus a new systemd user unit (`qwen38-ninfer.service`, Type=oneshot + RemainAfterExit, disabled at boot) serving on the same port 11437 with the same `/props.is_sleeping` contract. `qwen38-start`/`qwen38-stop` and `qwen_control.py` are re-pointed to the new unit. llma.cpp wrapper/unit are archived; an alias symlink keeps the old unit name resolving to NInfer for unknown consumers (e.g. Hermes Desktop).

**Tech Stack:** systemd user units, Docker 26.1.5, bash, Python 3, pytest, Qt Quick (untouched).

**Spec:** `docs/superpowers/specs/2026-10-12-qwen-ninfer-switch-design.md`

## Global Constraints

- Port `127.0.0.1:11437` must remain the serving address (`/props`, `/health`, `/v1/*`).
- Image pinned by digest: `ghcr.io/iamwavecut/ninfer-all@sha256:19b95534e0c54e6106f6931c757731294a1dce8bdc68e4c06132273d906fd24b`
- Flags: `--max-context 196608 --kv-capacity auto --kv-headroom-mib 192 --kv-dtype rk4v4 --gdn-state-fp16 --spec dflash2 --draft-tokens 5 --lm-head-draft --vision --vision-residency overlay --vision-max-merged 12288 --model-suspend --sleep-idle-seconds 600`
- Never touch: `qwen38-gateway.service` (running), `handoff.py`, `config.toml`, boot settings, `wait-qwen-loaded`, `free-blackwell-for-qwen` logic (comment edits skipped).
- `qwen38-stop` must keep the literal string `for port in 11439; do` (test_qwen_stop_readiness isolates it).
- No boot autostart for the new unit (user decision 3).

---

### Task 1: Backups

**Files:** Create `~/.hermes/backups/ninfer-switch-<TS>/` with: qwen38-start, qwen38-stop, qwen38-llama-server, qwen38-llama-server.service, qwen38-gateway.service, 40-qwen-ready.conf (hindsight+nimble), qwen_control.py (repo+installed), source tarball, installed tarball, SHA256SUMS.

- [ ] Create dir + copies + tarballs + checksums; verify checksums.

### Task 2: New wrapper + unit

**Files:** Create `~/.local/bin/qwen38-ninfer` (bash), `~/.config/systemd/user/qwen38-ninfer.service`.

- [ ] Write wrapper: `docker rm -f qwen38-ninfer` → `docker run -d --name qwen38-ninfer --rm --gpus '"device=2"' -p 127.0.0.1:11437:8080 --ulimit memlock=-1 -v /home/m3kky/models/ninfer:/models:ro -v ninfer-cache:/cache <image> serve <artifact> <flags>` → readiness loop: fail fast when container exited; poll `/health`; timeout `QWEN_READY_TIMEOUT` (default 240).
- [ ] `chmod +x`, `bash -n`.
- [ ] Write unit: Type=oneshot, RemainAfterExit=yes, Environment `QWEN_REQUIRED_FREE_MIB=24000`, ExecStartPre=free-blackwell-for-qwen, ExecStart=wrapper, ExecStop=`-/usr/bin/docker stop --time 60 qwen38-ninfer`, TimeoutStartSec=300, TimeoutStopSec=180, NoNewPrivileges=yes, PrivateTmp=yes, ConditionPathExists (artifact + wrapper), [Install] default.target, disabled.
- [ ] `systemctl --user daemon-reload`; `systemd-analyze --user verify`; `systemctl --user cat qwen38-ninfer.service`.

### Task 3: Wiring updates

**Files:** Modify `~/.local/bin/qwen38-start`, `~/.local/bin/qwen38-stop`, `~/.config/systemd/user/hindsight-ollama.service.d/40-qwen-ready.conf`, `~/.config/systemd/user/ollama-nimble.service.d/40-qwen-ready.conf`, `~/.config/systemd/user/qwen38-gateway.service`.

- [ ] qwen38-start: start/wait/verify `qwen38-ninfer.service` (comments updated).
- [ ] qwen38-stop: stop/verify `qwen38-ninfer.service` (keep `for port in 11439; do`).
- [ ] Drop-ins: `After=network-online.target qwen38-ninfer.service`.
- [ ] Gateway unit: `After=network.target qwen38-ninfer.service` (no restart).
- [ ] `bash -n` both scripts; `daemon-reload`; `systemctl --user show` checks.

### Task 4: Control helper + tests (repo)

**Files:** Modify `contents/code/qwen_control.py`, `tests/test_qwen_control.py`, `tests/test_qwen_stop_no_nimble.py`, `tests/test_qwen_stop_readiness.py`; create `tests/test_qwen_engine_wiring.py`; modify `docs/QWEN_CONTROL.md`.

- [ ] New wiring test (fake systemctl/curl recording argv): qwen38-start starts `qwen38-ninfer.service`, never `qwen38-llama-server.service`; qwen38-stop stops it; both exit 0. Run → FAIL (scripts still target old unit).
- [ ] Update `qwen_control.py`: unit → `qwen38-ninfer.service` (show + not-found text).
- [ ] Update the three existing tests' unit-name expectations.
- [ ] Run qwen tests → PASS; run full suite.
- [ ] docs/QWEN_CONTROL.md: engine switch section.

### Task 5: Deploy installed widget

- [ ] Copy `qwen_control.py` to installed plasmoid (kpackagetool6 upgrade or cp); diff installed vs repo (SAME); no plasmashell restart (helper is executed fresh per poll).

### Task 6: Archive llama.cpp + alias

- [ ] mv wrapper → `.bak-ninfer-switch-<TS>`; mv unit → `.bak-ninfer-switch-<TS>`; mv `qwen38-llama-server.service.d` → `.bak-ninfer-switch-<TS>` (avoid alias drop-in ambiguity).
- [ ] `ln -s qwen38-ninfer.service qwen38-llama-server.service`; daemon-reload; verify `systemctl --user show` resolves (Id=qwen38-ninfer.service, LoadState=loaded).

### Task 7: Live cycle test (real services)

- [ ] Capture pre-state (units, GPU, gateway `/proxy/status`, hindsight `/api/ps`).
- [ ] Real `qwen_control.py start` (as the button does) → verify: unit active, container running, `/props` reachable + `is_sleeping`, one real completion via 11437, gateway backend_ready.
- [ ] Suspend + wake check (handoff contract).
- [ ] Real `qwen_control.py stop` → verify: unit inactive, container gone, hindsight-ollama active + gpt-oss fully GPU-loaded, gateway active.
- [ ] Compare post-state to pre-state; save logs to `~/models/ninfer/eval/switch-live-<TS>.log`.

### Task 8: Close-out

- [ ] Commit repo changes (qwen_control.py, tests, docs, spec+plan).
- [ ] Update `~/models/ninfer/eval/SUMMARY.md`; patch skills `skybox-live-monitor-ui` + `local-ninfer-ops`; `hindsight_retain`.
- [ ] Final report: changed artifacts, evidence, rollback path (`~/.hermes/backups/ninfer-switch-<TS>/`), open items.
