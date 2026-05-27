"""Container-level tests for the merged PawDribble skill glue.

Exercise the DimOS ``PawDribbleSkillContainer`` orchestration -- ball-monitor
state reset, malformed input, status publishing, and the body-charge kick --
with fakes for the EdgeTAM tracker, the GO2 connection, and the output streams,
so no GPU, robot, or network is needed.
"""

import json

import numpy as np

from pawdribble.ball_movement_state import BallTrackMetrics
from pawdribble.skill_container import PawDribbleSkillContainer


class _Recorder:
    """Stand-in for an ``Out`` stream that records published messages."""

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


class _FakeConnection:
    """Records ``publish_request`` calls (the avoidance toggle goes here)."""

    def __init__(self):
        self.requests = []

    def publish_request(self, topic, data):
        self.requests.append((topic, data))
        return {}


def _container(init_count=0):
    container = PawDribbleSkillContainer()
    container.ball_status = _Recorder()
    container.debug_image = _Recorder()
    container.cmd_vel = _Recorder()
    container.kick_status = _Recorder()
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


# -- Perception --

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


# -- Kick --

def test_kick_publishes_forward_burst_then_stops():
    container = _container()
    # duration 0.3 keeps the default ramp (0.15 = duration/2) valid and fast.
    result = container.kick_ball(speed_mps=0.8, duration_s=0.3)

    assert result.startswith("Kicked")
    twists = container.cmd_vel.msgs
    assert len(twists) >= 2
    assert twists[-1].is_zero()  # always ends stopped
    forward = [t.linear.x for t in twists]
    assert max(forward) > 0.0  # actually charged
    assert max(forward) <= 0.8 + 1e-9  # never exceeds the requested peak
    assert all(vx >= 0.0 for vx in forward)  # only ever drives forward
    assert all(t.angular.z == 0.0 for t in twists)  # straight, no steering
    assert container.kick_status.msgs  # emitted diagnostics
    assert container._kicking is False  # flag cleared


def test_refuses_a_concurrent_kick():
    container = _container()
    container._kicking = True  # simulate a kick already in progress
    result = container.kick_ball(duration_s=0.3)
    assert "Already kicking" in result
    assert container.cmd_vel.msgs == []  # nothing published


def test_invalid_parameters_return_a_clean_error():
    container = _container()
    result = container.kick_ball(speed_mps=-1.0)
    assert "Invalid kick parameters" in result
    assert container.cmd_vel.msgs == []  # never moved
    assert container._kicking is False  # flag cleared even on the error path


def test_rejects_too_long_duration():
    container = _container()
    result = container.kick_ball(duration_s=3.0)
    assert "max_duration_s" in result
    assert container.cmd_vel.msgs == []  # never moved
    assert container._kicking is False


def test_stop_kick_is_safe_when_idle():
    container = _container()
    result = container.stop_kick()
    assert result == "No kick is currently running."
    assert container.cmd_vel.msgs[-1].is_zero()


def test_stop_kick_requests_stop_when_running():
    container = _container()
    container._kicking = True
    result = container.stop_kick()
    assert result == "Stopping the kick."
    assert container.cmd_vel.msgs[-1].is_zero()
    assert container.kick_status.msgs[-1] == (
        "Kick stop requested; stopping the robot."
    )


def test_kick_toggles_obstacle_avoidance_around_the_charge():
    container = _container()
    conn = _FakeConnection()
    container._connection = conn  # simulate the robot stack injecting it
    container.kick_ball(speed_mps=0.8, duration_s=0.3)
    enables = [data["parameter"]["enable"] for _, data in conn.requests]
    assert enables == [0, 1]  # default on: off for charge, then restored


def test_kick_skips_toggle_when_avoidance_already_off():
    container = _container()
    conn = _FakeConnection()
    container._connection = conn
    original = container.config.g.obstacle_avoidance
    container.config.g.obstacle_avoidance = False  # operator runs with it off
    try:
        container.kick_ball(speed_mps=0.8, duration_s=0.3)
    finally:
        container.config.g.obstacle_avoidance = original
    # Already off -> no disable and no restore, so zero RTC round-trips.
    assert conn.requests == []
