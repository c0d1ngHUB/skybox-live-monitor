# Nimble v3 residency control (auto / hold)

The AI SERVICES header shows a Nimble button left of the Qwen button.

- **Auto (default):** `nimble-v3-serve.service` keeps running, but the model is
  not resident. The Hindsight fact scorer loads it only when it has candidates
  and the Blackwell is free (no Qwen, no MOSS job, >= 17 GB free), with
  `keep_alive=60s`; Ollama unloads it afterwards. Load + first score ~6-8 s.
- **Hold ("Nimble v3 laden"):** pins the model (`keep_alive=-1`, ~6.5 GB idle,
  up to ~15-18 GB under load) until "Nimble v3 entladen" switches back to auto.
  Qwen and MOSS still take precedence; afterwards the model is reloaded.

## Runtime

- `runtime/nimble-ctl` (installed to `~/.local/bin/nimble-ctl`) owns the policy;
  the mode is persisted in `~/.config/hermes-nimble/mode`.
  Commands: `status | hold | auto | apply | mode`. `apply` never loads in auto and
  never loads while Qwen or a MOSS job owns the GPU.
- Consumers of the policy:
  - `nimble-v3-serve.service`: `ExecStartPost=-nimble-ctl apply` (replaces keep-model-warm).
  - LLM gateway `[gpu_handoff] resident_apply_command = [".../nimble-ctl", "apply"]`:
    after a Qwen session it starts the service and delegates residency instead of
    force-loading the model.
  - GPU scheduler `resident_apply_command` in `~/.local/state/moss-tts/scheduler-config.json`:
    restore after MOSS calls `nimble-ctl apply`; in auto an unloaded model counts as restored.
  - `~/.local/libexec/hermes/nimble-fact-scorer.py`: loads on demand, re-reads the
    mode per request (`-1` in hold, `60s` in auto).
- Nimble v3 (MLX runner) rejects `/api/generate` for loading; loading uses a
  `/v1/systemone` question. Unloading via `/api/generate keep_alive=0` works.
- `contents/ui/NimbleControl.qml` + `contents/code/nimble_control.py` mirror the
  QwenControl pattern (bounded executable sources, JSON-only bridge, busy lock).

## Qwen interplay

`free-blackwell-for-qwen` also stops `nimble-v3-serve.service`; `qwen38-start`
rollback and `qwen38-stop` restore `nimble-v3-serve.service` (formerly
`hindsight-ollama.service`, decommissioned 2026-11-01).
