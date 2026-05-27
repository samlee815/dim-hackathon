# PawDribble Design

PawDribble is a DimOS / Unitree Go2 hackathon project whose full demo target is:
identify a user-described ball, monitor it, let a teammate-owned planner move
the robot behind it, then body-charge through the ball toward a goal.

This document is intentionally explicit about ownership: this repo's active
slice is **ball monitoring plus the final body-charge kick**. It does not
replace the teammate's planner and it does not approach or aim the robot.

## Current Architecture

```text
user: "track the red ball"
        |
        v
McpClient
        |
        v
PawDribbleSkillContainer
  ACQUIRE: VLM localizes the described ball in the latest RGB frame
  TRACK:   EdgeTAM follows that selected object frame-to-frame
  REPORT:  /ball_status JSON + ball_tracking_status()
        |
        v
teammate planner / motion layer positions the robot behind the ball
        |
        v
PawDribbleSkillContainer
  KICK: short ramped cmd_vel body-charge, then zero twist
```

## Why VLM + EdgeTAM

YOLO is good for "any sports ball". The product need here is different: the
user can say "the red ball", "the tennis ball", or "the blue striped ball".
That is a semantic selection problem, so the monitor uses a VLM once to find the
described ball. EdgeTAM then tracks the selected object without requiring the
object to be a known detector class.

## Monitoring Output

The monitor reports:

- `status`: `idle`, `acquiring`, `tracking`, `lost`, `stale`, `stopped`, or
  `error` (`stale` = no fix past a timeout; `error` = loud loop failure)
- `description`
- `bbox`
- `center_px`
- `image_error_x`, `image_error_y`
- `width_px`, `height_px`
- `area_px`, `area_ratio`
- `mask_area_px`, `mask_area_ratio` when a mask exists
- `last_seen_age_s`

The current implementation publishes this as JSON on `/ball_status` and returns
the latest snapshot from `ball_tracking_status()`.

## Active Skills

- `track_ball(description)`
- `ball_tracking_status()`
- `stop_tracking_ball()`
- `kick_ball(speed_mps=None, duration_s=None)`
- `stop_kick()`

These tools monitor the ball and perform the final straight body-charge. They
do not plan, walk to the ball, or aim the robot.

## Teammate Integration Contract

The teammate-owned planner/control layer should treat the monitor as an input
source. It drives and aims the robot behind the ball, then calls `kick_ball`.
The final integration can choose whether to consume:

- image-space metrics for visual servoing,
- a future 3D pose stream,
- or both.

The earlier YOLO-detector and dribble planner/control modules
(`ball_detector.py`, `dribble_planner.py`, `charge_control.py`,
`skill_container.py`, `ball_settle.py`, `geometry.py`) have been removed -- they
were built on a world-pose `ball_position` contract that the image-space monitor
does not produce. The current kick is only the final forward burst; approach and
navigation should consume `/ball_status` / `/debug_image` and be written fresh.

See [`../plan.md`](../plan.md) for the current build plan and
[`dimos-agent-findings.md`](./dimos-agent-findings.md) for DimOS wiring notes.
