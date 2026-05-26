"""DimOS skill container for user-described ball monitoring.

The monitor is perception-only: it does not plan, walk to the ball, or kick.
The user describes the ball, a VLM localizes it in the current image, EdgeTAM
tracks the selected object frame-to-frame, and the module publishes JSON status
updates with bbox, centering, and size metrics.

The decision logic (status transitions, reacquire timing) lives in the pure
``MonitorState``; this module performs the effects (run the tracker/VLM, publish
snapshots, draw debug frames). The control loop fails loudly -- any exception is
logged with a traceback and surfaced as an ``error`` status rather than letting
the worker thread die silently. When EdgeTAM drops a moving ball, a frame-motion
fallback re-seeds the tracker before escalating to the slow VLM reacquire.
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
from dimos.msgs.sensor_msgs.Image import Image, ImageFormat
from dimos.navigation.visual.query import get_object_bbox_from_image
from dimos.perception.detection.type.detection2d.bbox import Detection2DBBox
from dimos.utils.logging_config import setup_logger

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
from pawdribble.qwen_china import QwenChinaVlModel

logger = setup_logger()


class Config(ModuleConfig):
    """Configuration for the described-ball monitor."""

    loop_hz: float = 15.0
    max_lost_frames: int = 15  # frames without a mask before reacquiring
    reacquire_interval_frames: int = 15  # VLM retry cadence while lost
    max_reacquire_attempts: int = 5  # give up after this many failed retries
    motion_fallback: bool = True  # re-seed tracker from frame motion on a miss
    max_center_jump_frac: float = 2.5  # reject jumps beyond this x ball width
    max_area_factor: float = 4.0  # reject area changes beyond this factor
    stale_timeout_s: float = 2.0  # no fix longer than this -> "stale"


class BallMonitorSkillContainer(Module):
    """Monitor a described ball with VLM acquisition and EdgeTAM tracking."""

    config: Config

    color_image: In[Image]
    ball_status: Out[str]  # JSON status (LCM diagnostic stream)
    debug_image: Out[Image]  # annotated camera frame (shows in Rerun)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._lock = threading.RLock()
        self._stop = threading.Event()
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

    @rpc
    def start(self) -> None:
        super().start()
        self.register_disposable(
            Disposable(self.color_image.subscribe(self._on_color_image))
        )

    @rpc
    def stop(self) -> None:
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
            self._stop.clear()
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
        period = 1.0 / self.config.loop_hz
        next_time = time.monotonic()

        while not self._stop.is_set():
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
                self._stop.wait(sleep_duration)
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
        self._stop.set()
        with self._lock:
            thread = self._thread
            self._thread = None
        # Join outside the lock; the loop thread takes the lock each iteration.
        if thread is not None:
            thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)


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
