"""PawDribble agentic blueprint with optional prebuilt-map relocalization.

Install this next to ``pawdribble_agentic.py`` in the DimOS source tree, then
regenerate ``all_blueprints``:

    cp integration/pawdribble_agentic_relocalization.py \
        "$DIMOS_HOME/dimos/robot/unitree/go2/blueprints/agentic/"
    pytest -o addopts="" \
        "$DIMOS_HOME/dimos/robot/test_all_blueprints_generation.py"

Run with a prebuilt map:

    PYTHONPATH=src dimos run pawdribble-agentic-relocalization \
        --robot-ip <dog_ip> \
        -o relocalizationmodule.map_file=<premap_name>

This intentionally keeps the existing ``pawdribble-agentic`` blueprint
unchanged.
Use this blueprint only when you want ``ball_map_pose`` in addition to the
always-on ``ball_world_pose``.
"""

from __future__ import annotations

import os

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.core.coordination.blueprints import autoconnect
from dimos.mapping.relocalization.module import RelocalizationModule
from dimos.robot.unitree.go2.blueprints.agentic._common_agentic import (
    _common_agentic,
)
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_spatial import (
    unitree_go2_spatial,
)

from pawdribble.skill_container import PawDribbleSkillContainer
from pawdribble.system_prompt import PAWDRIBBLE_PROMPT

_MODEL = os.environ.get("PAWDRIBBLE_MODEL", "openai:gpt-5.1")

pawdribble_agentic_relocalization = autoconnect(
    unitree_go2_spatial,
    RelocalizationModule.blueprint(),
    McpServer.blueprint(),
    McpClient.blueprint(model=_MODEL, system_prompt=PAWDRIBBLE_PROMPT),
    _common_agentic,
    PawDribbleSkillContainer.blueprint(),
)
