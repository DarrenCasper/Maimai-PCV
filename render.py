"""Everything drawn on screen, using plain OpenCV drawing calls.

The board outline is drawn once at start-up (BOARD_LINES); each frame only
copies it onto the scene. Colors are BGR, as everywhere in OpenCV.
"""

import math

import cv2
import numpy as np

from board import C_RADIUS, CENTER, FRAME_H, FRAME_W, RING_LAYERS, TARGET_RADIUS, zone_center

FONT = cv2.FONT_HERSHEY_DUPLEX
AA = cv2.LINE_AA
WHITE = (255, 255, 255)
BOX_COLOR = (90, 255, 120)
NOTE_COLORS = {
    "TAP": (180, 105, 255),        # pink
    "HOLD": (0, 190, 255),         # amber
    "TOUCH": (120, 230, 90),       # green
    "TOUCH_HOLD": (120, 230, 90),
    "SLIDE": (255, 170, 40),       # blue
}
TOUCH_KINDS = ("TOUCH", "TOUCH_HOLD")
START_RADIUS = 14      # a lane note starts this small at the board center
APPROACH_PX = 100      # a touch note's ring starts this far outside its target


# --- background -----------------------------------------------------------
def fit_cover(img):
    """Scale + center-crop an image to exactly FRAME_W x FRAME_H (no stretching)."""
    h, w = img.shape[:2]
    scale = max(FRAME_W / w, FRAME_H / h)
    img = cv2.resize(img, (round(w * scale), round(h * scale)))
    x = (img.shape[1] - FRAME_W) // 2
    y = (img.shape[0] - FRAME_H) // 2
    return img[y:y + FRAME_H, x:x + FRAME_W]


def load_backdrop(path):
    """The level's artwork, darkened so the board stays readable.
    Falls back to a plain dark background if the file is missing."""
    if not path.exists():
        return np.full((FRAME_H, FRAME_W, 3), (40, 30, 25), np.uint8)
    # imdecode instead of imread so non-ASCII folder names (Japanese) work
    art = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    return cv2.convertScaleAbs(fit_cover(art), alpha=0.3)


# --- board (drawn once) -----------------------------------------------------
R = RING_LAYERS[-1]["r_max"] + 10    # half-size of the square around the board (lane buttons stick out a bit)
BOARD_X0, BOARD_Y0 = CENTER[0] - R, CENTER[1] - R


def wedge_points(inner, outer, start_deg, end_deg):
    """Outline of a ring slice: along the outer arc, then back along the inner arc."""
    angles = np.radians(np.linspace(start_deg, end_deg, 12))

    def arc(radius):
        return np.stack([R + radius * np.cos(angles), R + radius * np.sin(angles)], axis=1)

    return np.vstack([arc(outer), arc(inner)[::-1]]).astype(np.int32)


def build_board():
    lines = np.zeros((2 * R, 2 * R, 3), np.uint8)
    for layer in RING_LAYERS:
        for i in range(8):
            start = layer["angle_offset"] + i * 45
            pts = wedge_points(layer["r_min"], layer["r_max"], start, start + 45)
            cv2.polylines(lines, [pts], True, WHITE, 2, AA)
    cv2.circle(lines, (R, R), C_RADIUS, WHITE, 2, AA)
    cv2.line(lines, (R, R - C_RADIUS), (R, R + C_RADIUS), WHITE, 2, AA)
    # the 8 lane buttons on the outer ring, where lane notes land
    for k in range(1, 9):
        x, y = zone_center(f"D{k}")
        cv2.circle(lines, (x - BOARD_X0, y - BOARD_Y0), TARGET_RADIUS, WHITE, 2, AA)
    return lines


BOARD_LINES = build_board()


def draw_board(frame):
    roi = frame[BOARD_Y0:BOARD_Y0 + 2 * R, BOARD_X0:BOARD_X0 + 2 * R]
    cv2.max(roi, BOARD_LINES, roi)      # white outlines on top of the scene


# --- notes and gloves ------------------------------------------------------------
def draw_note(frame, xy, radius, color):
    """A maimai-style note: a thick colored ring with a white edge on each side."""
    cv2.circle(frame, xy, radius, WHITE, 3, AA)
    cv2.circle(frame, xy, radius - 5, color, 8, AA)
    cv2.circle(frame, xy, radius - 11, WHITE, 2, AA)


def draw_star(frame, xy, radius, color):
    """A 5-point star: points alternate between the outer and a smaller inner radius."""
    points = []
    for i in range(10):
        r = radius if i % 2 == 0 else radius * 0.45
        angle = math.radians(-90 + i * 36)
        points.append((xy[0] + r * math.cos(angle), xy[1] + r * math.sin(angle)))
    points = np.array(points, np.int32)
    cv2.fillPoly(frame, [points], color, AA)
    cv2.polylines(frame, [points], True, WHITE, 3, AA)


def point_on_path(path, fraction):
    """The (x, y) point a fraction (0..1) of the way along the lines joining `path`."""
    lengths = [math.dist(a, b) for a, b in zip(path, path[1:])]
    remaining = fraction * sum(lengths)
    for a, b, length in zip(path, path[1:], lengths):
        if length and remaining <= length:      # skip zero-length segments (loop slides)
            f = remaining / length
            return (int(a[0] + (b[0] - a[0]) * f), int(a[1] + (b[1] - a[1]) * f))
        remaining -= length
    return path[-1]


def draw_notes(frame, notes):
    """Lane notes fly from the board center out to their lane button, growing as
    they go. A slide's star then travels along its path to the end lane. Touch
    notes stay put: a ring shrinks onto the target circle."""
    for n in notes:
        if n["judged"] is not None:
            continue
        color = NOTE_COLORS.get(n["kind"], WHITE)
        p = n["progress"]
        if n["kind"] in TOUCH_KINDS:
            cv2.circle(frame, n["xy"], TARGET_RADIUS, color, 2, AA)
            cv2.circle(frame, n["xy"], int(TARGET_RADIUS + APPROACH_PX * (1 - p)), color, 3, AA)
            continue

        first_spot = n["path"][0] if n["path"] else n["xy"]
        radius = int(START_RADIUS + (TARGET_RADIUS - START_RADIUS) * p)
        if n["path"]:
            cv2.polylines(frame, [np.array(n["path"], np.int32)], False, color, 4, AA)
        if p < 1:       # still flying out from the center to the first spot
            x = int(CENTER[0] + (first_spot[0] - CENTER[0]) * p)
            y = int(CENTER[1] + (first_spot[1] - CENTER[1]) * p)
            pos = (x, y)
        elif n["path"]:
            pos = point_on_path(n["path"], n["travel"])
        else:
            pos = first_spot
        if n["path"]:
            draw_star(frame, pos, radius, color)
        else:
            draw_note(frame, pos, radius, color)


def draw_boxes(frame, boxes, thickness=3):
    for x0, y0, x1, y1 in boxes:
        cv2.rectangle(frame, (x0, y0), (x1, y1), BOX_COLOR, thickness)


def draw_preview(frame, camera_img, boxes):
    """Small live camera picture (bottom-left) so you can see yourself."""
    w, h = 256, 144
    x, y = 20, FRAME_H - h - 20
    small = cv2.resize(camera_img, (w, h))
    scaled = [tuple(int(v * w / FRAME_W) for v in b) for b in boxes]
    draw_boxes(small, scaled, thickness=1)
    frame[y:y + h, x:x + w] = small
    cv2.rectangle(frame, (x - 1, y - 1), (x + w, y + h), WHITE, 2)


# --- text / HUD ---------------------------------------------------------------
def put_text(frame, text, x, y, scale=0.8, color=WHITE, align="left"):
    """Text with a dark outline so it reads on any background."""
    width = cv2.getTextSize(text, FONT, scale, 2)[0][0]
    if align == "right":
        x -= width
    elif align == "center":
        x -= width // 2
    cv2.putText(frame, text, (x, y), FONT, scale, (0, 0, 0), 5, AA)
    cv2.putText(frame, text, (x, y), FONT, scale, color, 2, AA)


def draw_hud(frame, mgr, song_ms, fps):
    right = FRAME_W - 30
    put_text(frame, f"FPS {fps:.0f}", right, FRAME_H - 20, 0.5, (200, 200, 200), "right")
    if mgr is None:
        return
    put_text(frame, f"SCORE {mgr.score}", right, 50, 1.0, WHITE, "right")
    put_text(frame, f"ACC {mgr.accuracy() * 100:5.1f}%", right, 85, 0.7, (200, 200, 200), "right")
    if mgr.combo >= 3:
        put_text(frame, str(mgr.combo), right, 190, 1.8, (0, 255, 255), "right")
        put_text(frame, "COMBO", right, 220, 0.7, (0, 255, 255), "right")
    if mgr.last_judgment:
        text, at = mgr.last_judgment
        if 0 <= song_ms - at < 600:
            color = (0, 255, 140) if text == "PERFECT" else (60, 60, 255)
            put_text(frame, text, right, 290, 1.1, color, "right")


def draw_results(frame, mgr):
    x0, y0, x1, y1 = FRAME_W // 2 - 240, FRAME_H // 2 - 130, FRAME_W // 2 + 240, FRAME_H // 2 + 130
    panel = frame[y0:y1, x0:x1]
    cv2.addWeighted(panel, 0.3, np.zeros_like(panel), 0.7, 0, panel)    # dark panel
    lines = [
        "RESULTS",
        f"Perfect {mgr.counts['PERFECT']}   Miss {mgr.counts['MISS']}",
        f"Max combo {mgr.max_combo}",
        f"Score {mgr.score}   Acc {mgr.accuracy() * 100:.2f}%",
        "press q to quit",
    ]
    for i, line in enumerate(lines):
        put_text(frame, line, FRAME_W // 2, y0 + 50 + i * 46, 0.9 if i else 1.2, WHITE, "center")
