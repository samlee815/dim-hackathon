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
stream (`/ball_status`, `/debug_image`) later. The current launcher only
monitors -- it does not drive `cmd_vel`, plan routes, or kick.

## Layout

- `src/pawdribble/ball_movement_monitor.py` — DimOS skill container for
  user-described ball monitoring (VLM acquire + EdgeTAM track).
- `src/pawdribble/ball_movement_state.py` — pure tracking logic: bbox/size/
  centering metrics plus the monitor state machine (status, drift gate,
  reacquire).
- `src/pawdribble/ball_movement_motion_fallback.py` — pure frame-diff fallback
  that re-seeds the tracker when EdgeTAM drops a moving ball.
- `src/pawdribble/image_source.py` — file/video frame source for no-robot runs.
- `src/pawdribble/system_prompt.py` — monitoring-focused agent prompt.
- `scripts/run_pawdribble.py` — launcher: `--source FILE` / `--camera` run the
  monitor end-to-end with no robot (Rerun viewer included), `--robot` at the
  venue.

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
