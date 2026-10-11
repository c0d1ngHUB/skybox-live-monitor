# Manual Qwen 3.8 control

The AI SERVICES header contains one state-dependent start/stop button.

## Runtime

- `contents/ui/QwenControl.qml` owns the button and independent 3-second status polling.
- `contents/code/qwen_control.py` reads `qwen38-llama-server.service` and invokes only `~/.local/bin/qwen38-start` / `qwen38-stop`.
- A sleeping Qwen server is still ON: its session reservation is not released until the server is stopped.
- Actions disable the button, use a per-user process lock and return verified service state. Unique executable-source request IDs discard disconnected/stale completions before buffer mutation.
- The priority gateway remains active when Qwen is OFF. Boot settings and model configuration are not modified.
- Stopping interrupts Qwen requests and restores only the Hindsight Ollama service. GPT-OSS warm-up follows its unit. Nimble is no longer used: it is never started, checked or probed by the stop script. The existing gateway configuration already has `[gpu_handoff].nimble = false` and remains unchanged.

## Existing stop-script readiness fix

A real test reproduced a `Type=simple` bind race: Nimble was systemd-active before its HTTP listener was ready. The first curl response was empty, causing `JSONDecodeError` and a false stop failure. The existing `~/.local/bin/qwen38-stop` now uses bounded curl connection-refused retries. Its obsolete Nimble start/check/probe path was subsequently removed to honor the current no-Nimble setup. No service or model configuration was changed.

`tests/test_qwen_stop_readiness.py` reproduces this with ephemeral loopback listeners and an intercepted `systemctl`; it never changes production services.

## Verified delivery

- 205 pytest cases passed without skips on the deployment host; the suite includes a no-Nimble start/check/probe regression.
- A real updated-button cycle verified that Nimble stayed inactive and its `ActiveEnterTimestamp` did not change.
- Node syntax, shell syntax and git whitespace checks passed.
- Installed runtime contents match source; Plasma is active.
- Real Qt Quick Test clicks on the installed component completed both start and stop successfully; actual units, Qwen listener, model API and Hindsight's Ollama API were read back.
- Live screenshots verified both labels without clipping or overlap.
- The initial ON/OFF state of the final continuous test was restored. Other pre-existing local source changes were preserved.
- Changes remain local and uncommitted.

## Durable rollback snapshots

Directory: `~/.hermes/backups/skybox-qwen-button-20261010-083244-verified/`

- `source-before.tar.gz`: source working-tree snapshot, including first-version feature tests.
- `installed-before.tar.gz`: installed widget before deployment.
- `qwen38-stop.before`: exact stop script before the readiness change.
- `qwen38-stop.before-no-nimble`: stop script with readiness retries, before removing the obsolete Nimble path.

Archive entries and stop-script backup were hash-verified. Prefer a targeted runtime rollback from the installed-widget snapshot; do not overwrite the entire dirty source tree blindly.
