"""DimOS skill container for PawDribble: find, track, and kick a ball.

One container exposes the whole PawDribble robot behavior as DimOS skills:

- Perception: the user describes a ball, a VLM localizes it, EdgeTAM tracks the
  selected object frame-to-frame, and JSON status (bbox, centering, size) is
  published. Decision logic lives in the pure ``MonitorState``; this module runs
  the effects (tracker/VLM, publish snapshots, debug frames). The monitor loop
  fails loudly -- any exception is logged and surfaced as an ``error`` status
  rather than letting the worker thread die silently. When EdgeTAM drops a
  moving ball, a frame-motion fallback re-seeds the tracker before retrying.
- Kick: once an upstream planner has positioned the robot behind the ball, a
  short forward ``cmd_vel`` body-charge (the Go2 has no joint-level kick over
  its link) drives through the ball, then stops. Velocity math is the pure
  ``kick_profile``. Obstacle avoidance would brake the charge, so it is disabled
  for the charge window and the configured state restored after, via the
  injected GO2 connection (a no-op off-robot, where no connection is wired).

The monitor (continuous, sensor-driven) and the kick (on-demand burst) are
independent; they share only the module's lock.
"""

from __future__ import annotations

import base64
import threading
import time
from typing import Any

import cv2
import numpy as np
from reactivex.disposable import Disposable
from turbojpeg import TurboJPEG

from dimos.agents.annotation import skill
from dimos.constants import DEFAULT_THREAD_JOIN_TIMEOUT
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.models.qwen.bbox import BBox
from dimos.models.segmentation.edge_tam import EdgeTAMProcessor
from dimos.models.vl.base import VlModel
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.sensor_msgs.CameraInfo import CameraInfo
from dimos.msgs.sensor_msgs.Image import Image, ImageFormat
from dimos.navigation.visual.query import get_object_bbox_from_image
from dimos.perception.detection.type.detection2d.bbox import Detection2DBBox
from dimos.robot.unitree.go2.connection_spec import GO2ConnectionSpec
from dimos.utils.logging_config import setup_logger
from unitree_webrtc_connect.constants import RTC_TOPIC

from pawdribble.ball_movement_motion_fallback import detect_motion_bbox
from pawdribble.ball_movement_state import (
    BallMonitorSnapshot,
    BallVisualObservation,
    MonitorParams,
    MonitorState,
    bbox_in_image,
    clamp_bbox,
    is_bbox_shape,
    valid_bbox,
    visual_metrics,
)
from pawdribble.ground_raycast import CameraIntrinsics, pixel_to_ground_point
from pawdribble.kick_profile import KickParams, charge_velocity
from pawdribble.qwen_china import QwenChinaVlModel

logger = setup_logger()


class Config(ModuleConfig):
    """Configuration for the PawDribble skills (ball monitor + kick)."""

    # Ball monitoring.
    monitor_loop_hz: float = 15.0
    max_lost_frames: int = 15  # frames without a mask before reacquiring
    reacquire_interval_frames: int = 15  # VLM retry cadence while lost
    max_reacquire_attempts: int = 5  # give up after this many failed retries
    motion_fallback: bool = True  # re-seed tracker from frame motion on a miss
    max_center_jump_frac: float = 2.5  # reject jumps beyond this x ball width
    max_area_factor: float = 4.0  # reject area changes beyond this factor
    stale_timeout_s: float = 2.0  # no fix longer than this -> "stale"
    # Body-charge kick.
    kick_speed_mps: float = 0.8  # peak forward charge speed
    kick_duration_s: float = 0.8  # total charge time
    kick_ramp_s: float = 0.15  # ramp-in/-out to soften the start/stop
    kick_loop_hz: float = 20.0  # cmd_vel re-publish rate (beats the watchdog)
    kick_max_speed_mps: float = 1.5  # hard safety clamp on forward speed
    kick_max_duration_s: float = 2.0  # hard safety clamp on charge duration
    kick_max_yaw_radps: float = 1.0  # hard safety clamp on yaw
    # Ball position (ground-plane raycast, approach "B"). The tracked ball
    # pixel is back-projected to the floor in the live world frame for the
    # planner. If relocalization is live, the same point is also published in
    # the stable map frame.
    camera_info: CameraInfo | None = None  # intrinsics; auto-resolved if None
    # Ball-center height above the floor, assuming a roughly 22 cm ball.
    ball_radius_m: float = 0.11
    # Floor height (z) in each frame, kept separate from the ball radius so a
    # frame whose z=0 is not the floor is a config change, not a logic change.
    # The raycast plane is ``floor_z + ball_radius_m`` (the ball center).
    world_floor_z_m: float = 0.0  # floor z in the live odometry frame
    map_floor_z_m: float = 0.0  # floor z in the prebuilt-map frame
    world_frame_id: str = "world"  # always-on live odometry frame
    map_frame_id: str = "map"  # optional prebuilt-map frame
    camera_optical_frame_id: str = "camera_optical"
    tf_tolerance_s: float = 1.0  # max age for the live world/odom TF lookup
    # The map<-world TF is republished only every ~2 s by relocalization, so the
    # map lookup needs a looser tolerance than the live odom chain or it would
    # pause for the back half of each interval even when relocalization is
    # healthy. Must exceed the relocalization publish interval (2.0 s).
    map_tf_tolerance_s: float = 3.0
    # While a pose frame's TF is absent, retry its lookup at most this often
    # rather than every tracked frame -- ``tf.get`` warns on each miss, which
    # would otherwise flood logs at the monitor loop rate.
    pose_probe_interval_s: float = 2.0


class PawDribbleSkillContainer(Module):
    """Find, track, and body-charge a user-described ball."""

    config: Config

    color_image: In[Image]
    ball_status: Out[str]  # JSON status (LCM diagnostic stream)
    debug_image: Out[Image]  # annotated camera frame (shows in Rerun)
    cmd_vel: Out[Twist]  # body-charge velocity to the robot
    kick_status: Out[str]  # human-readable kick diagnostics
    ball_world_pose: Out[PoseStamped]  # ball position in live odometry frame
    ball_map_pose: Out[PoseStamped]  # optional ball position in premap frame
    # Injected on the robot stack; None off-robot (avoidance toggle skipped).
    _connection: GO2ConnectionSpec | None = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._lock = threading.RLock()
        self._monitor_stop = threading.Event()
        self._kick_stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest_image: Image | None = None
        self._prev_image: Image | None = None
        self._state = MonitorState(MonitorParams(
            max_lost_frames=self.config.max_lost_frames,
            reacquire_interval_frames=self.config.reacquire_interval_frames,
            max_reacquire_attempts=self.config.max_reacquire_attempts,
            max_center_jump_frac=self.config.max_center_jump_frac,
            max_area_factor=self.config.max_area_factor,
            stale_timeout_s=self.config.stale_timeout_s,
        ))
        self._snapshot = self._state.snapshot
        self._vl_model: VlModel | None = None
        self._tracker: EdgeTAMProcessor | None = None
        self._kicking = False
        # Believed onboard avoidance state, so we skip redundant RTC requests.
        self._avoidance_enabled: bool | None = None
        self._intrinsics = self._resolve_intrinsics()
        self._warned_no_world_tf = False
        self._warned_no_map_tf = False
        # Monotonic time before which a pose frame's TF lookup is skipped, per
        # frame_id, to throttle retries while that frame is absent.
        self._next_pose_probe_s: dict[str, float] = {}

    def _resolve_intrinsics(self) -> CameraIntrinsics | None:
        """Pinhole intrinsics for the ground-plane raycast.

        Prefer an explicit ``config.camera_info``; otherwise use the static
        intrinsics of whichever connection backs this run (sim or the real
        Go2), so the raycast works without extra blueprint wiring. Returns
        ``None`` if no usable intrinsics are found (pose publishing then
        no-ops).
        """
        camera_info = self.config.camera_info
        if camera_info is None:
            simulation = self.config.g.simulation
            if simulation == "mujoco":
                from dimos.robot.unitree.mujoco_connection import (
                    MujocoConnection,
                )

                camera_info = MujocoConnection.camera_info_static
            elif simulation == "dimsim":
                from dimos.robot.unitree.dimsim_connection import (
                    DimSimConnection,
                )

                camera_info = DimSimConnection.camera_info_static
            else:
                from dimos.robot.unitree.go2.connection import GO2Connection

                camera_info = GO2Connection.camera_info_static
        if (
            camera_info is None
            or len(camera_info.K) != 9
            or camera_info.K[0] <= 0.0
        ):
            return None
        return CameraIntrinsics.from_k(list(camera_info.K))

    @rpc
    def start(self) -> None:
        super().start()
        self.register_disposable(
            Disposable(self.color_image.subscribe(self._on_color_image))
        )

    @rpc
    def stop(self) -> None:
        self._kick_stop.set()
        self._stop_monitoring()
        with self._lock:
            tracker = self._tracker
            self._tracker = None
            vl_model = self._vl_model
            self._vl_model = None
        if tracker is not None:
            tracker.stop()
        if vl_model is not None:
            vl_model.stop()
        super().stop()

    # -- Perception skills --

    @skill
    def track_ball(
        self,
        description: str,
        initial_bbox: list[float] | None = None,
        initial_image: str | None = None,
    ) -> str:
        """Start monitoring the ball matching a visual description.

        Use this when the user asks to find, track, watch, or monitor a
        specific ball. The description should identify the ball visually, for
        example "the red ball", "the tennis ball", or "the blue striped ball".

        Args:
            description: Visual description of the ball to monitor.
            initial_bbox: Optional bbox ``[x1, y1, x2, y2]`` to skip VLM
                acquisition when another tool or UI already selected the ball.
            initial_image: Optional base64 JPEG matching ``initial_bbox``.

        Returns:
            Status text describing whether monitoring started.
        """
        if initial_bbox is not None and not is_bbox_shape(initial_bbox):
            return "initial_bbox must be four numbers [x1, y1, x2, y2]."

        self._stop_monitoring()

        with self._lock:
            latest_image = self._latest_image
        if latest_image is None:
            return "No image available to identify the ball."

        # Reset the state machine for the new target before EITHER acquisition
        # path, so a previous track's last fix cannot carry forward under the
        # new description (via the drift gate or a carried-forward bbox).
        self._set_snapshot(self._state.begin(description))

        detection_image = None
        if initial_bbox is not None:
            bbox: BBox = (
                initial_bbox[0],
                initial_bbox[1],
                initial_bbox[2],
                initial_bbox[3],
            )
            if initial_image is not None:
                detection_image = _decode_base64_image(initial_image)
        else:
            detected = get_object_bbox_from_image(
                self._get_vl_model(),
                latest_image,
                description,
            )
            if detected is None:
                self._set_snapshot(BallMonitorSnapshot(
                    status="lost",
                    description=description,
                    message=f"Could not find {description} in view.",
                ))
                return f"Could not find '{description}' in the current view."
            bbox = detected

        return self._start_tracking(description, bbox, detection_image)

    @skill
    def stop_tracking_ball(self) -> str:
        """Stop monitoring the currently tracked ball."""
        self._stop_monitoring()
        self._set_snapshot(self._state.stopped())
        return "Stopped monitoring the ball."

    @skill
    def ball_tracking_status(self) -> str:
        """Return the latest tracked-ball bbox, size, and centering metrics."""
        with self._lock:
            snapshot = self._snapshot
        return snapshot.to_json()

    # -- Kick skills --

    @skill
    def kick_ball(
        self,
        speed_mps: float | None = None,
        duration_s: float | None = None,
    ) -> str:
        """Kick the ball forward with a body-charge.

        Call this only when the robot is already positioned directly behind the
        ball and facing it -- the planner handles the approach and aiming. The
        robot drives a short forward burst through the ball and then stops; it
        does not steer, so do not call this until the ball is centered ahead.

        Args:
            speed_mps: Optional peak charge speed override (m/s).
            duration_s: Optional charge duration override (seconds).

        Returns:
            Status text describing the kick or why it was refused.
        """
        with self._lock:
            if self._kicking:
                return "Already kicking; ignoring the new kick request."
            self._kicking = True
        speed = self.config.kick_speed_mps if speed_mps is None else speed_mps
        duration = (
            self.config.kick_duration_s if duration_s is None else duration_s
        )
        try:
            params = KickParams(
                speed_mps=speed,
                duration_s=duration,
                ramp_s=self.config.kick_ramp_s,
                max_speed_mps=self.config.kick_max_speed_mps,
                max_duration_s=self.config.kick_max_duration_s,
                max_yaw_radps=self.config.kick_max_yaw_radps,
            )
        except ValueError as err:
            logger.error("kick_ball: invalid parameters: %s", err)
            with self._lock:
                self._kicking = False
            return f"Invalid kick parameters: {err}"
        try:
            return self._charge(params)
        finally:
            with self._lock:
                self._kicking = False

    @skill
    def stop_kick(self) -> str:
        """Stop an in-progress body-charge kick and publish zero velocity.

        Call this when the user asks to cancel, stop, abort, or interrupt a
        kick. It is safe to call when no kick is running.

        Returns:
            Status text describing whether a kick was stopped.
        """
        with self._lock:
            was_kicking = self._kicking
        self._kick_stop.set()
        self.cmd_vel.publish(Twist.zero())
        if was_kicking:
            self.kick_status.publish("Kick stop requested; stopping the robot.")
            return "Stopping the kick."
        return "No kick is currently running."

    # -- Internals: perception --

    def _on_color_image(self, image: Image) -> None:
        with self._lock:
            self._latest_image = image

    def _get_vl_model(self) -> VlModel:
        with self._lock:
            if self._vl_model is None:
                self._vl_model = QwenChinaVlModel()
            return self._vl_model

    def _get_tracker(self) -> EdgeTAMProcessor:
        with self._lock:
            if self._tracker is None:
                self._tracker = EdgeTAMProcessor()
            return self._tracker

    def _start_tracking(
        self,
        description: str,
        bbox: BBox,
        detection_image: Image | None,
    ) -> str:
        with self._lock:
            latest_image = self._latest_image
        if latest_image is None:
            return "No image available to start ball tracking."
        init_image = (
            detection_image if detection_image is not None else latest_image
        )
        if not valid_bbox(bbox, init_image.width, init_image.height):
            logger.error(
                "track_ball: invalid bbox %s for a %dx%d image",
                bbox, init_image.width, init_image.height,
            )
            self._set_snapshot(self._state.errored(
                description, f"Invalid bounding box for {description}."
            ))
            return (
                f"Invalid bounding box for '{description}'; cannot start "
                "tracking."
            )
        bbox = clamp_bbox(bbox, init_image.width, init_image.height)

        tracker = self._get_tracker()
        initial_detections = tracker.init_track(
            image=init_image, box=np.array(bbox, dtype=np.float32), obj_id=1
        )
        if len(initial_detections) == 0:
            self._set_snapshot(BallMonitorSnapshot(
                status="lost",
                description=description,
                message=f"Tracker could not initialize on {description}.",
            ))
            return f"Could not initialize tracking for '{description}'."

        self.start_tool("track_ball")
        with self._lock:
            self._prev_image = None
            self._monitor_stop.clear()
            self._thread = threading.Thread(
                target=self._monitor_loop,
                args=(tracker, description),
                name="ball-monitor",
                daemon=True,
            )
            self._thread.start()

        return (
            f"Started monitoring '{description}'. Call ball_tracking_status for"
            " the latest size and position metrics."
        )

    def _monitor_loop(
        self,
        tracker: EdgeTAMProcessor,
        description: str,
    ) -> None:
        period = 1.0 / self.config.monitor_loop_hz
        next_time = time.monotonic()

        while not self._monitor_stop.is_set():
            next_time += period
            try:
                with self._lock:
                    image = self._latest_image
                detection = self._track_frame(tracker, image)
                metrics = (
                    self._metrics(image, detection)
                    if detection is not None else None
                )
                snapshot, action = self._state.observe(
                    description, metrics, time.monotonic())
                self._set_snapshot(snapshot)
                if action == "give_up":
                    break
                if action == "reacquire" and image is not None:
                    if self._reacquire(tracker, description, image):
                        self._state.reacquired()
                if image is not None:
                    drawn = detection if snapshot.status == "tracking" else None
                    self._publish_debug(description, image, drawn)
                    if drawn is not None and snapshot.center_px is not None:
                        self._publish_ground_poses(image, snapshot.center_px)
            except Exception:  # noqa: BLE001 - thread-boundary guard
                # Deliberately broad: this is the worker-thread top level. Fail
                # LOUDLY (full traceback + an `error` status) instead of letting
                # the thread die silently and freeze the status.
                logger.exception(
                    "ball monitor loop crashed for %r", description)
                self._set_snapshot(self._state.errored(
                    description, "Monitor loop error; see logs."))
                break

            now = time.monotonic()
            sleep_duration = next_time - now
            if sleep_duration > 0:
                self._monitor_stop.wait(sleep_duration)
            else:
                # Fell behind (e.g. a slow VLM reacquire); drop the banked lag
                # instead of spinning flat-out forever.
                next_time = now

        self.stop_tool("track_ball")

    def _track_frame(
        self, tracker: EdgeTAMProcessor, image: Image | None
    ) -> Detection2DBBox | None:
        """Track one frame, with a frame-motion re-seed fallback on a miss."""
        if image is None:
            return None
        detection = self._tracked_detection(tracker, image)
        prev_image = self._prev_image
        self._prev_image = image
        if detection is not None:
            return detection
        # EdgeTAM lost it: try to recover from frame motion (cheap, no VLM).
        # NOTE: this re-seeds on the largest moving blob over the whole frame,
        # which assumes a roughly static camera. Under robot ego-motion (e.g. a
        # Go2 body-charge) the background can dominate and re-seed onto the
        # wrong thing -- disable via the `motion_fallback` config, or gate it on
        # robot motion state, once the kick/approach layer drives the camera.
        if not self.config.motion_fallback or prev_image is None:
            return None
        bbox = detect_motion_bbox(prev_image.to_opencv(), image.to_opencv())
        if bbox is None or not bbox_in_image(bbox, image.width, image.height):
            return None
        tracker.init_track(
            image=image, box=np.array(bbox, dtype=np.float32), obj_id=1)
        recovered = self._tracked_detection(tracker, image)
        if recovered is not None:
            # Motion recovery is a reacquire: re-baseline so the drift gate
            # does not reject the (possibly far) recovered position.
            self._state.reacquired()
        return recovered

    def _tracked_detection(
        self, tracker: EdgeTAMProcessor, image: Image
    ) -> Detection2DBBox | None:
        detections = tracker.process_image(image)
        if len(detections) == 0:
            return None
        best = max(detections.detections, key=lambda d: d.bbox_2d_volume())
        if not bbox_in_image(best.bbox, image.width, image.height):
            return None
        return best

    def _reacquire(
        self, tracker: EdgeTAMProcessor, description: str, image: Image
    ) -> bool:
        """Re-run the VLM for the description and re-init the tracker on it."""
        bbox = get_object_bbox_from_image(
            self._get_vl_model(), image, description
        )
        if bbox is None:
            return False
        if not valid_bbox(bbox, image.width, image.height):
            logger.error(
                "reacquire: VLM returned an invalid bbox %s for %r",
                bbox, description,
            )
            return False
        bbox = clamp_bbox(bbox, image.width, image.height)
        detections = tracker.init_track(
            image=image, box=np.array(bbox, dtype=np.float32), obj_id=1
        )
        return len(detections) > 0

    def _metrics(self, image: Image, detection: Detection2DBBox):
        mask = getattr(detection, "mask", None)
        mask_area = None if mask is None else float(np.count_nonzero(mask))
        return visual_metrics(BallVisualObservation(
            bbox=detection.bbox,
            image_width=image.width,
            image_height=image.height,
            confidence=detection.confidence,
            mask_area_px=mask_area,
        ))

    def _publish_debug(
        self,
        description: str,
        image: Image,
        detection: Detection2DBBox | None,
    ) -> None:
        """Publish an annotated frame for Rerun: green box when tracking, red
        label while searching."""
        if detection is not None:
            frame = _draw_overlay(
                image, detection.bbox, description, (0, 255, 0)
            )
        else:
            frame = _draw_overlay(
                image, None, f"searching: {description}", (0, 0, 255)
            )
        self.debug_image.publish(frame)

    def _publish_ground_poses(
        self, image: Image, center_px: tuple[float, float]
    ) -> None:
        """Publish the tracked ball's ground position for planning.

        Back-projects the ball's image center onto the floor plane (approach
        "B") and publishes it as ``PoseStamped`` in the live ``world`` frame.
        If relocalization is publishing a prebuilt ``map`` frame, also publishes
        the same observation there. Tracking is never blocked on missing TF.

        Args:
            image: The frame the detection came from (supplies the timestamp).
            center_px: ``(u, v)`` pixel center of the tracked ball.
        """
        self._publish_ground_pose(
            image,
            center_px,
            self.config.world_frame_id,
            self.config.world_floor_z_m,
            self.ball_world_pose,
            self.config.tf_tolerance_s,
            required=True,
        )
        self._publish_ground_pose(
            image,
            center_px,
            self.config.map_frame_id,
            self.config.map_floor_z_m,
            self.ball_map_pose,
            self.config.map_tf_tolerance_s,
            required=False,
        )

    def _publish_ground_pose(
        self,
        image: Image,
        center_px: tuple[float, float],
        frame_id: str,
        floor_z: float,
        stream: Out[PoseStamped],
        tf_tolerance: float,
        required: bool,
    ) -> None:
        """Publish one ground-raycasted ball pose in ``frame_id``.

        Args:
            floor_z: Floor height in ``frame_id``; the raycast plane is
                ``floor_z + ball_radius_m`` so the result is the ball center.
            tf_tolerance: Max age for the ``frame_id <- camera_optical`` lookup.
                The map frame's TF is republished slowly, so it needs a looser
                tolerance than the live odom chain.
            required: Whether this frame should always be present (live world)
                vs optional (prebuilt map); only changes the warning wording.
        """
        if self._intrinsics is None:
            return
        # Throttle retries while this frame's TF is absent: tf.get logs on every
        # miss, so probing each tracked frame would flood logs at loop rate.
        now = time.monotonic()
        if now < self._next_pose_probe_s.get(frame_id, 0.0):
            return
        transform = self.tf.get(
            frame_id,
            self.config.camera_optical_frame_id,
            time_point=image.ts,
            time_tolerance=tf_tolerance,
        )
        if transform is None:
            self._next_pose_probe_s[frame_id] = (
                now + self.config.pose_probe_interval_s)
            self._warn_missing_tf_once(frame_id, required)
            return
        self._next_pose_probe_s[frame_id] = 0.0  # present -> probe every frame
        point = pixel_to_ground_point(
            center_px,
            self._intrinsics,
            transform.to_matrix(),
            ground_z=floor_z + self.config.ball_radius_m,
        )
        if point is None:
            return
        stream.publish(PoseStamped(
            ts=image.ts,
            frame_id=frame_id,
            position=list(point),
            orientation=[0.0, 0.0, 0.0, 1.0],
        ))

    def _warn_missing_tf_once(self, frame_id: str, required: bool) -> None:
        """Log a single warning the first time a pose frame's TF is missing."""
        if required and not self._warned_no_world_tf:
            logger.warning(
                "No %r<-%r transform yet; ball_world_pose paused.",
                frame_id,
                self.config.camera_optical_frame_id,
            )
            self._warned_no_world_tf = True
        elif not required and not self._warned_no_map_tf:
            logger.warning(
                "No %r<-%r transform yet; optional ball_map_pose paused until "
                "relocalization publishes the map frame.",
                frame_id,
                self.config.camera_optical_frame_id,
            )
            self._warned_no_map_tf = True

    def _set_snapshot(self, snapshot: BallMonitorSnapshot) -> None:
        with self._lock:
            previous = self._snapshot
            self._snapshot = snapshot
        # Publish live tracking every frame; otherwise only on a real change,
        # so a long "lost" stretch does not spam identical status messages.
        if (
            snapshot.status == "tracking"
            or snapshot.status != previous.status
            or snapshot.message != previous.message
        ):
            self.ball_status.publish(snapshot.to_json())

    def _stop_monitoring(self) -> None:
        self._monitor_stop.set()
        with self._lock:
            thread = self._thread
            self._thread = None
        # Join outside the lock; the loop thread takes the lock each iteration.
        if thread is not None:
            thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)

    # -- Internals: kick --

    def _charge(self, params: KickParams) -> str:
        self._kick_stop.clear()
        self.kick_status.publish(
            f"Charging the ball at {params.speed_mps:.1f} m/s for "
            f"{params.duration_s:.1f}s."
        )
        # Restore whatever avoidance was configured (the value the connection
        # applied at startup), not a blind "on" -- the operator may run with it
        # globally off. Seed the believed state from it so an already-off run
        # sends no toggle requests at all.
        restore_avoidance = self.config.g.obstacle_avoidance
        self._avoidance_enabled = restore_avoidance
        period = 1.0 / self.config.kick_loop_hz
        start = time.monotonic()
        try:
            # Let the charge reach the ball; avoidance would otherwise brake it.
            self._set_obstacle_avoidance(False)
            while not self._kick_stop.is_set():
                elapsed = time.monotonic() - start
                if elapsed >= params.duration_s:
                    break
                vx, wz = charge_velocity(params, elapsed)
                self.cmd_vel.publish(
                    Twist(
                        linear=Vector3(vx, 0.0, 0.0),
                        angular=Vector3(0.0, 0.0, wz),
                    )
                )
                self._kick_stop.wait(period)
        finally:
            # Always stop the dog and restore avoidance, even if interrupted.
            self.cmd_vel.publish(Twist.zero())
            self._set_obstacle_avoidance(restore_avoidance)
        interrupted = self._kick_stop.is_set()
        message = (
            "Kick interrupted; stopped the robot."
            if interrupted
            else f"Kicked the ball: charged forward at {params.speed_mps:.1f}"
            f" m/s for {params.duration_s:.1f}s."
        )
        self.kick_status.publish(message)
        return message

    def _set_obstacle_avoidance(self, enabled: bool) -> None:
        """Toggle the Go2's onboard obstacle avoidance via the connection.

        Skips the request when avoidance is already in the requested state
        (no wasted RTC round-trip), and is a no-op when no robot connection is
        injected (off-robot runs), so the kick still publishes ``cmd_vel`` in
        tests and simulation.

        Args:
            enabled: True restores avoidance; False disables it for the charge.
        """
        if self._connection is None or self._avoidance_enabled == enabled:
            return
        self._connection.publish_request(
            RTC_TOPIC["OBSTACLES_AVOID"],
            {"api_id": 1001, "parameter": {"enable": int(enabled)}},
        )
        self._avoidance_enabled = enabled


def _draw_overlay(
    image: Image,
    bbox: tuple[float, float, float, float] | None,
    label: str,
    color: tuple[int, int, int],
) -> Image:
    """Draw the tracked bbox and a label on a copy of the frame (BGR)."""
    frame = image.to_opencv().copy()
    if bbox is not None:
        x1, y1, x2, y2 = (int(round(v)) for v in bbox)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        text_y = y1 - 8 if y1 > 16 else y1 + 16
        cv2.putText(
            frame,
            label,
            (x1, text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
        )
    else:
        cv2.putText(
            frame,
            label,
            (12, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            color,
            2,
        )
    return Image.from_opencv(frame, ts=image.ts)


def _decode_base64_image(b64: str) -> Image:
    bgr_array = TurboJPEG().decode(base64.b64decode(b64))
    return Image(data=bgr_array, format=ImageFormat.BGR)
