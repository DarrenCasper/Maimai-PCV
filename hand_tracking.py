"""HSV-based hand blob tracking — no MediaPipe, no learned model.

Two selectable color models, picked with TRACK_MODE:

  "skin"  — bare skin tone in HSV. No gear needed, but the range is
            lighting- and camera-dependent: retune SKIN_HSV_RANGES per
            session with calibrate_hsv.py rather than trusting the
            defaults.
  "glove" — one solid HSV range per hand (GLOVE_HSV). Fill these in once
            you have colored gloves/tape — a distinct color per hand also
            makes handedness exact instead of a positional guess. Until
            both entries are filled in, "glove" mode falls back to
            GLOVE_HSV_SINGLE (one color, either hand, labeled the same
            positional way as "skin" mode).

detect_hands() reduces each hand to one blob (contour) and two numbers:
  - palm position: the blob's centroid (image moments), normalized 0..1.
  - fist / open: the blob's solidity (contour_area / hull_area). A closed
    fist is nearly convex; splayed fingers cut concave notches into the
    hull, which is what a fist has almost none of. Pure geometry, no
    landmarks.
"""

import cv2
import numpy as np

TRACK_MODE = "skin"  # "skin" or "glove"

# --- skin-tone model ---------------------------------------------------
# Two ranges because skin hue wraps around OpenCV's H axis (0-180): most
# skin tones sit near hue 0, but under warm/tungsten light or on darker
# skin the same tone can sit near hue 180. Tune both with calibrate_hsv.py
# for your camera/lighting/skin tone — these defaults are just a start.
SKIN_HSV_RANGES = [
    ((0, 20, 70), (20, 150, 255)),
    ((160, 20, 70), (180, 150, 255)),
]

# --- glove model ---------------------------------------------------
# Placeholder — fill in once you have colored gloves, e.g.:
#   GLOVE_HSV = {"Left":  ((100, 150, 50), (140, 255, 255)),   # blue
#                "Right": ((0, 150, 80), (10, 255, 255))}      # red
# Leave an entry None until then; "glove" mode falls back to
# GLOVE_HSV_SINGLE for hand(s) with no dedicated color.
GLOVE_HSV = {
    "Left": None,
    "Right": None,
}
GLOVE_HSV_SINGLE = ((100, 150, 50), (140, 255, 255))  # single fallback glove color

MIN_BLOB_AREA = 1500  # px^2 in the *detection-resolution* frame; drops noise specks
FIST_SOLIDITY = 0.88  # contour_area / hull_area at/above this reads as a closed fist
MORPH_KERNEL = np.ones((5, 5), np.uint8)

# Hand-label continuity: no color/model gives us true handedness in "skin"
# mode (or "glove" mode with only one color filled in), so a hand keeps
# whichever label ("Left"/"Right") was closest to it last frame, and a
# newly-appearing hand is labeled by screen position (left-to-right).
_prev_centers = {}


def _mask_for_ranges(hsv, ranges):
    mask = None
    for lo, hi in ranges:
        m = cv2.inRange(hsv, np.array(lo), np.array(hi))
        mask = m if mask is None else cv2.bitwise_or(mask, m)
    return mask


def _clean(mask):
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, MORPH_KERNEL)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, MORPH_KERNEL)
    return mask


def _is_fist(contour):
    hull_area = cv2.contourArea(cv2.convexHull(contour))
    if hull_area <= 0:
        return False
    return (cv2.contourArea(contour) / hull_area) >= FIST_SOLIDITY


def _largest_blobs(mask, limit):
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = [c for c in contours if cv2.contourArea(c) >= MIN_BLOB_AREA]
    contours.sort(key=cv2.contourArea, reverse=True)
    return contours[:limit]


def _centroid(contour, w, h):
    m = cv2.moments(contour)
    if m["m00"] == 0:
        return None
    return (m["m10"] / m["m00"] / w, m["m01"] / m["m00"] / h)


def _assign_labels(blobs):
    """blobs: [(cx, cy, contour), ...] -> {label: (cx, cy, contour)}.

    Greedily keeps each previously-seen label on whichever blob is now
    closest to where it last was, then hands out any leftover labels to
    leftover blobs by left-to-right screen position.
    """
    if not blobs:
        _prev_centers.clear()
        return {}

    remaining = list(blobs)
    assigned = {}
    for label, (px, py) in list(_prev_centers.items()):
        if not remaining:
            break
        remaining.sort(key=lambda b: (b[0] - px) ** 2 + (b[1] - py) ** 2)
        assigned[label] = remaining.pop(0)

    remaining.sort(key=lambda b: b[0])
    for label in ("Left", "Right"):
        if label in assigned or not remaining:
            continue
        assigned[label] = remaining.pop(0)

    _prev_centers.clear()
    _prev_centers.update({label: (b[0], b[1]) for label, b in assigned.items()})
    return assigned


def _detect_positional(hsv, ranges, w, h, limit=2):
    mask = _clean(_mask_for_ranges(hsv, ranges))
    blobs = []
    for c in _largest_blobs(mask, limit):
        centroid = _centroid(c, w, h)
        if centroid is not None:
            blobs.append((centroid[0], centroid[1], c))

    assigned = _assign_labels(blobs)
    return [
        {"label": label, "cx": cx, "cy": cy, "fist": _is_fist(c), "contour": c}
        for label, (cx, cy, c) in assigned.items()
    ]


def detect_hands(frame):
    """frame: BGR image, already resized to the detection resolution.

    Returns a list of up to 2 dicts: {"label", "cx", "cy", "fist", "contour"}.
    cx/cy are normalized 0..1 (same convention MediaPipe's landmarks used),
    so callers that map them back to full-res pixels don't need to change.
    """
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    if TRACK_MODE == "glove":
        dedicated = {k: v for k, v in GLOVE_HSV.items() if v is not None}
        if dedicated:
            hands = []
            for label, rng in dedicated.items():
                mask = _clean(_mask_for_ranges(hsv, [rng]))
                blobs = _largest_blobs(mask, limit=1)
                if not blobs:
                    continue
                centroid = _centroid(blobs[0], w, h)
                if centroid is None:
                    continue
                hands.append({
                    "label": label, "cx": centroid[0], "cy": centroid[1],
                    "fist": _is_fist(blobs[0]), "contour": blobs[0],
                })
            return hands
        return _detect_positional(hsv, [GLOVE_HSV_SINGLE], w, h)

    return _detect_positional(hsv, SKIN_HSV_RANGES, w, h)
