"""Glove tracking by HSV color. No MediaPipe, no trained model.

The gloves are light blue + orange with white trim. Steps for each frame:
  1. Shrink the frame (INTER_AREA averages pixels, which cancels camera noise)
     and convert it to HSV.
  2. Make a mask for each color with cv2.inRange (255 where the color matches)
     and remove stray dots with "opening".
  3. Join blue + orange (+ white pixels that touch them) into one mask, and
     "close" it so the patches of one glove fuse into a single blob.
  4. Find the blobs (contours) and take their bounding boxes.
  5. Keep a blob only if it contains BOTH blue and orange. This ignores a
     lone blue wall or an orange door.

detect_hands(frame) returns a list of boxes (x0, y0, x1, y1) in pixels of `frame`.
GloveTracker wraps it and keeps the last boxes for a moment if the gloves vanish,
so the box does not blink. Tune the ranges below with calibrate_hsv.py.
"""

import time

import cv2
import numpy as np

# HSV ranges as ((H min, S min, V min), (H max, S max, V max)).
# OpenCV hue is 0-180. Measured from a photo of the fuzzy glove:
#   light blue: hue ~93 (cyan-ish), pale (S ~90), shaded parts get dark -> low V min
#   orange:     hue ~19 (golden). Skin is hue 6-12, so H min 13 keeps skin out
#   white:      S under 20, but only V ~180 in the camera (not bright), and darker
#               when exposure drops -> low V min, so the white strip keeps the glove
#               in one piece (a higher V min made the box blink in tests)
# If the lighting changes, re-tune with calibrate_hsv.py.
BLUE = ((85, 40, 100), (110, 255, 255))
ORANGE = ((13, 100, 140), (26, 255, 255))
WHITE = ((0, 0, 110), (180, 45, 255))

DETECT_WIDTH = 320     # frames are shrunk to this width before detection
MAX_HANDS = 2          # most boxes returned (biggest first)
MIN_BLOB_AREA = 120    # ignore blobs smaller than this (pixels at DETECT_WIDTH)
MIN_COLOR_PIXELS = 12  # a color must cover this many pixels to count as "present"
MERGE_SIZE = 9         # closing kernel size: how big a gap between patches is bridged
WHITE_REACH = 5        # white only counts this close to blue/orange
BOX_PADDING = 10       # grow each box by this many pixels (full-size frame)
HOLD_SECONDS = 0.15    # if the gloves vanish, keep the last boxes this long


def color_mask(hsv, color):
    low, high = color
    mask = cv2.inRange(hsv, np.array(low), np.array(high))
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))   # drop stray dots


def detect_hands(frame):
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (DETECT_WIDTH, h * DETECT_WIDTH // w), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)

    blue = color_mask(hsv, BLUE)
    orange = color_mask(hsv, ORANGE)
    glove = cv2.bitwise_or(blue, orange)

    # white only counts right next to blue/orange, so white walls are ignored
    near = cv2.dilate(glove, np.ones((2 * WHITE_REACH + 1, 2 * WHITE_REACH + 1), np.uint8))
    white = cv2.bitwise_and(color_mask(hsv, WHITE), near)

    blob = cv2.bitwise_or(glove, white)
    blob = cv2.morphologyEx(blob, cv2.MORPH_CLOSE, np.ones((MERGE_SIZE, MERGE_SIZE), np.uint8))

    contours, _ = cv2.findContours(blob, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)

    scale = w / DETECT_WIDTH
    boxes = []
    for c in contours:
        if cv2.contourArea(c) < MIN_BLOB_AREA or len(boxes) == MAX_HANDS:
            break
        x, y, bw, bh = cv2.boundingRect(c)
        has_blue = cv2.countNonZero(blue[y:y + bh, x:x + bw]) >= MIN_COLOR_PIXELS
        has_orange = cv2.countNonZero(orange[y:y + bh, x:x + bw]) >= MIN_COLOR_PIXELS
        if has_blue and has_orange:
            x0 = max(int(x * scale) - BOX_PADDING, 0)
            y0 = max(int(y * scale) - BOX_PADDING, 0)
            x1 = min(int((x + bw) * scale) + BOX_PADDING, w)
            y1 = min(int((y + bh) * scale) + BOX_PADDING, h)
            boxes.append((x0, y0, x1, y1))
    return boxes


class GloveTracker:
    """detect_hands() plus a short memory, so one bad frame doesn't make the box blink."""

    def __init__(self):
        self.boxes = []
        self.last_seen = 0.0

    def update(self, frame):
        found = detect_hands(frame)
        now = time.perf_counter()
        if found:
            self.boxes = found
            self.last_seen = now
        elif now - self.last_seen > HOLD_SECONDS:
            self.boxes = []
        return self.boxes
