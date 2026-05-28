"""Multi-target identification: prompt a VL model for every match + parse it.

The single-box ``get_object_bbox_from_image`` grounding returns one box; the
greeter needs *all* matching subjects so it can pick one at random. This asks the
same VL model (via its duck-typed ``query(image, prompt)``) for a JSON list of
boxes and parses it leniently.

No DimOS import -- it works on any object with a ``query`` method and the parser
is pure, so both are unit tested without a model. The DimOS container injects the
real ``QwenChinaVlModel``.
"""

from __future__ import annotations

import json
import re

BBox = tuple[float, float, float, float]

_PROMPT = (
    "Look at this image and find EVERY {description}. Return ONLY a JSON array; "
    "each element is an object {{\"bbox\": [x1, y1, x2, y2]}} where x1,y1 is the "
    "top-left and x2,y2 the bottom-right corner in pixels. Return [] if there are "
    "none."
)


def build_prompt(description: str) -> str:
    """The multi-box detection prompt for a subject description."""
    return _PROMPT.format(description=description)


def _coerce_bbox(item: object) -> BBox | None:
    """Coerce one parsed element into a 4-float bbox, or None if it is not one."""
    if isinstance(item, dict):
        item = item.get("bbox") or item.get("box") or item.get("bbox_2d")
    if not isinstance(item, (list, tuple)) or len(item) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in item)
    except (TypeError, ValueError):
        return None
    return (x1, y1, x2, y2)


def parse_bboxes(response_text: str) -> list[BBox]:
    """Parse a VL model's response into a list of bboxes (lenient).

    Accepts a bare JSON array (of ``{"bbox": [...]}`` objects or of raw
    ``[x1, y1, x2, y2]`` lists), or an object wrapping such an array, with or
    without ``` fences. Anything unparseable yields an empty list.

    Args:
        response_text: Raw model output.

    Returns:
        The valid 4-number boxes found, in order; empty if none.
    """
    if not response_text:
        return []
    text = response_text.strip()
    # Strip an optional ```json ... ``` code fence.
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Fall back to the outermost JSON array/object if there is surrounding prose.
    if not text or text[0] not in "[{":
        match = re.search(r"[\[{].*[\]}]", text, re.DOTALL)
        if not match:
            return []
        text = match.group(0)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, dict):
        if _coerce_bbox(parsed) is not None:
            # A single detection object, e.g. {"bbox": [x1, y1, x2, y2]} -- the
            # dict itself is one box (some models answer with one despite the
            # multi-box prompt).
            items: list[object] = [parsed]
        else:
            # A wrapper, e.g. {"objects": [...]} / {"boxes": [[...], ...]}: take
            # its first list of detections.
            items = next(
                (v for v in parsed.values() if isinstance(v, list)), []
            )
    elif isinstance(parsed, list):
        items = parsed
    else:
        return []
    boxes = [_coerce_bbox(item) for item in items]
    return [box for box in boxes if box is not None]


def detect_all(vl_model: object, image: object, description: str) -> list[BBox]:
    """Query ``vl_model`` for every ``description`` in ``image`` and parse boxes.

    Args:
        vl_model: Anything with ``query(image, prompt) -> str`` (e.g. a DimOS
            ``VlModel``).
        image: The frame to pass through to the model.
        description: Visual description of the subject (e.g. "a person sitting
            on a chair").

    Returns:
        Every parsed bounding box; empty if none were found or returned.
    """
    response = vl_model.query(image, build_prompt(description))
    return parse_bboxes(response)
