"""Container-level tests for the ball-movement skill glue.

These exercise the DimOS ``BallMonitorSkillContainer`` orchestration -- state
reset on a new target, malformed-input handling, and status publishing -- with
fakes for the EdgeTAM tracker and the output streams, so no GPU, robot, or
network is needed.
"""

import json

import numpy as np

from pawdribble.ball_movement_monitor import BallMonitorSkillContainer
from pawdribble.ball_movement_state import BallTrackMetrics


class _Recorder:
    """Stand-in for an ``Out[str]`` stream that records published messages."""

    def __init__(self):
        self.msgs = []

    def publish(self, msg):
        self.msgs.append(msg)


class _FakeImage:
    """Minimal ``Image`` stand-in: size for bbox math, a frame for overlays."""

    width = 640
    height = 480
    ts = 0.0

    def to_opencv(self):
        return np.zeros((self.height, self.width, 3), dtype=np.uint8)


class _FakeTracker:
    """EdgeTAM stand-in; ``init_count`` controls init success vs failure."""

    def __init__(self, init_count=0):
        self._init_count = init_count

    def init_track(self, **_kwargs):
        return list(range(self._init_count))

    def process_image(self, _image):
        return []  # always a miss, so the monitor loop just coasts

    def stop(self):
        pass


def _container(init_count=0):
    container = BallMonitorSkillContainer()
    container.ball_status = _Recorder()
    container.debug_image = _Recorder()
    container._latest_image = _FakeImage()
    container._tracker = _FakeTracker(init_count)
    return container


def _statuses(container):
    return [json.loads(msg)["status"] for msg in container.ball_status.msgs]


def _metrics():
    return BallTrackMetrics(
        bbox=(10.0, 10.0, 30.0, 30.0), center_px=(20.0, 20.0),
        width_px=20.0, height_px=20.0, area_px=400.0, area_ratio=0.001,
        image_error_x=0.0, image_error_y=0.0, confidence=0.9,
        mask_area_px=320.0, mask_area_ratio=0.001,
    )


def test_track_ball_rejects_malformed_initial_bbox():
    container = _container()
    result = container.track_ball("ball", initial_bbox=[1, 2])
    assert "initial_bbox" in result
    assert container.ball_status.msgs == []  # nothing published
    assert container._snapshot.status == "idle"  # state left untouched


def test_track_ball_resets_prior_state_and_publishes_acquiring():
    container = _container(init_count=0)  # tracker init fails -> no loop thread
    # A prior track left a baseline behind.
    container._state.observe("old ball", _metrics(), 1.0)
    assert container._state._last_tracked is not None

    result = container.track_ball(
        "the red ball", initial_bbox=[10.0, 10.0, 60.0, 60.0])

    assert "Could not initialize" in result
    assert _statuses(container) == ["acquiring", "lost"]
    first = json.loads(container.ball_status.msgs[0])
    assert first["description"] == "the red ball"
    # The old target's baseline was cleared, so it cannot carry forward.
    assert container._state._last_tracked is None


def test_status_json_is_well_formed():
    container = _container(init_count=0)
    container.track_ball("the red ball", initial_bbox=[10.0, 10.0, 60.0, 60.0])
    assert container.ball_status.msgs
    for msg in container.ball_status.msgs:
        data = json.loads(msg)
        assert "seen_at" not in data
        assert "last_seen_age_s" in data
        for key in ("status", "description", "message"):
            assert key in data


def test_start_then_stop_publishes_stopped():
    container = _container(init_count=1)  # tracker init succeeds -> loop starts
    result = container.track_ball(
        "the red ball", initial_bbox=[10.0, 10.0, 60.0, 60.0])
    assert result.startswith("Started monitoring")

    stopped = container.stop_tracking_ball()

    assert stopped.startswith("Stopped")
    assert container._snapshot.status == "stopped"
    statuses = _statuses(container)
    assert "acquiring" in statuses and "stopped" in statuses
    assert container._thread is None or not container._thread.is_alive()
