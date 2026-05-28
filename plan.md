# PawTrack Build Plan

## Scope

PawTrack's demo is a **greeter dog**: a Go2 that wanders a room, finds a person
sitting on a chair, walks over to a polite standoff, and waves "hi" (whichever
way they face), then resumes wandering. The build splits into two layers:

1. **Perception (this slice, done):** identify a described subject, track it,
   publish image-space metrics and the subject's floor position, all as DimOS
   skills.
2. **Motion (next):** an autonomous loop — wander, approach to a polite
   standoff, wave once (any facing), greet — built on top of the perception
   streams.

## Perception Pipeline

1. User / agent: "track the person sitting on the chair".
2. Agent calls `track_subject(description="a person sitting on a chair")`.
3. `PawTrackSkillContainer` uses the latest RGB frame and a VLM to acquire a
   bbox for the described subject.
4. EdgeTAM initializes from that bbox and tracks the selected subject
   frame-to-frame.
5. The module publishes `/subject_status` JSON and serves `tracking_status()`
   with:
   - status: `idle`, `acquiring`, `tracking`, `lost`, `stale`, `stopped`, or
     `error`
   - description
   - bbox and center pixel
   - x/y image-center error
   - bbox width, height, area, and area ratio
   - optional mask area and mask-area ratio
   - last-seen age
6. While tracking, it raycasts the bbox bottom-center to the floor and publishes
   `/subject_world_pose` (and `/subject_map_pose` when relocalization is live).

## Code Map

- `src/pawtrack/skill_container.py` — DimOS skill container
  (`PawTrackSkillContainer`): `track_subject`, `tracking_status`,
  `stop_tracking`, plus the ground-pose raycast. Perception only.
- `src/pawtrack/track_state.py` — pure metrics, ground-contact pixel, and state
  machine.
- `src/pawtrack/motion_fallback.py` — pure frame-diff re-seed (off by default;
  the camera moves with the dog).
- `src/pawtrack/ground_raycast.py` — pure pixel-to-floor raycast.
- `src/pawtrack/image_source.py` — file/video frame source for no-robot runs.
- `src/pawtrack/system_prompt.py` — tells the agent to find and track the
  subject.
- `scripts/run_pawtrack.py` — packages the tracker into an off-robot
  (`--source FILE` / `--camera`, with the Rerun viewer) or `--robot` blueprint.

The earlier strike / body-charge path was removed: the Go2 has no usable kick
(sport-mode animations rear up rather than strike, and the Air has no low-level
joint control), so the demo pivoted to the greeter. The motion layer will be
built fresh against the perception contract (`/subject_status`, `/debug_image`,
`/subject_world_pose`).

## Greeter Motion Layer (built — off-robot)

Implemented and unit-tested off-robot; composes as the `greeter-agentic`
blueprint. On-robot tuning still pending (standoff / approach speed / forget
window need a real run). A dedicated autonomous container, not agent-in-the-loop.
The loop:

> patrol (wander) → detect person(s)-on-chair → pick a **random** one not greeted
> in the last `revisit_forget_s` → stop patrol → approach → wave once (any
> facing) → mark visited → resume patrol. If the subject is lost, or nobody
> greet-eligible is in view, resume / stay on patrol.

**Reuse DimOS for the wander (do not hand-roll it).** DimOS already ships the
roam: `dimos.navigation.patrolling.PatrollingModule` drives map- and
obstacle-aware patrol goals from a router (`random` / `coverage` / `frontier`)
and exposes `start_patrol()` / `stop_patrol()` / `is_patrolling()` via a
`PatrollingModuleSpec`. `dimos/agents/skills/person_follow.py` is the reference
pattern: it injects that spec and calls `stop_patrol()` when it engages a person.
It is bundled in the `unitree_go2` "smart" stack, so the spatial blueprint our
agent already uses provides it. (Approach uses the geometric ground distance
from the raycast + bbox centering via `approach_geometry.approach_velocity`, not
a monocular width model -- a person on a chair is wider than the person-shoulder
width `VisualServoing2D` assumes, which would stop the approach short.)

- **Pure logic (no DimOS, unit-tested):**
  - `greeter_state` — WANDER → APPROACH → GREET → COOLDOWN, with a
    `target_acquired` trigger out of WANDER and a lost-subject fallback. The
    container keys effects off the `entered` edges (approach → stop patrol +
    track; greet → fire the greeting once; cooldown → mark visited; wander →
    resume patrol). Greet is gated on distance only (a safe standoff), not
    facing.
  - `visited_registry` — remembers greeted **floor positions + times** and picks
    a random candidate not greeted within `forget_after_s` (`select_target`);
    identity is positional (a chair stays put). The forget window means the same
    person is re-greeted only after it elapses, not never.
  - `approach_geometry` — standoff hold + centering yaw for the approach drive
    (`approach_velocity`, `hold_distance_vx`, `centering_yaw`).
- **Multi-target acquisition (extend VLM + EdgeTAM, no new detector):** each
  wander scan tick, a multi-box VLM query returns *all* "person sitting on a
  chair" boxes (the same `vl_model.query` as the single-box grounding, just a
  prompt that asks for a list). EdgeTAM is seeded with one `obj_id` per box --
  it is natively multi-object (`propagate_in_video` returns every `obj_id` each
  frame, each `Detection2DSeg` tagged with its id), so several seated people are
  tracked at once with stable identity.
- **Glue:** a greeter container that injects `PatrollingModuleSpec`; on each
  wander tick it acquires + tracks the candidates above, raycasts each tracked
  mask to a floor position (`Candidate` carries its `obj_id`), and
  `select_target` picks a random one not greeted recently. It then `stop_patrol`s,
  approaches that `obj_id`, waves once at the standoff, marks it visited, and
  resumes the patrol. Greeting halts first, runs `Hello` (1016), then recovers
  with `RecoveryStand` (1006) + `BalanceStand` (1002) so it can move again.
- **Greet on arrival, any facing (done):** as soon as the dog reaches the safe
  standoff it waves once -- there is no orbit-to-front step. An earlier design
  circled until the subject **faced the camera** (`YoloPersonDetector` keypoints
  → `faces_camera`), but desk-seated people (legs occluded, face out of the dog's
  low frame) often never resolved as "facing", so the dog orbited until the
  engage timeout and never waved. Dropping the facing gate removed the orbit
  phase, `facing.py`, the pose detector, and the orbit geometry. Obstacle
  avoidance stays ON for a polite standoff.

## Verification

```bash
source ~/dimos-env/bin/activate && cd ~/dim-hackathon
pytest
PYTHONPATH=src python scripts/run_pawtrack.py
```

Expected off-robot tools:

```text
track_subject
tracking_status
stop_tracking
```

## Pending

Built and unit-tested off-robot (108 tests): pure logic, the `GreeterSkillContainer`
loop (`start_greeting` / `stop_greeting` / `greeter_status` / `wave_hello`), the
agent-wrapped `greeter-agentic` blueprint, and the MuJoCo sim entrypoints. Open:

**Agent / LLM:**
- Run the gated agent E2E test with a real LLM to confirm the agent selects the
  greeting tools from a plain prompt: `PAWTRACK_LLM_E2E=1 <creds> pytest
  tests/test_greeter_agent_e2e.py` (it asserts `start_greeting` then
  `stop_greeting` are called). Skipped by default (needs creds + an MCP/LCM bus).

**On-robot validation (needs the dog):**
- Run the full closed loop end-to-end on the Go2.
- Confirm `wave_hello` halts, plays `Hello`, and the `RecoveryStand` +
  `BalanceStand` recovery restores cmd_vel afterward.
- Confirm the approach reaches the greet window and the dog waves on arrival from
  whatever angle it ends up at (no orbit).
- Tune: `standoff_m`, `min_safe_distance_m`, `standoff_deadband_m`,
  `scan_interval_s`, `engage_timeout_s`, greet/cooldown durations, and
  `revisit_forget_s` (how soon the same person is re-greeted).

**Perception:**
- Validate the multi-box VLM prompt returns several "person on a chair" boxes.
- EdgeTAM identity hold when several people are close together.

**Simulation:**
- Run `scripts/run_greeter_sim.sh` on the GPU box (sim person stands -> target
  "a person"; `Hello` is a no-op in sim). Watch VRAM: EdgeTAM + the spatial stack
  + sim render share 8 GiB (the YOLO-pose detector is no longer loaded).

**Deferred / nice-to-have:**
- Trim unused models (moondream, whisper) to free VRAM (`detection_model=qwen`,
  or a lighter blueprint than `unitree_go2_spatial`).
- Speak "hi" (SpeakSkill, now in the stack) and/or LED on greet.
- Advanced planner: replace the reactive `_approach_twist` seam with a path
  planner driven by `/subject_world_pose`.
- Patrol router is hardcoded to `coverage`; subclass for `random` if wanted.

**Teammate seam:**
- Feed `/subject_status` and `/subject_world_pose` to a teammate's planner / point
  cloud.
