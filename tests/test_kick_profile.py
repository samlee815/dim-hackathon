"""Tests for the pure body-charge velocity profile."""

from pawdribble.kick_profile import KickParams, charge_velocity


def _close(actual, expected, tol=1e-9):
    return all(abs(a - e) <= tol for a, e in zip(actual, expected))


def test_ramps_in_holds_then_ramps_out():
    params = KickParams(speed_mps=1.0, duration_s=1.0, ramp_s=0.2)
    assert _close(charge_velocity(params, 0.0), (0.0, 0.0))  # start of ramp-in
    assert _close(charge_velocity(params, 0.1), (0.5, 0.0))  # mid ramp-in
    assert _close(charge_velocity(params, 0.5), (1.0, 0.0))  # full speed hold
    assert _close(charge_velocity(params, 0.9), (0.5, 0.0))  # mid ramp-out


def test_zero_outside_the_charge_window():
    params = KickParams(speed_mps=1.0, duration_s=1.0, ramp_s=0.2)
    assert charge_velocity(params, -0.1) == (0.0, 0.0)
    assert charge_velocity(params, 1.0) == (0.0, 0.0)  # window is half-open
    assert charge_velocity(params, 2.0) == (0.0, 0.0)


def test_speed_is_clamped_to_max():
    params = KickParams(
        speed_mps=2.0, duration_s=1.0, ramp_s=0.0, max_speed_mps=1.5)
    vx, _ = charge_velocity(params, 0.5)
    assert vx == 1.5  # 2.0 requested, clamped to the 1.5 safety limit


def test_yaw_defaults_straight_and_clamps():
    straight = KickParams(speed_mps=1.0, duration_s=1.0, ramp_s=0.0)
    assert charge_velocity(straight, 0.5)[1] == 0.0
    turning = KickParams(
        speed_mps=1.0, duration_s=1.0, ramp_s=0.0,
        yaw_rate_radps=2.0, max_yaw_radps=1.0)
    assert charge_velocity(turning, 0.5)[1] == 1.0  # clamped


def test_invalid_params_raise():
    for bad in (
        dict(speed_mps=0.0),
        dict(duration_s=0.0),
        dict(ramp_s=0.6, duration_s=1.0),  # ramp > duration / 2
        dict(max_speed_mps=0.0),
        dict(duration_s=3.0, max_duration_s=2.0),
    ):
        try:
            KickParams(**bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad}")
