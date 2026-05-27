"""Pure body-charge velocity profile for the Go2 "kick" (no DimOS).

The Go2 has no joint-level kick over its normal link, so a "kick" is a short
forward body-charge: drive ``cmd_vel`` forward through the ball, then stop. This
module is the pure, testable core -- it computes the commanded ``(vx, wz)`` at a
given elapsed time: a ramped forward burst clamped to safe limits, then zero
once the charge window ends. The DimOS skill just publishes whatever this
returns each tick. Pure: stdlib only, unit tested.
"""

from __future__ import annotations

import dataclasses

# Commanded body velocity: (forward m/s, yaw rad/s).
Twist2D = tuple[float, float]


@dataclasses.dataclass(frozen=True)
class KickParams:
    """Tunables for one forward body-charge.

    Attributes:
        speed_mps: Peak forward charge speed.
        duration_s: Total charge time; the command is zero afterwards.
        ramp_s: Ramp-in/-out time, easing the start and stop so the charge is
            not a single jerk. Must be at most half of ``duration_s``.
        yaw_rate_radps: Steady yaw during the charge; zero drives straight
            (the planner has already aimed the robot at the ball).
        max_speed_mps: Hard safety clamp on the forward command.
        max_yaw_radps: Hard safety clamp on the yaw command.
        max_duration_s: Hard safety clamp on charge duration.
    """

    speed_mps: float = 0.8
    duration_s: float = 0.8
    ramp_s: float = 0.15
    yaw_rate_radps: float = 0.0
    max_speed_mps: float = 1.5
    max_yaw_radps: float = 1.0
    max_duration_s: float = 2.0

    def __post_init__(self):
        if self.speed_mps <= 0.0:
            raise ValueError("speed_mps must be positive")
        if self.duration_s <= 0.0:
            raise ValueError("duration_s must be positive")
        if self.max_duration_s <= 0.0:
            raise ValueError("max_duration_s must be positive")
        if self.duration_s > self.max_duration_s:
            raise ValueError("duration_s must not exceed max_duration_s")
        if self.ramp_s < 0.0 or self.ramp_s > self.duration_s / 2.0:
            raise ValueError("ramp_s must be in [0, duration_s / 2]")
        if self.max_speed_mps <= 0.0:
            raise ValueError("max_speed_mps must be positive")
        if self.max_yaw_radps < 0.0:
            raise ValueError("max_yaw_radps must be non-negative")


def charge_velocity(params: KickParams, elapsed_s: float) -> Twist2D:
    """Commanded ``(vx, wz)`` at ``elapsed_s`` into the charge.

    Returns ``(0.0, 0.0)`` before the charge starts and once it has run for
    ``duration_s``; in between, a ramped forward speed (and the steady yaw),
    each clamped to the configured safety limits.

    Args:
        params: The charge tunables.
        elapsed_s: Seconds since the charge began.

    Returns:
        ``(vx_mps, wz_radps)`` to publish this tick.
    """
    if elapsed_s < 0.0 or elapsed_s >= params.duration_s:
        return (0.0, 0.0)
    speed = _ramped(
        params.speed_mps, elapsed_s, params.duration_s, params.ramp_s)
    vx = _clamp(speed, 0.0, params.max_speed_mps)
    wz = _clamp(
        params.yaw_rate_radps, -params.max_yaw_radps, params.max_yaw_radps)
    return (vx, wz)


def _ramped(peak: float, t: float, duration: float, ramp: float) -> float:
    """Trapezoidal profile: ease 0->peak over ``ramp``, hold, peak->0."""
    if ramp <= 0.0:
        return peak
    rise = peak * (t / ramp) if t < ramp else peak
    fall = peak * ((duration - t) / ramp) if t > duration - ramp else peak
    return max(0.0, min(rise, fall))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))
