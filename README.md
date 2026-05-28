# PawTrack

A Unitree Go2 hackathon project for a **greeter dog**: the robot wanders a room,
finds a person sitting on a chair, walks over, and waves "hi" — built on
[DimOS](https://github.com/dimensionalOS/dimos).

Two layers, both implemented:

- **Greeter** (`greeter-agentic`, the headline demo): an autonomous loop —
  wander → identify a person on a chair → pick a random one not greeted recently
  → navigate to a standoff → wave hello (whichever way they face) → resume
  wandering. Start it with `start_greeting`.
- **Perception** (`pawtrack-agentic`): the lower-level tracker — describe a
  subject, the robot identifies and tracks it and publishes its floor position.
  Use it standalone via `track_subject`; the greeter reuses the same pieces.

- **Design:** [`docs/pawtrack-design.md`](./docs/pawtrack-design.md)
- **Build plan:** [`plan.md`](./plan.md)
- **Environment (native Ubuntu + GPU):** [`SETUP.md`](./SETUP.md)
- **GPU host setup:** [`docs/gpu-host-setup.md`](./docs/gpu-host-setup.md)
- **Robot map scan:** [`docs/robot-map-scan.md`](./docs/robot-map-scan.md)
- **DimOS agent reference:**
  [`docs/dimos-agent-findings.md`](./docs/dimos-agent-findings.md)
- **Debugging / monitoring:** [`docs/debugging.md`](./docs/debugging.md)

## Greeter Demo

- **Skill container:** `GreeterSkillContainer` (autonomous loop)
  - `start_greeting()` — begin wandering and greeting
  - `stop_greeting()` — halt the loop and the patrol
  - `greeter_status()` — current phase as JSON
- **Loop, each step a separate seam:** wander (DimOS `PatrollingModule`) →
  identify (multi-box VLM) → select (random, by floor position, not greeted in
  the last `revisit_forget_s`) → navigate (ground-distance + centering) → greet
  (halt → `Hello` wave, any facing → `RecoveryStand` + `BalanceStand`).

```bash
PYTHONPATH=src dimos run greeter-agentic --robot-ip <dog_ip>
dimos mcp call start_greeting
dimos mcp call stop_greeting
```

## Perception Slice

- **Skill container:** `PawTrackSkillContainer`
  - `track_subject(description="a person sitting on a chair", ...)`
  - `tracking_status()`
  - `stop_tracking()`
- **Strategy:** VLM acquisition for the described subject, then EdgeTAM tracking
  frame-to-frame.
- **Output:** JSON status on `/subject_status` (bbox, center pixel, image-center
  error, bbox area, optional mask area, age), plus the subject's floor position
  on `/subject_world_pose` (and `/subject_map_pose` with relocalization).

## Layout

- `src/pawtrack/skill_container.py` — the DimOS skill container
  (`PawTrackSkillContainer`): perception skills (VLM acquire + EdgeTAM track)
  and the ground-pose raycast. Perception only; no motion.
- `src/pawtrack/track_state.py` — pure tracking logic: bbox/size/centering
  metrics, the ground-contact pixel, and the monitor state machine (status,
  drift gate, reacquire).
- `src/pawtrack/motion_fallback.py` — pure frame-diff fallback that re-seeds the
  tracker on a miss (off by default; the camera moves with the dog).
- `src/pawtrack/ground_raycast.py` — pure pixel-to-floor raycast for the
  subject's absolute position.
- `src/pawtrack/qwen_china.py` — Qwen-VL on the Alibaba China DashScope endpoint.
- `src/pawtrack/image_source.py` — file/video frame source for no-robot runs.
- `src/pawtrack/system_prompt.py` — agent prompt: find and track the subject.
- `scripts/run_pawtrack.py` — launcher: `--source FILE` / `--camera` run the
  pipeline end-to-end with no robot (Rerun viewer included), `--robot` on the
  real Go2.

## Real Robot Agentic Runs

All real-robot runs are native Ubuntu DimOS runs from this repo, with the
PawTrack package on `PYTHONPATH`:

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
cp integration/pawtrack_agentic.py \
  "$DIMOS_HOME/dimos/robot/unitree/go2/blueprints/agentic/"

pytest -o addopts="" \
  "$DIMOS_HOME/dimos/robot/test_all_blueprints_generation.py"
```

### 2. Run PawTrack Agentic

This is the normal real-robot mode. It includes the Go2 agentic stack, the
PawTrack prompt, the PawTrack tools, and live subject tracking. It publishes
`/subject_world_pose` in the current odometry `world` frame. It does not require
a prebuilt map.

```bash
PYTHONPATH=src PAWTRACK_MODEL=openai:deepseek-chat \
  dimos --robot-ip "$ROBOT_IP" --rerun-open web run pawtrack-agentic
```

Expected planner-facing streams:

```text
/subject_status       JSON tracking diagnostics
/debug_image          annotated camera frame
/subject_world_pose   PoseStamped in live world/odom frame
/subject_map_pose     paused unless relocalization is running
```

### 3. Optional: Register Map-Relocalized Agentic

Use this only when you have exported a prebuilt `.pc2.lcm` map and want
`/subject_map_pose` in the stable `map` frame. The normal `pawtrack-agentic`
blueprint is left unchanged to avoid surprising behavior.

```bash
cp integration/pawtrack_agentic_relocalization.py \
  "$DIMOS_HOME/dimos/robot/unitree/go2/blueprints/agentic/"

pytest -o addopts="" \
  "$DIMOS_HOME/dimos/robot/test_all_blueprints_generation.py"
```

Run with a premap:

```bash
PYTHONPATH=src PAWTRACK_MODEL=openai:deepseek-chat \
  dimos --robot-ip "$ROBOT_IP" --rerun-open web \
  run pawtrack-agentic-relocalization \
  -o relocalizationmodule.map_file="<premap_name>"
```

Rules:

- `subject_world_pose` is always the primary pose for current-run planning.
- `subject_map_pose` only publishes when `RelocalizationModule` is present and
  `relocalizationmodule.map_file` points to a valid premap.
- If `map_file` is missing, relocalization disables itself and `subject_map_pose`
  stays paused.

### 4. Verify The Agentic Tools

Use direct MCP calls first, before relying on the LLM:

```bash
dimos mcp list-tools
dimos mcp call track_subject --arg description="a person sitting on a chair"
dimos mcp call tracking_status
dimos mcp call stop_tracking
```

The tool list should include `track_subject`, `tracking_status`, and
`stop_tracking`.

## Run Tests

```bash
export REPO_ROOT="$(pwd)"
export DIMOS_VENV="${DIMOS_VENV:-$REPO_ROOT/.venv}"
source "$DIMOS_VENV/bin/activate" && cd "$REPO_ROOT"
pytest
```
