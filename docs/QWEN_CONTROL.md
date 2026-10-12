# Manual Qwen 3.8 control

The AI SERVICES header contains one state-dependent start/stop button.

## Runtime

- `contents/ui/QwenControl.qml` owns the button and independent 3-second status polling.
- `contents/code/qwen_control.py` reads `qwen38-ninfer.service` and invokes only `~/.local/bin/qwen38-start` / `qwen38-stop`.
- A sleeping Qwen server is still ON: its session reservation is not released until the server is stopped.
- Actions disable the button, use a per-user process lock and return verified service state. An active unit also requires a confirmed running NInfer container; Docker query failures produce UNKNOWN. Unique executable-source request IDs discard disconnected/stale completions before buffer mutation.
- The priority gateway remains active when Qwen is OFF. Boot settings and model configuration are not modified.
- Stopping interrupts Qwen requests and restores only the Hindsight Ollama service. GPT-OSS warm-up follows its unit. Nimble is no longer used: it is never started, checked or probed by the stop script. The existing gateway configuration already has `[gpu_handoff].nimble = false` and remains unchanged.

## Engine: NInfer Docker server (switch 2026-10-12)

The engine behind the button changed from llama.cpp to NInfer (Docker). The
button, the gateway and the GPU handoff all observe the same contract as
before: serving on `http://127.0.0.1:11437`, `/props.is_sleeping`, control via
`qwen38-start` / `qwen38-stop`.

- `qwen38-ninfer.service` (systemd user unit, `Type=oneshot` +
  `RemainAfterExit=yes`, **disabled at boot**, no autostart) starts
  `~/.local/bin/qwen38-ninfer`, which runs the digest-pinned image
  `ghcr.io/iamwavecut/ninfer-all@sha256:19b95534e0c54e6106f6931c757731294a1dce8bdc68e4c06132273d906fd24b`
  as `docker run -d --rm --gpus '"device=2"'` with `~/models/ninfer` mounted
  read-only and the model cache in the `ninfer-cache` volume.
- Engine flags: 192k context (`--max-context 196608 --kv-capacity auto
  --kv-headroom-mib 192`), `rk4v4` KV compression, `--spec dflash2`
  speculation, vision (`--vision --vision-residency overlay`) and
  `--model-suspend` (scheduled idle sleep with `/props.is_sleeping`).
- `ExecStartPre=free-blackwell-for-qwen` (unchanged) frees the Blackwell
  before the container starts. `ExecStop=docker stop --time 60` ends the
  container together with the unit, and `--rm` removes it.
- The `40-qwen-ready.conf` drop-ins of `hindsight-ollama` and `ollama-nimble`
  plus `qwen38-gateway.service` order `After=qwen38-ninfer.service`.
- The llama.cpp wrapper and unit are archived as `*.bak-ninfer-switch-*`;
  `qwen38-llama-server.service` remains resolvable as a symlink alias to the
  new unit, so anything that only knows the old unit name keeps working.

### Failure recovery (2026-10-12)

The detached container can exit while the oneshot unit remains `active/exited`.
The status helper therefore checks the exact `qwen38-ninfer` container through
`docker container ls` before displaying ON. Sleeping still means ON; an
unreadable Docker state is UNKNOWN. The Plasma user needs access to the same
Docker daemon used by the start wrapper.

The NInfer wrapper removes its container when startup fails or receives a
termination signal. `ExecStopPost=docker rm -f qwen38-ninfer` also cleans up a
failed or forcibly interrupted `ExecStart`, for which systemd skips `ExecStop`.
The start script resets a confirmed stale active unit before retrying; a failed
Docker query aborts the action instead of treating the container as absent.
These wrappers and the user unit are local runtime dependencies outside this
repository. The local regression suite exercises copies with fake Docker and a
transient systemd unit; it does not load the production model.

## Existing stop-script readiness fix

A real test reproduced a `Type=simple` bind race: Nimble was systemd-active before its HTTP listener was ready. The first curl response was empty, causing `JSONDecodeError` and a false stop failure. The existing `~/.local/bin/qwen38-stop` now uses bounded curl connection-refused retries. Its obsolete Nimble start/check/probe path was subsequently removed to honor the current no-Nimble setup. No service or model configuration was changed.

`tests/test_qwen_stop_readiness.py` reproduces this with ephemeral loopback listeners and an intercepted `systemctl`; it never changes production services.

## Verified delivery (button, 2026-10-10)

- 205 pytest cases passed without skips on the deployment host; the suite includes a no-Nimble start/check/probe regression.
- A real updated-button cycle verified that Nimble stayed inactive and its `ActiveEnterTimestamp` did not change.
- Node syntax, shell syntax and git whitespace checks passed.
- Installed runtime contents match source; Plasma is active.
- Real Qt Quick Test clicks on the installed component completed both start and stop successfully; actual units, Qwen listener, model API and Hindsight's Ollama API were read back.
- Live screenshots verified both labels without clipping or overlap.
- The initial ON/OFF state of the final continuous test was restored. Other pre-existing local source changes were preserved.

## Durable rollback snapshots

Engine switch (2026-10-12): `~/.hermes/backups/ninfer-switch-<TS>/` — latest
timestamp pointer in `~/.hermes/backups/ninfer-switch-LATEST.path`. Contains
the pre-switch unit files, drop-ins, wrappers, `qwen_control.py` (repo +
installed) and source/installed tarballs, hash-verified via its `SHA256SUMS`.

Button delivery (2026-10-10): `~/.hermes/backups/skybox-qwen-button-20261010-083244-verified/`

- `source-before.tar.gz`: source working-tree snapshot, including first-version feature tests.
- `installed-before.tar.gz`: installed widget before deployment.
- `qwen38-stop.before`: exact stop script before the readiness change.
- `qwen38-stop.before-no-nimble`: stop script with readiness retries, before removing the obsolete Nimble path.

Archive entries and stop-script backup were hash-verified. Prefer a targeted runtime rollback from the installed-widget snapshot; do not overwrite the entire dirty source tree blindly.
