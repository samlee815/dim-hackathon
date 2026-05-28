# DimOS Dev Environment

PawTrack is intended to run on native Ubuntu x86_64 with an NVIDIA GPU.
DimOS ships native extensions and LCM tooling, so a local Linux host is the
recommended development and robot runtime environment.

This document avoids user-specific paths. Set these variables once per shell:

```bash
export REPO_ROOT="$(pwd)"                  # this repository
export DIMOS_HOME="/path/to/dimos"       # upstream DimOS checkout
export DIMOS_VENV="$REPO_ROOT/.venv"   # Python virtual environment
```

Use different locations if your checkout or venv lives elsewhere.

## 1. System Dependencies

```bash
sudo apt-get update
sudo apt-get install -y \
  build-essential git curl ca-certificates pkg-config \
  python3-dev python3-venv portaudio19-dev libasound2-dev \
  libjack-jackd2-dev libgl1 libglib2.0-0 libsm6 libxext6 \
  libxrender1 libgomp1 ffmpeg git-lfs libturbojpeg0-dev
git lfs install
```

## 2. Clone DimOS

```bash
mkdir -p "$(dirname "$DIMOS_HOME")"
git clone https://github.com/dimensionalOS/dimos.git "$DIMOS_HOME"
```

Install from the editable source checkout, not the PyPI wheel. The editable
checkout includes subpackages required by the Go2 agentic stack.

## 3. Create the Venv

```bash
python3 -m venv "$DIMOS_VENV"
source "$DIMOS_VENV/bin/activate"
python -m pip install --upgrade pip
pip install -e "$DIMOS_HOME[base,unitree]"
```

Optional CUDA extras:

```bash
pip install -e "$DIMOS_HOME[base,unitree,cuda]"
```

The optional CUDA extra may pull packages with stricter PyTorch/CUDA wheel
compatibility. Install it only if the base setup is working and the extra
acceleration is needed.

## 4. Verify

```bash
source "$DIMOS_VENV/bin/activate"
cd "$REPO_ROOT"

python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
dimos list
python -c "import dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic"
pytest
```

Replay smoke test:

```bash
dimos --replay run unitree-go2
```

The first replay run may fetch Git LFS assets from the DimOS checkout.

## 4a. Greeter in Simulation (MuJoCo)

Run the greeter end-to-end in the MuJoCo office sim (it has chairs and a person
mesh; no robot needed). `scripts/run_greeter_sim.sh` sets the sim gotchas (a
display is required, no `MUJOCO_GL=egl`, proxies unset, HF models offline):

```bash
./scripts/run_greeter_sim.sh
```

Then, in a second shell, plant the person where the dog can find it and start
the loop:

```bash
PYTHONPATH=src python scripts/sim_person_pose.py --x 2.5 --y 0.0
dimos mcp call start_greeting     # wander -> approach -> face -> wave
dimos mcp call greeter_status
dimos mcp call stop_greeting
```

The sim person mesh stands rather than sits, and the scene has a bare chair (no
one seated on it), so target something the scene actually contains. Set the
default at launch with `GREETER_SUBJECT` (e.g. "a person" or "a chair"), or
override per run without restarting:

```bash
dimos mcp call start_greeting --arg target="a chair"
```

`Hello` is a no-op in sim, so the wave itself only shows on the real dog; wander,
detect, and approach are what the sim exercises. The same `start_greeting
target=...` parameter drives both: "a chair" / "a person" in sim, "a person
sitting on a chair" on the robot.

**Testing the facing -> FingerHeart logic in sim.** After the wave the greeter
runs a one-shot VLM check on the *target object* (`identify.detect_facing`,
prompt built from `target`), so a chair stands in for a person: a chair facing
the dog reads as "front" (-> FingerHeart), one showing its backrest reads as
"back" (-> no heart). Point at chairs in different orientations and watch the
decision in the log -- both `Hello` and `FingerHeart` are no-ops in sim, so the
log line is how you observe it:

```bash
dimos mcp call start_greeting --arg target="a chair"
dimos log -f | grep "faces the camera"   # e.g. 'a chair' faces the camera=False -> no heart
```

The greeter delegates wander to the DimOS coverage patrol, which needs a map to
produce goals. A fresh sim has none, so the dog sits ("No patrol goal
available"). Seed the map first -- drive the dog around (web-viz click-to-go /
teleop / frontier explore) to build the costmap -- then `start_greeting`; the
patrol then has cells to cover and roams on its own.

## 5. Daily Use

```bash
source "$DIMOS_VENV/bin/activate"
cd "$REPO_ROOT"

pytest
PYTHONPATH=src python scripts/run_pawtrack.py
PYTHONPATH=src python scripts/run_pawtrack.py --robot
```

Useful DimOS commands:

```bash
dimos list
dimos status
dimos log -f
dimos stop
dimos --replay --viewer rerun-web run unitree-go2
```

## 6. Robot Networking

Run the live robot blueprint on the same LAN or direct wired subnet as the Go2.

```bash
ip -brief addr
ping <dog_ip>
```

PawTrack ships two registered DimOS blueprints: `greeter-agentic` (the
autonomous greeter demo) and `pawtrack-agentic` (the standalone tracker). Both
register the same way -- copy the file into the DimOS tree and regenerate the
registry. For the greeter:

```bash
cp integration/greeter_agentic.py \
  "$DIMOS_HOME/dimos/robot/unitree/go2/blueprints/agentic/"
pytest -o addopts="" \
  "$DIMOS_HOME/dimos/robot/test_all_blueprints_generation.py"

PYTHONPATH=src dimos run greeter-agentic --robot-ip <dog_ip>
dimos mcp call start_greeting     # wander, find a seated person, face them, wave
dimos mcp call stop_greeting
```

The rest of this section covers `pawtrack-agentic` (the tracker), which is the
stock `unitree-go2-agentic` stack plus the PawTrack skill container and prompt.

Registering `pawtrack-agentic` is a one-time step, because `dimos run` resolves
names from a registry generated by scanning the DimOS tree. The blueprint object
must live there:

```bash
cp integration/pawtrack_agentic.py \
  "$DIMOS_HOME/dimos/robot/unitree/go2/blueprints/agentic/"

pytest -o addopts="" \
  "$DIMOS_HOME/dimos/robot/test_all_blueprints_generation.py"
```

After registration, start PawTrack on the real Go2:

```bash
source ~/dimos-env/bin/activate
cd ~/dim-hackathon

export ROBOT_IP=<dog_ip>
ping "$ROBOT_IP"

PYTHONPATH=src PAWTRACK_MODEL=openai:deepseek-chat \
  dimos --robot-ip "$ROBOT_IP" --rerun-open web run pawtrack-agentic
```

`pawtrack-agentic` publishes `subject_world_pose` in the live odometry frame. If
you also need `subject_map_pose` in a prebuilt-map frame, register the separate
relocalization blueprint instead of changing the default one:

```bash
cp integration/pawtrack_agentic_relocalization.py \
  "$DIMOS_HOME/dimos/robot/unitree/go2/blueprints/agentic/"

pytest -o addopts="" \
  "$DIMOS_HOME/dimos/robot/test_all_blueprints_generation.py"
```

Run it only when you have already exported a premap (`.pc2.lcm`):

```bash
PYTHONPATH=src PAWTRACK_MODEL=openai:deepseek-chat \
  dimos --robot-ip "$ROBOT_IP" --rerun-open web \
  run pawtrack-agentic-relocalization \
  -o relocalizationmodule.map_file=<premap_name>
```

Without `relocalizationmodule.map_file`, relocalization is disabled and
`subject_map_pose` will stay paused; `subject_world_pose` still works.

Use direct MCP calls to verify the live tools without relying on the LLM:

```bash
dimos mcp list-tools
dimos mcp call track_subject --arg description="a person sitting on a chair"
dimos mcp call tracking_status
dimos mcp call stop_tracking
```

`pawtrack` must be importable at run time: use `PYTHONPATH=src` (as above) or
`pip install -e .` from the repo. Watch Rerun for `/debug_image`,
`/subject_status`, camera, map, and navigation streams. For the most reliable
camera, LiDAR, and LCM stream behavior, prefer a wired connection when
available.

## 7. GPU Notes

- Confirm the NVIDIA driver with `nvidia-smi`.
- Confirm PyTorch GPU visibility with `torch.cuda.is_available()`.
- EdgeTAM requires CUDA.
- Local LLMs, VLMs, mapping, and tracking share VRAM; watch `nvidia-smi`.

## 8. Common Gotchas

| Symptom | Cause | Fix |
|---|---|---|
| `pyaudio` install fails: `Python.h: No such file` | missing C headers | install `python3-dev portaudio19-dev` |
| `ImportError: libGL.so.1` | missing graphics libs | install `libgl1 libglib2.0-0` and related libs above |
| `RuntimeError: Missing required tools: git-lfs` | LFS assets missing | install Git LFS and run `git lfs install` |
| `Unable to locate turbojpeg library` | libturbojpeg missing | install `libturbojpeg0-dev` |
| Rerun has no display | headless / SSH session | use `--viewer rerun-web` |
| HF model fetch times out (e.g. `repvit_m1`) | offline / proxy / CN network, but the model is cached | `export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1` to use the cache |
| `torch.cuda.is_available()` is `False` | driver or wheel mismatch | verify `nvidia-smi`, then reinstall the appropriate PyTorch wheel |

## 9. Agent Model

Cloud model:

```bash
export OPENAI_API_KEY=<key>
export PAWTRACK_MODEL=openai:gpt-5.1
```

DeepSeek via OpenAI-compatible API:

```bash
export OPENAI_API_KEY=<deepseek-key>
export OPENAI_BASE_URL=https://api.deepseek.com
export PAWTRACK_MODEL=openai:deepseek-chat
```

Local Ollama fallback:

```bash
ollama pull llama3.1:8b
export PAWTRACK_MODEL=ollama:llama3.1:8b
```

## 10. Perception Models (subject tracker)

The tracker needs two extra pieces beyond the agent LLM:

**VLM (subject acquisition by description).** Uses Qwen-VL on Alibaba DashScope.
`pawtrack.qwen_china.QwenChinaVlModel` points at the **China** endpoint
(`dashscope.aliyuncs.com`) because DimOS's stock `QwenVlModel` hardcodes the
*international* endpoint, which 401s for a China Model Studio key. Default model
is `qwen-vl-max` (returns accurate pixel-space bboxes; the `qwen3-vl-*`
models return 0-1000 normalized coords the parser does not rescale).

```bash
export ALIBABA_API_KEY=<china-model-studio-key>
export PAWTRACK_VLM_MODEL=qwen-vl-max   # optional override
```

**Tracker (frame-to-frame).** EdgeTAM needs SAM2, shipped as the `edgetam-dimos`
package (the DimOS `[misc]` extra), not in `[base,unitree]`:

```bash
pip install edgetam-dimos timm   # timm is needed by EdgeTAM's image encoder
# or: pip install -e "$DIMOS_HOME[misc]" timm
```

`timm` is a transitive dependency of EdgeTAM's `TimmBackbone` (the `repvit_m1`
RepViT encoder) that `edgetam-dimos` does not pull in on its own.

With these in place the tracker runs end-to-end with no robot via
`--source <file>` / `--camera`: the agent picks the described subject,
`qwen-vl-max` acquires it, and EdgeTAM tracks it frame to frame. A
runnable check and a local verification log live in `.local_docs/` (gitignored):
`python .local_docs/selftest.py`.
