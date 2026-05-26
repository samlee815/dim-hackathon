# DimOS Dev Environment

PawDribble is intended to run on native Ubuntu x86_64 with an NVIDIA GPU.
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

## 5. Daily Use

```bash
source "$DIMOS_VENV/bin/activate"
cd "$REPO_ROOT"

pytest
PYTHONPATH=src python scripts/run_pawdribble.py
PYTHONPATH=src python scripts/run_pawdribble.py --robot
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
dimos run unitree-go2 --robot-ip <dog_ip>
```

For the most reliable camera, LiDAR, and LCM stream behavior, prefer a wired
connection when available.

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
| `torch.cuda.is_available()` is `False` | driver or wheel mismatch | verify `nvidia-smi`, then reinstall the appropriate PyTorch wheel |

## 9. Agent Model

Cloud model:

```bash
export OPENAI_API_KEY=<key>
export PAWDRIBBLE_MODEL=openai:gpt-5.1
```

DeepSeek via OpenAI-compatible API:

```bash
export OPENAI_API_KEY=<deepseek-key>
export OPENAI_BASE_URL=https://api.deepseek.com
export PAWDRIBBLE_MODEL=openai:deepseek-chat
```

Local Ollama fallback:

```bash
ollama pull llama3.1:8b
export PAWDRIBBLE_MODEL=ollama:llama3.1:8b
```
