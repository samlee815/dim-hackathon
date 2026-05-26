# PawDribble

A Unitree Go2 hackathon project for **dribbling a user-described ball to a
destination**, built on [DimOS](https://github.com/dimensionalOS/dimos).

This repo currently packages the perception slice as a DimOS skill: the user
describes a ball, the robot identifies that specific object, tracks it
frame-to-frame, and streams bbox / centering / apparent-size metrics. Planning,
walking to the ball, and kick/body-charge execution are intentionally left as a
teammate-owned integration layer.

- **Design:** [`docs/pawdribble-design.md`](./docs/pawdribble-design.md)
- **Build plan:** [`plan.md`](./plan.md)
- **Environment (native Ubuntu + GPU):** [`SETUP.md`](./SETUP.md)
- **GPU host setup:** [`docs/gpu-host-setup.md`](./docs/gpu-host-setup.md)
- **DimOS agent reference:** [`docs/dimos-agent-findings.md`](./docs/dimos-agent-findings.md)
- **Debugging / monitoring:** [`docs/debugging.md`](./docs/debugging.md)

## Active Slice

- **Ball monitoring skill:** `BallMonitorSkillContainer`
  - `track_ball(description)`
  - `ball_tracking_status()`
  - `stop_tracking_ball()`
- **Perception strategy:** VLM acquisition for the described ball, then EdgeTAM
  tracking for frame-to-frame monitoring.
- **Output:** JSON status on `/ball_status` with bbox, center pixel,
  image-center error, bbox area, optional mask area, and age.

## Teammate Seam

The planner / navigation / body-charge layer should consume the monitoring
stream later. This repo keeps the older pure dribble helpers as reference code,
but the current launcher does not drive `cmd_vel`, plan routes, or kick.

## Layout

- `src/pawdribble/ball_monitor.py` — DimOS skill container for user-described
  ball monitoring.
- `src/pawdribble/ball_tracker.py` — pure helpers for position smoothing and
  bbox/size metrics.
- `src/pawdribble/system_prompt.py` — monitoring-focused agent prompt.
- `scripts/run_pawdribble.py` — off-robot and robot launcher for the monitoring
  skill. The optional `--include-reference-dribble` flag also exposes the old
  dribble container for teammate integration checks.
- `src/pawdribble/dribble_planner.py`, `charge_control.py`,
  `skill_container.py` — reference dribble/control code, not the active slice.

## Run / Test

```bash
export REPO_ROOT="$(pwd)"
export DIMOS_VENV="${DIMOS_VENV:-$REPO_ROOT/.venv}"
source "$DIMOS_VENV/bin/activate" && cd "$REPO_ROOT"
pytest
PYTHONPATH=src python scripts/run_pawdribble.py
```

The off-robot blueprint should discover:
`track_ball`, `ball_tracking_status`, and `stop_tracking_ball`.
