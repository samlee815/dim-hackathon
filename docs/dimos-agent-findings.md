# DimOS Agent Notes

These notes summarize the DimOS agent pieces PawTrack uses. The current
implementation packages the **perception stage** (find + track a described
subject) as DimOS skills; the wander / approach / greet motion is a
separate follow-up layer.

## Agent Shape

DimOS agents are composed from:

- `McpServer` — exposes `@skill` methods as MCP tools.
- `McpClient` — LangGraph LLM client that discovers and calls tools.
- Skill containers — DimOS `Module` classes with `@skill` methods.

On the robot, PawTrack runs as the registered `pawtrack-agentic` blueprint
(`dimos run pawtrack-agentic --robot-ip <ip>`). That blueprint rebuilds the
stock `unitree-go2-agentic` parts with a PawTrack-prompted `McpClient` and adds
the skill container; its source lives in the dimos tree (the registry only scans
there), with a version-controlled copy in `integration/`. See `SETUP.md`.

```python
# pawtrack-agentic (dimos tree): stock parts + our prompt + our container
autoconnect(
    unitree_go2_spatial,
    McpServer.blueprint(),
    McpClient.blueprint(model=model, system_prompt=PAWTRACK_PROMPT),
    _common_agentic,
    PawTrackSkillContainer.blueprint(),
)
```

Off-robot, the standalone launcher (`scripts/run_pawtrack.py`) uses
`McpServer.blueprint()` + the container + an `McpClient` with the same prompt.
This is enough to inspect tool discovery and call tools directly.

## Current Skills

`PawTrackSkillContainer` (one container, `skill_container.py`) exposes the
perception skills:

```python
@skill
def track_subject(description: str = "a person sitting on a chair", ...) -> str:
    """Start tracking the subject matching a visual description."""

@skill
def tracking_status() -> str:
    """Return the latest tracked-subject bbox, size, and centering metrics."""

@skill
def stop_tracking() -> str:
    """Stop tracking the current subject."""
```

The container is **perception only** — it never drives the robot. It works on
ANY description (the default is "a person sitting on a chair"), so the agent
should never refuse a request based on what the subject is. The docstrings are
important: DimOS uses them as tool descriptions for the LLM.

## Runtime Streams

The tracker consumes:

- `/color_image#sensor_msgs.Image`

The tracker publishes:

- `/subject_status` -- JSON diagnostic stream: tracking state, bbox, center
  pixel, image-center error, bbox area, optional mask area, and last-seen age.
- `/debug_image` -- the annotated camera frame. Because it is an `Image`, the
  Rerun bridge logs it automatically: a green box + the description while
  tracking, a red "searching" label while lost. This is the at-a-glance debug
  view.
- `/subject_world_pose` -- the subject's floor position in the live `world`
  frame, from raycasting the bbox bottom-center.
- `/subject_map_pose` -- the same point in the prebuilt `map` frame, published
  only when relocalization is running.

Statuses: `idle`, `acquiring`, `tracking`, `lost`, `stale` (no fix past a
timeout), `stopped`, `error` (loud loop failure). A `lost`/`stale`/`error`
snapshot keeps the last good bbox + `last_seen_age_s`. Publishing is
deduplicated: every `tracking` frame publishes, the rest only on a real change,
so a long "lost" stretch does not spam identical messages.

Robustness (in the pure `track_state` + `motion_fallback`): a tracked frame that
jumps too far or changes area too abruptly versus the last good frame is rejected
as drift; on a miss the monitor can re-seed the tracker from frame motion (cheap,
no VLM) before escalating to a VLM reacquire — but that fallback is **off by
default**, since it re-seeds on the largest moving blob and the camera itself
moves with the dog (it would lock onto the background). The worker loop catches
and surfaces any exception as an `error` status instead of dying.

## Perception Choice

The perception stage uses open-vocabulary selection because the user can
describe any subject ("a person sitting on a chair", "the person in the red
shirt"):

1. VLM acquisition: turn the user's description into an initial bbox.
2. EdgeTAM tracking: follow that selected subject frame-to-frame.

This avoids being limited to a fixed detector's classes.

## Model Selection

The agent model is configured with `PAWTRACK_MODEL`.

Examples:

```bash
PAWTRACK_MODEL=openai:deepseek-chat
PAWTRACK_MODEL=openai:gpt-5.1
```

For DeepSeek, also set:

```bash
OPENAI_BASE_URL=https://api.deepseek.com
```

DeepSeek is text-only; the prompt forbids image-returning tools (e.g. `observe`)
so the agent never sends an image the model would reject.

## Useful CLIs

```bash
dimos mcp list-tools
dimos mcp call track_subject --arg description="a person sitting on a chair"
dimos mcp call tracking_status
dimos mcp call stop_tracking
```

Use `humancli` for interactive chat with the agent and `agentspy` to watch tool
calls and replies.

## Testing end to end without a robot

The launcher has two no-robot modes that run the full VLM-acquire + EdgeTAM
pipeline and the Rerun viewer:

```bash
# Fully hardware-free: feed a photo or clip of the subject.
PYTHONPATH=src python scripts/run_pawtrack.py --source room.mp4

# Live: use a laptop webcam.
PYTHONPATH=src python scripts/run_pawtrack.py --camera
```

Then describe the subject from the DimOS CLI -- either straight to the skill
(no LLM needed) or through the agent:

```bash
dimos mcp call track_subject --arg description="a person sitting on a chair"
dimos agent-send "track the person sitting on the chair"
```

Watch `/debug_image` (the annotated frame) and `/subject_status` in Rerun. This
exercises identification + tracking with neither a robot nor a real venue.

## Integration Boundary

The motion layer (wander / approach / greet) consumes the perception
output: `/subject_status` for centering and size, `/subject_world_pose` for the
floor position. Perception lives in `PawTrackSkillContainer`; its pure logic is
in `track_state` (metrics + state machine) and `ground_raycast` (floor
projection). The motion layer is a separate container, since perception and
motion are independent concerns.
