import time

import cv2
import numpy as np

BLUE = ((85, 40, 100), (110, 255, 255))
ORANGE = ((13, 100, 140), (26, 255, 255))
WHITE = ((0, 0, 110), (180, 45, 255))

DETECT_WIDTH = 320
MAX_HANDS = 2
MIN_BLOB_AREA = 120
MIN_COLOR_PIXELS = 12
MERGE_SIZE = 9
WHITE_REACH = 5
BOX_PADDING = 10
HOLD_SECONDS = 0.15


def color_mask(hsv, color):
    low, high = color
    mask = cv2.inRange(hsv, np.array(low), np.array(high))
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))


def detect_hands(frame):
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (DETECT_WIDTH, h * DETECT_WIDTH // w), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)

    blue = color_mask(hsv, BLUE)
    orange = color_mask(hsv, ORANGE)
    glove = cv2.bitwise_or(blue, orange)

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
