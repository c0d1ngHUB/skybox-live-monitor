# Skybox review fixes implementation plan

**Goal:** Fix all six findings in the approved 28/29 October review, preserve the existing five-second GPU history fix, commit, merge through protected Forgejo main and deploy locally.
**Spec:** /home/m3kky/Documents/Codex/2026-10-28/ka/outputs/skybox-live-monitor-review.md; user authorization: alles fixen, comitten, mergen und deployen.
**Architecture:** Keep the existing Python/JS/QML boundaries. Version the changed runtime start/stop scripts with the widget, reconcile failures under the controller lock, represent unavailable metrics explicitly, and maintain a bounded elapsed-time history.
**Tech stack:** Python3, POSIX process groups, bash/systemd, JavaScript, Qt6/QML, pytest, Forgejo CI.

## Constraints
- Preserve the five-second GPU-history tolerance and stricter live peak age.
- Preserve source and installed backups; no unrelated service, GPU routing or model changes.
- No inference or real model start is necessary to test a failure path. Validate deployed UI and service state.
- Preserve inactive initial Qwen state and active Hindsight backend.
- Forgejo main is primary and protected: PR + successful required checks before merge. Mirror resulting main to GitHub if configured and no unrelated divergence.

## Review focus
- A failed engine or gateway start after GPU release must restore only services active before the attempt.
- A timed-out action's descendants/systemd job must not overlap the next action, including failed cleanup.
- Legitimate zero and unchanged sensor values remain distinguishable from unavailable data.
- Both short scheduling delays and long/resumed/clock-reversed history intervals have bounded, truthful behavior.
- Runtime script tests must use controlled service boundaries, and committed runtime files must match deployed files.

## Task 1: Runtime handoff recovery and docs (root)
- [x] Add failing behavior tests for engine/readiness/gateway failure, previously inactive backend and failed reconciliation.
- [x] Version runtime/qwen38-start and runtime/qwen38-stop from installed scripts; add whole-start rollback that settles the engine before restoring the previously active Hindsight backend.
- [x] Make relevant existing script tests use versioned files; update GPU UUID documentation and runtime deployment instructions.
- [x] Run targeted tests and full suite after integration.

## Task 2: Controller timeout lifecycle (control agent)
- [x] Add RED regressions for surviving child, serialized next operation and uncertain systemd cleanup.
- [x] Implement group-aware execution and timeout reconciliation under flock; retain a recovery barrier if cleanup cannot prove completion.
- [x] Run relevant existing tests and report exact changes and limitations; do not deploy or commit.

## Task 3: Telemetry and history (UI agent)
- [x] Add RED regressions for unavailable/unchanged/zero CPU/RAM sensors, unknown GPU watts and pause/backwards-clock history.
- [x] Fix main.qml/monitor_logic.js with explicit validity/status handling and elapsed one-second history slots (cap120), keeping short-delay tolerance.
- [x] Update affected existing tests only for intentional interfaces; run UI/logic tests and report results; do not deploy or commit.

## Task 4: Integration, review, merge, deployment (root)
- [ ] Run required Qt suite, Node syntax, QML lint and whitespace; get one fresh independent whole-change review and fix material findings with regression tests.
- [ ] Commit changes, push Forgejo branch, create/attach PR, wait for required CI then merge; synchronize local main while preserving preexisting patch backup and mirror GitHub when safe.
- [ ] Deploy widget and changed runtime scripts with byte parity, reload Plasma, verify live UI + service health and preserve rollback evidence.
- [ ] Publish final report with commits, PR, CI, installed parity and verification limits.
