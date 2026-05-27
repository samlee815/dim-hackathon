# PawDribble

A Unitree Go2 hackathon project for **dribbling a user-described ball to a
destination**, built on [DimOS](https://github.com/dimensionalOS/dimos).

This repo currently packages the perception and final kick slice as DimOS
skills: the user describes a ball, the robot identifies that specific object,
tracks it frame-to-frame, streams bbox / centering / apparent-size metrics, and
can body-charge through the ball once a teammate-owned planner has positioned
the robot behind it.

- **Design:** [`docs/pawdribble-design.md`](./docs/pawdribble-design.md)
- **Build plan:** [`plan.md`](./plan.md)
- **Environment (native Ubuntu + GPU):** [`SETUP.md`](./SETUP.md)
- **GPU host setup:** [`docs/gpu-host-setup.md`](./docs/gpu-host-setup.md)
- **Robot map scan:** [`docs/robot-map-scan.md`](./docs/robot-map-scan.md)
- **DimOS agent reference:**
  [`docs/dimos-agent-findings.md`](./docs/dimos-agent-findings.md)
- **Debugging / monitoring:** [`docs/debugging.md`](./docs/debugging.md)

## Active Slice

- **Skill container:** `PawDribbleSkillContainer` (one container, perception +
  kick)
  - `track_ball(description)`
  - `ball_tracking_status()`
  - `stop_tracking_ball()`
  - `kick_ball(speed_mps=None, duration_s=None)`
  - `stop_kick()`
- **Perception strategy:** VLM acquisition for the described ball, then EdgeTAM
  tracking for frame-to-frame monitoring.
- **Output:** JSON status on `/ball_status` with bbox, center pixel,
  image-center error, bbox area, optional mask area, and age.

## Teammate Seam

The planner / navigation / body-charge layer should consume the monitoring
stream (`/ball_status`, `/debug_image`) later. The current launcher exposes the
final `kick_ball` body-charge, but it does not plan routes, approach the ball,
or aim the robot.

## Layout

- `src/pawdribble/skill_container.py` — the DimOS skill container
  (`PawDribbleSkillContainer`): perception skills (VLM acquire + EdgeTAM track)
  plus the `kick_ball` body-charge and `stop_kick`.
- `src/pawdribble/ball_movement_state.py` — pure tracking logic: bbox/size/
  centering metrics plus the monitor state machine (status, drift gate,
  reacquire).
- `src/pawdribble/ball_movement_motion_fallback.py` — pure frame-diff fallback
  that re-seeds the tracker when EdgeTAM drops a moving ball.
- `src/pawdribble/kick_profile.py` — pure ramped charge-velocity profile.
- `src/pawdribble/image_source.py` — file/video frame source for no-robot runs.
- `src/pawdribble/system_prompt.py` — agent prompt: see the ball, then kick it.
- `scripts/run_pawdribble.py` — launcher: `--source FILE` / `--camera` run the
  pipeline end-to-end with no robot (Rerun viewer included), `--robot` at the
  venue (the kick toggles obstacle avoidance off only for the charge window).

## Run / Test

```bash
export REPO_ROOT="$(pwd)"
export DIMOS_VENV="${DIMOS_VENV:-$REPO_ROOT/.venv}"
source "$DIMOS_VENV/bin/activate" && cd "$REPO_ROOT"
pytest
PYTHONPATH=src python scripts/run_pawdribble.py
```

The off-robot blueprint should discover:
`track_ball`, `ball_tracking_status`, `stop_tracking_ball`, `kick_ball`, and
`stop_kick`.
