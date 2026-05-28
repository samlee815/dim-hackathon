# Unitree Go2 Map Scan With DimOS

This is the workflow for connecting to a Unitree Go2, letting DimOS drive an
automatic frontier-exploration scan, recording the sensor stream, and exporting
a reusable map.

## What To Run

Use `unitree-go2-memory` for map recording. Plain `unitree-go2` builds a live
map in Rerun, but it does not save the SQLite dataset that `export-premap`
needs.

`unitree-go2-memory` includes the same navigation stack as `unitree-go2` plus a
recorder for:

- `lidar`
- `odom`
- `color_image`

The automatic scan is handled by DimOS's native
`WavefrontFrontierExplorer`. It picks frontier goals from the current costmap
and sends them to the planner until you stop it or it runs out of useful
frontiers.

## 1. Connect To The Robot

Activate the project environment:

```bash
source ~/dimos-env/bin/activate
cd ~/dim-hackathon
```

If you do not know the robot IP, discover it:

```bash
dimos go2tool discover
```

Export it:

```bash
export ROBOT_IP=<dog_ip>
ping "$ROBOT_IP"
```

Use a wired connection when possible. For mapping, low latency and stable packet
delivery matter more than peak bandwidth.

## 2. Start DimOS With Recording Enabled

Create a local data directory and run the Go2 memory blueprint:

```bash
mkdir -p data/maps

dimos --robot-ip "$ROBOT_IP" \
  --rerun-open web \
  run unitree-go2-memory \
  -o go2memory.db_path="$PWD/data/maps/go2_scan.db"
```

Rerun should open and show the live robot, camera, LiDAR-derived map, costmap,
and path/debug streams. Leave this process running while the robot scans.

## 3. Start Automatic Frontier Exploration

In another terminal:

```bash
source ~/dimos-env/bin/activate
cd ~/dim-hackathon
```

Start the scan by sending the explorer's command topic:

```bash
python scripts/go2_exploration.py start
```

The robot should begin picking frontier goals and driving itself. Watch Rerun:

- `global_map` / `global_map_pgo`: accumulated map
- `global_costmap`: known free/occupied/unknown space
- `path`: current planned route
- `goal_request`: frontier goal selected by the explorer

The browser UI may also expose an autonomous-exploration toggle, but the LCM
command above is the reproducible CLI path.

## 4. Stop The Scan

Stop exploration before exporting:

```bash
python scripts/go2_exploration.py stop
```

Then stop the DimOS run with `Ctrl-C` in the original terminal. If it was
daemonized, use:

```bash
dimos stop
```

## 5. Export The Map

Export a two-pass relocalization premap from the recorded SQLite dataset:

```bash
dimos export-premap \
  "$PWD/data/maps/go2_scan.db" \
  -o "$PWD/data/maps/go2_scan_twopass_map.pc2.lcm" \
  --voxel-size 0.05 \
  --device CUDA:0
```

For a quick test export from only the first two minutes:

```bash
dimos export-premap \
  "$PWD/data/maps/go2_scan.db" \
  -o "$PWD/data/maps/go2_scan_2min_twopass_map.pc2.lcm" \
  --duration 120 \
  --voxel-size 0.05 \
  --device CUDA:0
```

## 6. Replay The Recording

Replay the scan without the robot:

```bash
dimos --replay \
  --replay-db "$PWD/data/maps/go2_scan.db" \
  --rerun-open web \
  run unitree-go2
```

Use this before trusting the exported map. If replay shows bad drift, missing
sections, or sparse geometry, rescan more slowly and include loop closures.

## Scanning Tips

- Start in an open, easy-to-localize spot.
- Let the robot revisit the start or previously mapped corridors. Loop closures
  improve the two-pass map.
- Keep people, chairs, doors, and balls out of the scan area when possible.
- Avoid glass walls, mirrors, and featureless long corridors.
- Stop the scan if the robot repeatedly chooses unsafe or unreachable frontier
  goals; manually reposition, then restart exploration.
- For hackathon use, scan only the field/room you need. Smaller maps export and
  relocalize faster.

## Troubleshooting

If the robot only opens Rerun and waits, exploration has not been started yet.
Run the `/explore_cmd` command from step 3.

If the export fails because there is no `lidar` stream, the run was probably
started with `unitree-go2` instead of `unitree-go2-memory`, or the recorder path
was wrong.

If the robot drives but no map appears, check the live topics:

```bash
dimos lcmspy
```

Look for traffic on camera, odom, LiDAR, costmap, path, and goal topics. Then
check logs:

```bash
dimos log -f
```
