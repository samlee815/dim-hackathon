# PawTrack Design

PawTrack is a DimOS / Unitree Go2 hackathon project. The demo target is a
**greeter dog**: the robot wanders a room, finds a person sitting on a chair,
walks over to a polite standoff, waves "hi" (whichever way they face), then
resumes wandering -- re-greeting the same person only after a forget window.

The repo has two layers, both implemented:

- **Perception** (`PawTrackSkillContainer`, the `pawtrack-agentic` blueprint):
  find a user-described subject (default "a person sitting on a chair"), track
  it, report image-space metrics, and publish its floor position. Usable on its
  own via `track_subject`.
- **Greeter** (`GreeterSkillContainer`, the `greeter-agentic` blueprint): the
  autonomous wander -> identify -> select -> navigate -> greet loop, built on top
  of the same perception pieces. Started with `start_greeting`.

## Current Architecture

```text
user / agent: "track the person sitting on the chair"
        |
        v
McpClient
        |
        v
PawTrackSkillContainer  (perception only -- never drives the robot)
  ACQUIRE: VLM localizes the described subject in the latest RGB frame
  TRACK:   EdgeTAM follows that selected subject frame-to-frame
  REPORT:  /subject_status JSON + tracking_status()
  LOCATE:  raycast the bbox bottom-center to the floor ->
           /subject_world_pose (+ /subject_map_pose with relocalization)
        |
        v
greeter loop (built): wander -> approach -> greet (wave, any facing)
```

## Why VLM + EdgeTAM

YOLO is good for fixed detector classes. The need here is open-vocabulary
selection: the user can say "a person sitting on a chair", "the person in the
red shirt", or any other object. That is a semantic selection problem, so the
perception stage uses a VLM once to find the described subject. EdgeTAM then
tracks the selected subject without requiring it to be a known detector class.

## Perception Output

The tracker reports:

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

This is published as JSON on `/subject_status` and returned by
`tracking_status()`.

## Subject Position + Range

The greeter ranges the subject two ways, preferring the first:

1. **Lidar** (`greeter_container._locate_lidar`) -- DimOS
   `Detection3DPC.from_2d` projects the world-frame lidar cloud into the camera,
   keeps the returns inside the tracked bbox, and takes their cleaned centroid.
   This reads the subject's real distance off their body, so it does not assume
   the feet rest on a flat floor -- the mono raycast's failure mode that put a
   seated person metres from their true range and made the greeter wave across
   the room.
2. **Ground raycast** (fallback when no lidar is wired or no points land in the
   box) -- back-projects the subject's **ground-contact pixel** (bbox
   bottom-center) onto the floor plane using the camera intrinsics and the
   `world <- camera_optical` transform. The bbox bottom (feet / seat / chair
   base), not the center, avoids a torso-height bias for a seated person.

For a planner that wants an absolute position, the tracker also publishes:

- `/subject_world_pose` -- always-on, in the live odometry `world` frame.
- `/subject_map_pose` -- in the stable prebuilt `map` frame, published only when
  `RelocalizationModule` is running. Tracking is never blocked on missing TF.

## Active Skills

- `track_subject(description="a person sitting on a chair", ...)`
- `tracking_status()`
- `stop_tracking()`

This container is perception only. It does not plan, walk, orbit, or greet;
those belong to the greeter loop (below).

## Greeter Loop (implemented)

`GreeterSkillContainer` runs the autonomous loop, each step a separable seam:

- **wander** -> DimOS `PatrollingModule` (via the injected
  `PatrollingModuleSpec`). The patrol usually has no goal when the dog faces
  something it cannot act on -- every person in view already greeted, or every
  detection rejected by the filters below (too far, or on a mapped wall, e.g. a
  glass reflection) -- and the thing ahead is an obstacle, so the dog would
  freeze staring at it. In that case the greeter rotates in place
  (`_rescan_twist`) to look elsewhere instead of freezing -- bounded by
  `wander_rescan_max_s`, after which it hands back to the patrol from the new
  heading. Rotating only changes heading, though; when the patrol stays
  goal-less (coverage exhausted) the dog can still sit in one spot. So a
  **free-explore fallback** (`_roam_tick`) watches odom and, when the dog has
  not moved (> `stall_move_threshold_m`) for `stall_timeout_s`, takes over and
  roams into new space -- a brief in-place reorient, then forward at
  `roam_forward_mps` -- until it has escaped `roam_escape_distance_m` (or
  `roam_max_s` elapses), then hands back so the patrol can replan from the new
  location. It needs odom (it never drives blind without it) and its forward
  speed stays above the sim RL translation deadband.
- **identify** -> multi-box VLM query (`identify.detect_all`); each box is
  located on the floor and two sanity filters drop bad candidates before one can
  be selected:
  - *Distance bound* -- a box near the image horizon raycasts to a near-infinite
    floor point, so a candidate ranged beyond `max_subject_distance_m`
    (default 30 m) is dropped instead of becoming a phantom the dog walks toward
    across empty space.
  - *Mapped-wall filter* -- a candidate landing on (or within
    `obstacle_clearance_m`, default 0.3 m, of) a mapped wall in the navigation
    costmap is dropped (`occupancy.is_near_obstacle`, fed by the `global_costmap`
    port). A person reflected in a glass wall is detected by the VLM and ranged
    by the lidar at the glass *surface*, so without this the dog walks over and
    waves at the reflection; a real subject stands in free space. This fails open
    (no costmap -> no filtering), so a fresh, unmapped run greets as before.
- **select** -> a random subject (by floor position) not greeted within the last
  `revisit_forget_s` seconds (`visited_registry.select_target`). The forget
  window (default 60 s) is the relaxation of the old "greet each person once per
  run" rule, so the demo keeps greeting people instead of going quiet.
- **navigate** -> drive to a ~2 m standoff using the subject's range +
  bbox centering (`approach_geometry.approach_velocity`) -- the seam a real
  planner replaces. Range comes from the lidar (`_locate_lidar`, below),
  falling back to the ground raycast when no lidar is wired. The standoff is 2 m
  (greet window `[0.6, 2.3]` m) because the Go2's onboard obstacle avoidance
  halts the dog ~2 m from a person, so a tighter target left it stuck just
  outside greeting range. If the tracker drops the subject mid-approach, the dog
  does **not** abandon the engagement: it dead-reckons (odom) to the subject's
  last-known floor position and re-detects there (`_deadreckon_tick`). A match
  near that spot re-acquires it (so a subject that *shifted* is still greeted);
  none means it moved off (or was a bad fix) and the subject is skipped. While
  the tracker holds the subject its last-known position is continuously
  refreshed, so a slowly moving subject is chased. A brief in-place re-find
  (`_search_twist`) is the fallback when no odom is wired.
- **greet** -> as soon as the dog reaches the safe standoff it halts and waves
  once with `Hello` (1016) -- **whichever way the person faces**. There is no
  orbit-to-front step: any seated person reached at a reasonable distance gets a
  wave. (An earlier design circled until the subject faced the camera, but
  desk-seated people often never resolved as "facing", so the dog orbited until
  the engage timeout and never waved.) **After the wave**, a one-shot VLM check
  (`identify.detect_facing`) asks whether the **target** faces the camera --
  parameterised by the subject `description`, so it reads a person's facing on
  the robot and a chair's orientation (backrest = "facing away") in sim. If it
  faces the camera the dog rewards it with a `FingerHeart` (1036), otherwise it
  just moves on. The
  check runs while the dog is stopped, costs no GPU (the VL model is remote), and
  an ambiguous answer defaults to "no heart" so an uncertain read does not
  trigger the rear-up gesture. After the gesture(s) it recovers with
  `RecoveryStand` (1006) + `BalanceStand` (1002) via the GO2 connection so it can
  walk again, marks the subject visited (with the greet time), and resumes the
  patrol.

Phase decisions live in the pure `greeter_state.GreeterMachine` (wander ->
approach -> greet -> cooldown). The Go2 has no usable kick and no low-level joint
control on the Air, so the loop uses only what the platform does well: `cmd_vel`
walking, sport animations, obstacle avoidance. Skills: `start_greeting`
(optional `target` overrides the subject for that run -- e.g. "a chair" in sim,
"a person sitting on a chair" on the robot), `stop_greeting`, `greeter_status`,
`wave_hello`.

See [`../plan.md`](../plan.md) for the build plan and
[`dimos-agent-findings.md`](./dimos-agent-findings.md) for DimOS wiring notes.
