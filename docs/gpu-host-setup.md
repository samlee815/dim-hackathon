# GPU Host Setup Notes

PawTrack should run on a native Ubuntu x86_64 machine with an NVIDIA
GPU when using DimOS perception modules, VLMs, EdgeTAM, or local model
inference.

## Requirements

- Ubuntu x86_64.
- NVIDIA GPU with a working driver.
- Python virtual environment for this repository.
- Editable DimOS checkout available through `DIMOS_HOME`.
- Robot and host on the same LAN or direct wired subnet for live runs.

## Why GPU

The perception path can share GPU memory across object detection,
video tracking, local language models, and mapping components. Monitor
available VRAM during live runs and avoid starting extra GPU-heavy
services unless they are part of the experiment.

## Checks

Verify that the driver and Python environment can see the GPU:

```bash
nvidia-smi
source "$DIMOS_VENV/bin/activate"
python -c "import torch; print(torch.cuda.is_available())"
```

Run the repository checks from the project root:

```bash
pytest
PYTHONPATH=src python scripts/run_pawtrack.py
```

Use project-relative paths and environment variables in documentation
and scripts. Avoid committing host-specific checkout paths, usernames,
proxy settings, driver versions, or hardware inventory unless a document
explicitly asks contributors to record their own local setup.
