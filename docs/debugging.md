# PawTrack — Debugging and Monitoring

Native DimOS debugging is mostly LCM stream monitoring plus agent/MCP tooling.
Run everything from the project venv:

```bash
export REPO_ROOT="$(pwd)"
export DIMOS_VENV="${DIMOS_VENV:-$REPO_ROOT/.venv}"
source "$DIMOS_VENV/bin/activate"
cd "$REPO_ROOT"
```

## Start a Run

Off-robot agent/container:

```bash
PYTHONPATH=src PAWTRACK_MODEL=openai:deepseek-chat python scripts/run_pawtrack.py
```

Robot stack (native `dimos run`; see `SETUP.md` for the one-time blueprint
registration):

```bash
PYTHONPATH=src PAWTRACK_MODEL=openai:deepseek-chat \
  dimos run pawtrack-agentic --robot-ip <dog_ip>
```

The autonomous greeter demo is its own blueprint (no LLM needed -- trigger it
over MCP):

```bash
PYTHONPATH=src dimos run greeter-agentic --robot-ip <dog_ip>
dimos mcp call start_greeting    # wander -> find subject -> approach -> wave
dimos mcp call start_greeting --arg target="a chair"   # override the subject
dimos mcp call greeter_status    # current phase JSON (also on /greeter_phase)
dimos mcp call stop_greeting
```

The standalone `python scripts/run_pawtrack.py --robot` still works as a quick
fallback, but uses the agent's default prompt rather than the PawTrack one.

Run the full pipeline without a robot (feed a file or a webcam) and watch
`/debug_image` + `/subject_status` in Rerun:

```bash
PYTHONPATH=src python scripts/run_pawtrack.py --source room.mp4
PYTHONPATH=src python scripts/run_pawtrack.py --camera
```

Replay the stock Go2 stack for sensor/nav stream debugging:

```bash
dimos --replay run unitree-go2
```

## LCM Stream Monitor

Use DimOS's native LCM traffic TUI:

```bash
lcmspy
```

or:

```bash
dimos lcmspy
```

`lcmspy` subscribes to all LCM topics and shows topic frequency, bandwidth, and
total traffic. Quit with `q` or `Ctrl-C`.

High-value topics for PawTrack:

```text
/human_input
/agent
/agent_idle
/color_image#sensor_msgs.Image
/subject_status
```

If a topic is missing, check whether the producing module is in the blueprint
and whether startup logs show a transport binding for that stream.

## Reading `/subject_status` Payloads

`lcmspy` shows that `/subject_status` is *alive* (frequency, bandwidth) but not
its contents. It is a pickled-string topic (`subject_status: Out[str]`), and
there is no `dimos topic echo` to dump it. Two ways to read the JSON snapshot
itself:

- On demand (easiest) -- ask the skill, which returns the same snapshot string:

  ```bash
  dimos mcp call tracking_status
  ```

- Tail the live stream -- subscribe to the LCM topic from the venv:

  ```python
  import time
  from dimos.core.transport import pLCMTransport

  pLCMTransport("/subject_status").subscribe(print)  # prints each status JSON
  time.sleep(60)                                      # keep the subscriber alive
  ```

`/debug_image` is an `Image` topic, so the Rerun bridge renders it
automatically -- watch it there rather than over raw LCM.

## Interactive Agent CLI

Use `humancli` for an interactive chat session with the running agent:

```bash
humancli
```

Use `agentspy` in another terminal to watch the agent's replies and tool calls:

```bash
agentspy
```

`dimos agent-send "..."` is only a one-shot fire-and-forget input; it does not
print the reply. Use `humancli`, `agentspy`, the web UI, or logs for interactive
debugging.

## MCP Tool Debugging

Check server health and loaded modules:

```bash
dimos mcp status
dimos mcp modules
dimos mcp list-tools
```

Call PawTrack skills directly, bypassing the LLM:

```bash
dimos mcp call track_subject --arg description="a person sitting on a chair"
dimos mcp call tracking_status
dimos mcp call stop_tracking
```

Direct MCP calls are the fastest way to separate skill/container bugs from
agent prompt or model-routing bugs.

## Logs

Foreground runs print logs directly. For daemonized DimOS runs:

```bash
dimos status
dimos log -f
```

Look for transport lines such as:

```text
Transport module=PawTrackSkillContainer name=color_image topic=/color_image#sensor_msgs.Image
Transport module=PawTrackSkillContainer name=subject_status topic=/subject_status
Transport module=PawTrackSkillContainer name=subject_world_pose topic=/subject_world_pose#geometry_msgs.PoseStamped
Transport module=PawTrackSkillContainer name=subject_map_pose topic=/subject_map_pose#geometry_msgs.PoseStamped
```

Those lines confirm that `autoconnect` wired the expected LCM streams.
`/subject_world_pose` is the planner's primary live-odometry pose.
`/subject_map_pose` appears only when relocalization/prebuilt-map TF is
available; use it for map-stable planning, and fall back to
`/subject_world_pose` otherwise.

## Rerun Viewer

Use Rerun for spatial debugging: camera, pointcloud, odom, maps, detections,
and command streams on one timeline.

```bash
dimos --replay --viewer rerun-web run unitree-go2
```

Live robot:

```bash
dimos --viewer rerun-web run unitree-go2 --robot-ip <dog-ip>
```

For PawTrack, verify in order:

1. Camera frames are live.
2. `track_subject(description)` can acquire the described subject.
3. `/subject_status` reports `tracking` snapshots (read via `tracking_status`
   or the LCM subscriber above; only `/debug_image` renders in Rerun).
4. `tracking_status` returns bbox, center, and size metrics.
5. Lost tracking is reported as `lost`, not as stale `tracking`.

## Common Failure Split

Use this split when debugging:

```text
No topic in lcmspy          -> module not running or stream not wired
Topic alive, no tool        -> MCP server/module discovery issue
Tool works directly         -> agent prompt/model/tool-routing issue
No image available          -> camera stream is missing or not autoconnected
Cannot find subject         -> VLM acquisition / prompt / current view issue
Tracking starts then lost   -> EdgeTAM drift, occlusion, or fast motion
```

## Debugging the Greeter

The greeter loop exposes three traces plus narrated logs. Watch them together:

| What | Stream / source | Tells you |
|---|---|---|
| Phase + **why** | `greeter_status` skill, `/greeter_phase` (JSON str) | the current phase and the perception driving it |
| Annotated camera | `/debug_image` (renders in Rerun) | bbox + a `phase  d=<m>` overlay; in wander, `wander: N cand` |
| Where it thinks the subject is | `/subject_world_pose` (PoseStamped) | a 3D marker in Rerun |
| Narration | `dimos log -f` | one INFO line per phase change + each engagement |

Read the phase trace on demand (it is a pickled-string topic, like `subject_status`):

```bash
dimos mcp call greeter_status
# -> {"state":"approach","message":"Approaching the subject.",
#     "entered":false,"reason":null,"patrolling":false,"subject_visible":true,
#     "distance_m":1.62,"subject_xy":[2.48,0.01],"greeted":0}
```

The logs narrate the whole run:

```text
greeter -> approach
greeter: engaging subject at (2.48, 0.01) of 1 candidate(s)
greeter -> greet
greeter -> cooldown
greeter -> wander
```

### Why isn't it ...?

Read the `greeter_status` trace fields:

```text
stuck in wander, greeted=0      -> no subject selected: subject_visible=false or
                                   distance/locate failing. Watch /debug_image
                                   "wander: N cand" -- N=0 means the VLM found
                                   none (prompt / scene / `description`).
approach->wander reason=stuck,   -> can't reach standoff. Onboard obstacle
  distance stalls ~2 m              avoidance halts the dog ~2 m from a person;
                                   standoff is 2 m to greet from there. If it
                                   stalls farther out, raise standoff_m / the
                                   engage timeout, or check /subject_world_pose.
"to last seen" then wander       -> tracker dropped the subject, so the dog
  reason=lost                       dead-reckoned (odom) to its last-known spot
                                   and could not re-detect it there -> skipped
                                   (reason=lost, eligible again after
                                   revisit_forget_s). It moved off, was a bad
                                   fix, or the VLM re-detect missed it. Needs
                                   /odom; with none, it just re-finds in place
                                   (_search_twist) until the engage timeout.
approach, never greets           -> distance_m never lands in the greet window
  (distance_m never in window)      [min_safe_distance_m, standoff_m+tol]. Stuck
                                   high = can't reach the standoff (see the row
                                   above); stuck low = inside min_safe, backing
                                   off. Tune standoff_m / min_safe_distance_m.
re-greets the same person         -> revisit_forget_s elapsed, so a greeted
                                   subject is greet-eligible again (by design).
                                   Raise revisit_forget_s (or set it to None for
                                   once-per-run) to space greetings out more.
returns to wander, reason=stuck  -> engage_timeout_s hit; the subject was skipped
                                   (greeted count unchanged). Loosen the timeout
                                   or the step that stalled.
greet entered but no wave        -> no GO2 connection (off-robot/sim no-op), or
                                   `wave_hello` works but Hello mode rejected.
patrolling=true but not moving   -> patrol has no goal: needs odom + costmap (the
                                   spatial stack); check `dimos status` / nav.
wander frozen facing a person,   -> wedged on an already-greeted subject the
  only lidar fixes in the log      patrol can't drive past. The greeter rotates
                                   in place (`_rescan_twist`) to look elsewhere;
                                   if it isn't, check wander_rescan_* and that
                                   BalanceStand is active (cmd_vel accepted).
```

`wave_hello` (skill) runs the greeting in isolation -- the fastest way to confirm
the `Hello` + recovery (`RecoveryStand` + `BalanceStand`) path works on the
robot, separate from perception.
