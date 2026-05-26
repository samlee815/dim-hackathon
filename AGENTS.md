# AGENTS.md

Guidance for AI agents and humans writing code in this repo (PawDribble — a DimOS /
Unitree Go2 hackathon project). Read this before writing or editing Python.

## Python Style Guide

This repo follows the **[Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)**.
The rules below are the load-bearing subset; the full guide is authoritative where this
summary is silent.

### Formatting
- Line length **80** characters max. Use implicit line joining (parentheses), never `\`
  backslash continuation.
- 4 spaces per indent level. Never tabs.
- One statement per line. No semicolons.
- No whitespace inside brackets/parens or before commas; spaces around binary operators.
- No parentheses around return values unless building a tuple.
- Use `"""` for multi-line strings, not `'''`. Be consistent with quote style in a file.

### Imports
- `import x` for packages and modules; `from x import y` for submodules. Import modules,
  not individual symbols (except `typing` / `collections.abc`, which may share a line).
- Group and order: (1) `__future__`, (2) stdlib, (3) third-party, (4) this repo's
  sub-packages. Sort lexicographically within each group; blank line between groups.
- No wildcard imports (`from x import *`).

### Naming
| Entity | Public | Internal |
|--------|--------|----------|
| Modules / packages | `lower_with_under` | `_lower_with_under` |
| Classes | `CapWords` | `_CapWords` |
| Functions / methods | `lower_with_under()` | `_lower_with_under()` |
| Constants | `CAPS_WITH_UNDER` | `_CAPS_WITH_UNDER` |
| Variables / parameters | `lower_with_under` | `_lower_with_under` |

Names describe intent. Avoid single-letter names except short-lived loop indices / math.

### Type annotations
- Annotate public APIs at minimum; annotate freely elsewhere — it's encouraged.
- Use `X | None` (Python ≥3.10), not implicit `None` defaults or bare `Optional`.
- Don't annotate `self`, `cls`, or `__init__`'s return.
- One parameter per line for long signatures.
- Use a `TYPE_CHECKING` block for type-only imports. `from __future__ import annotations`
  at the top of modules is the existing convention in `src/pawdribble`.

### Docstrings (PEP 257, Google format)
- Triple double-quotes `"""`. Summary line, blank line, then details.
- **Functions/methods:** `Args:` (name: description), `Returns:` (or `Yields:`), `Raises:`.
- **Classes:** what an instance represents; `Attributes:` for public attributes.
- **Modules:** summary + overview, optional usage example.
- For DimOS `@skill` methods the docstring **is** the LLM tool description — write it as a
  clear instruction: when to call it and what each arg means.

### Comments & TODOs
- Comments explain *why*, not *what* — assume the reader knows Python.
- Two spaces before `#`, one space after. Proper grammar and punctuation.
- TODO format: `# TODO: context-link - explanation`. No anonymous TODOs.
- No commented-out code.

### Functions & structure
- Do not use mutable objects (`[]`, `{}`) as default argument values. Use `None` and
  initialize inside, or `dataclasses.field(default_factory=...)`.
- Never use `staticmethod` — use a module-level function instead.
- Avoid mutable global state; module-level constants are fine.
- Use `with` for file/resource management.
- Prefer pure functions and dependency injection (pass timestamps, robot refs in) so
  logic is testable without hardware — the pure-logic pattern in `dribble_planner.py`.

### Exceptions
- Catch specific exception types, never bare `except:` or `except Exception`.
- Keep `try` blocks minimal; use `finally` for cleanup.
- Don't use `assert` for runtime validation (asserts are for tests).

### Blank lines
- Two blank lines between top-level definitions; one between methods.
- One blank line after a class docstring; none after a `def` line.

### Executable scripts
- Guard side effects with `if __name__ == "__main__":` so modules import cleanly.

## Environment — native Ubuntu, run in the venv
All Python in this project — the agent, the DimOS blueprint, and the tests — runs natively
on Ubuntu x86_64 with an NVIDIA GPU. Do not assume fixed local checkout paths. Use
`SETUP.md` and these variables when running commands:

```bash
export REPO_ROOT="$(pwd)"
export DIMOS_HOME="${DIMOS_HOME:-/path/to/dimos}"
export DIMOS_VENV="${DIMOS_VENV:-$REPO_ROOT/.venv}"
```

Activate the venv, then run anything, e.g. the tests:
```bash
source "$DIMOS_VENV/bin/activate" && cd "$REPO_ROOT" && pytest
```

## Tests
- Every non-trivial unit gets a `pytest` test. Importing DimOS in tests is fine (it is
  installed in the venv); keep real hardware and heavy models (robot, EdgeTAM, the VLM) out
  via pure logic (`ball_movement_state.py`) or fakes for injected module refs.
- Test names state the behavior: `test_records_and_recalls_most_recent_location`.
- Run tests in the venv (see Environment above): `source "$DIMOS_VENV/bin/activate" && cd "$REPO_ROOT" && pytest`.

## Project layout
- `src/pawdribble/` — first-party package (importable via `PYTHONPATH=src`).
- `tests/` — pytest suite: pure-logic tests plus DimOS-glue tests that fake injected refs.
- `docs/` — design (`pawdribble-design.md`), DimOS agent reference (`dimos-agent-findings.md`),
  and host rationale (`gpu-host-setup.md`).
- DimOS upstream source for reference lives outside this repo at `$DIMOS_HOME`.

## Before committing
Run the suite in the venv (see Environment above):
```bash
source "$DIMOS_VENV/bin/activate" && cd "$REPO_ROOT" && pytest
```
