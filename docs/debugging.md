# PawDribble — Debugging and Monitoring

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
PYTHONPATH=src PAWDRIBBLE_MODEL=openai:deepseek-chat python scripts/run_pawdribble.py
```

Robot stack:

```bash
PYTHONPATH=src PAWDRIBBLE_MODEL=openai:deepseek-chat \
  python scripts/run_pawdribble.py --robot
```

Teammate/reference dribble tools can be exposed explicitly:

```bash
PYTHONPATH=src python scripts/run_pawdribble.py --include-reference-dribble
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

High-value topics for PawDribble:

```text
/human_input
/agent
/agent_idle
/color_image#sensor_msgs.Image
/ball_status
```

If a topic is missing, check whether the producing module is in the blueprint
and whether startup logs show a transport binding for that stream.

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

Call PawDribble skills directly, bypassing the LLM:

```bash
dimos mcp call track_ball --arg description="the red ball"
dimos mcp call ball_tracking_status
dimos mcp call stop_tracking_ball
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
Transport module=BallMonitorSkillContainer name=color_image topic=/color_image#sensor_msgs.Image
Transport module=BallMonitorSkillContainer name=ball_status topic=/ball_status
```

Those lines confirm that `autoconnect` wired the expected LCM streams.

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

For PawDribble, verify in order:

1. Camera frames are live.
2. `track_ball(description)` can acquire the described ball.
3. `/ball_status` publishes `tracking` snapshots.
4. `ball_tracking_status` returns bbox, center, and size metrics.
5. Lost tracking is reported as `lost`, not as stale `tracking`.

## Common Failure Split

Use this split when debugging:

```text
No topic in lcmspy       -> module not running or stream not wired
Topic alive, no tool     -> MCP server/module discovery issue
Tool works directly      -> agent prompt/model/tool-routing issue
No image available       -> camera stream is missing or not autoconnected
Cannot find ball         -> VLM acquisition / prompt / current view issue
Tracking starts then lost -> EdgeTAM drift, occlusion, or fast ball motion
```
