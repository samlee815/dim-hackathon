"""Version-controlled copy of the PawDribble `dimos run` blueprint.

`dimos run <name>` resolves names from a registry that the generator builds by
scanning only the dimos source tree, so the blueprint object has to live inside
that tree. This file is the canonical copy; install it with:

    cp integration/pawdribble_agentic.py \\
        "$DIMOS_HOME/dimos/robot/unitree/go2/blueprints/agentic/"
    pytest "$DIMOS_HOME/dimos/robot/test_all_blueprints_generation.py"  # regen

Then (with the `pawdribble` package importable):

    PYTHONPATH=src dimos run pawdribble-agentic --robot-ip <dog_ip>

It rebuilds the four parts of the stock ``unitree-go2-agentic`` blueprint but
swaps in an ``McpClient`` with the PawDribble prompt and adds the PawDribble
skill container (perception + kick) -- reusing the stock stack, no duplicate
agent.
"""

from __future__ import annotations

import os

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.agentic._common_agentic import (
    _common_agentic,
)
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_spatial import (
    unitree_go2_spatial,
)

from pawdribble.skill_container import PawDribbleSkillContainer
from pawdribble.system_prompt import PAWDRIBBLE_PROMPT

_MODEL = os.environ.get("PAWDRIBBLE_MODEL", "openai:gpt-5.1")

pawdribble_agentic = autoconnect(
    unitree_go2_spatial,
    McpServer.blueprint(),
    McpClient.blueprint(model=_MODEL, system_prompt=PAWDRIBBLE_PROMPT),
    _common_agentic,
    PawDribbleSkillContainer.blueprint(),
)
