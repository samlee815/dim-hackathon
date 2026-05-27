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

## Real Robot Agentic Runs

All real-robot runs are native Ubuntu DimOS runs from this repo, with the
PawDribble package on `PYTHONPATH`:

```bash
export REPO_ROOT="$(pwd)"
export DIMOS_HOME="${DIMOS_HOME:-/path/to/dimos}"
export DIMOS_VENV="${DIMOS_VENV:-$REPO_ROOT/.venv}"
export ROBOT_IP="<dog_ip>"

source "$DIMOS_VENV/bin/activate"
cd "$REPO_ROOT"
ping "$ROBOT_IP"
```

### 1. Register The Agentic Blueprint

`dimos run <name>` resolves names from DimOS' generated blueprint registry, so
the blueprint file must be copied into the DimOS source tree and the registry
must be regenerated:

```bash
cp integration/pawdribble_agentic.py \
  "$DIMOS_HOME/dimos/robot/unitree/go2/blueprints/agentic/"

pytest -o addopts="" \
  "$DIMOS_HOME/dimos/robot/test_all_blueprints_generation.py"
```

### 2. Run PawDribble Agentic

This is the normal real-robot mode. It includes the Go2 agentic stack,
PawDribble prompt, PawDribble tools, and live ball tracking. It publishes
`/ball_world_pose` in the current odometry `world` frame. It does not require a
prebuilt map.

```bash
PYTHONPATH=src PAWDRIBBLE_MODEL=openai:deepseek-chat \
  dimos --robot-ip "$ROBOT_IP" --rerun-open web run pawdribble-agentic
```

Expected planner-facing streams:

```text
/ball_status       JSON tracking diagnostics
/debug_image       annotated camera frame
/ball_world_pose   PoseStamped in live world/odom frame
/ball_map_pose     paused unless relocalization is running
```

### 3. Optional: Register Map-Relocalized Agentic

Use this only when you have exported a prebuilt `.pc2.lcm` map and want
`/ball_map_pose` in the stable `map` frame. The normal `pawdribble-agentic`
blueprint is left unchanged to avoid surprising behavior.

```bash
cp integration/pawdribble_agentic_relocalization.py \
  "$DIMOS_HOME/dimos/robot/unitree/go2/blueprints/agentic/"

pytest -o addopts="" \
  "$DIMOS_HOME/dimos/robot/test_all_blueprints_generation.py"
```

Run with a premap:

```bash
PYTHONPATH=src PAWDRIBBLE_MODEL=openai:deepseek-chat \
  dimos --robot-ip "$ROBOT_IP" --rerun-open web \
  run pawdribble-agentic-relocalization \
  -o relocalizationmodule.map_file="<premap_name>"
```

Rules:

- `ball_world_pose` is always the primary pose for current-run planning.
- `ball_map_pose` only publishes when `RelocalizationModule` is present and
  `relocalizationmodule.map_file` points to a valid premap.
- If `map_file` is missing, relocalization disables itself and `ball_map_pose`
  stays paused.

### 4. Verify The Agentic Tools

Use direct MCP calls first, before relying on the LLM:

```bash
dimos mcp list-tools
dimos mcp call track_ball --arg description="the red ball"
dimos mcp call ball_tracking_status
dimos mcp call stop_tracking_ball
dimos mcp call kick_ball --arg speed_mps=0.8 --arg duration_s=0.8
dimos mcp call stop_kick
```

The tool list should include `track_ball`, `ball_tracking_status`,
`stop_tracking_ball`, `kick_ball`, and `stop_kick`.

## Run Tests

```bash
export REPO_ROOT="$(pwd)"
export DIMOS_VENV="${DIMOS_VENV:-$REPO_ROOT/.venv}"
source "$DIMOS_VENV/bin/activate" && cd "$REPO_ROOT"
pytest
```
