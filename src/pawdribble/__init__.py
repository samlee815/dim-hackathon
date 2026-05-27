"""PawDribble -- pure helpers for the Go2 ball-monitoring demo.

Only dependency-free modules are exported here, so importing :mod:`pawdribble`
stays light. The DimOS glue lives in :mod:`pawdribble.skill_container`,
imported directly by the launcher and not re-exported.
"""

from __future__ import annotations

from pawdribble.ball_movement_state import (
    BallTrackMetrics,
    BallVisualObservation,
    visual_metrics,
)

__all__ = [
    "BallTrackMetrics",
    "BallVisualObservation",
    "visual_metrics",
]
