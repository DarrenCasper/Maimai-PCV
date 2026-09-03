import cv2
import mediapipe as mp
import numpy as np
import time
import math
import threading
from pathlib import Path

import pygame

from chart_parser import parse_chart
from maidata import get_events, load_maidata
from rhythm import NoteManager


def test_chart_parser():
    """Sanity-check chart_parser.parse_chart: parse a sample chart mixing ring
    taps/holds, touch notes and slides, and print the event list. Flip
    RUN_PARSER_TEST."""
    chart_text = "1,,A1,,{8}1-3[4:1],,C1h[4:1],,,4h[4:1],,1/4>6[8:3],"
    song_bpm = 120
    events = parse_chart(chart_text, bpm=song_bpm)
    print(f"{len(events)} events parsed (bpm={song_bpm}):")
    for e in events:
        if e["type"] == "SLIDE":
            what = f"{e['start']} {e['shape']} {e['end']}  ({e['duration_ms']} ms)"
        elif "duration_ms" in e:
            what = f"pos {e['pos']}  hold {e['duration_ms']} ms"
        else:
            what = f"pos {e['pos']}"
        print(f"  {e['time_ms']:>6} ms   {e['type']:<10}  {what}")
    return events


RUN_PARSER_TEST = False   # True = just run the chart-parser check and exit;
                          # False = run the normal camera app
if RUN_PARSER_TEST:
    test_chart_parser()
    raise SystemExit(0)


# --- hit zone layout ------------------------------------------------------
FRAME_W, FRAME_H = 1280, 720
CENTER = (FRAME_W // 2, FRAME_H // 2)

# Ring layers, innermost to outermost. angle_offset rotates that layer's
# 8 slices relative to 0deg (east/right) — alternating 0/22.5 creates the
# interlocking look where each ring's seams sit between the ring inside it.
RING_LAYERS = [
    {"label": "B", "r_min": 40,  "r_max": 110, "angle_offset": 22.5},
    {"label": "E", "r_min": 110, "r_max": 180, "angle_offset": 0},
    {"label": "A", "r_min": 180, "r_max": 250, "angle_offset": 22.5},
    {"label": "D", "r_min": 250, "r_max": 320, "angle_offset": 0},
]
C_RADIUS = 40

# The palm isn't a point — treat it as a disc this many pixels across when
# scanning for hits. Bigger = more forgiving; it's fine (intended) for the
# disc to straddle 2-3 zones and register all of them at once.
PALM_HIT_RADIUS = 70

# --- rhythm game -------------------------------------------------------------
PLAY_CHART = True            # False = free-play tracking, no song / notes
LEVEL_NAME = "メズマライザー"
DIFFICULTY = 2               # 2 Basic 3 Advanced 4 Expert 5 Master 6 Re:Master
AUDIO_OFFSET_MS = 0          # +ve = notes judged later; tune by ear

# maimai-style note speed. Higher = notes travel faster / spend less time on
# screen. ~4-5 is relaxed, 6.0-7.5 is what most maimai players use. This only
# changes how early a note appears, never the timing windows.
NOTE_SPEED = 5.0
NOTE_LEAD_MS = int(3600 / NOTE_SPEED)   # ~720ms at 5.0, ~576ms at 6.25


def zone_containing(px, py):
    """Returns the string id of the zone a point falls in, or None."""
    dx, dy = px - CENTER[0], py - CENTER[1]
    r = math.hypot(dx, dy)

    if r <= C_RADIUS:
        return "C1" if px < CENTER[0] else "C2"

    theta = math.degrees(math.atan2(dy, dx)) % 360
    for layer in RING_LAYERS:
        if layer["r_min"] <= r < layer["r_max"]:
            slice_theta = (theta - layer["angle_offset"]) % 360
            idx = int(slice_theta // 45)
            return f"{layer['label']}{idx + 1}"

    return None  # beyond the board entirely


def zones_touching(px, py, radius=PALM_HIT_RADIUS):
    """Every zone id a disc of `radius` at (px, py) overlaps.

    Cheap approximation: probe the centre plus two rings of sample points
    and union whatever zones they land in.
    """
    hits = set()
    z = zone_containing(px, py)
    if z:
        hits.add(z)
    for rr in (radius * 0.55, radius):
        for k in range(12):
            ang = math.radians(k * 30)
            z = zone_containing(px + rr * math.cos(ang), py + rr * math.sin(ang))
            if z:
                hits.add(z)
    return hits


def draw_wedge(frame, center, inner_r, outer_r, center_angle, span_deg, color, thickness=2):
    start = center_angle - span_deg / 2
    end = center_angle + span_deg / 2
    cv2.ellipse(frame, center, (outer_r, outer_r), 0, start, end, color, thickness)
    cv2.ellipse(frame, center, (inner_r, inner_r), 0, start, end, color, thickness)
    for ang in (start, end):
        rad = math.radians(ang)
        p1 = (int(center[0] + inner_r * math.cos(rad)), int(center[1] + inner_r * math.sin(rad)))
        p2 = (int(center[0] + outer_r * math.cos(rad)), int(center[1] + outer_r * math.sin(rad)))
        cv2.line(frame, p1, p2, color, thickness)


def draw_board(frame):
    for layer in RING_LAYERS:
        for i in range(8):
            # +22.5 so the wedge sits centred on the slice, not on its seam
            center_angle = layer["angle_offset"] + i * 45 + 22.5
            draw_wedge(frame, CENTER, layer["r_min"], layer["r_max"],
                       center_angle, span_deg=45, color=(255, 255, 255))
    cv2.circle(frame, CENTER, C_RADIUS, (255, 255, 255), 2)
    cv2.line(frame, (CENTER[0], CENTER[1] - C_RADIUS),
             (CENTER[0], CENTER[1] + C_RADIUS), (255, 255, 255), 2)


# The board never changes — render it once to a template, then each frame
# just stamp its white pixels on with cv2.max instead of ~200 draw calls.
BOARD_IMG = np.zeros((FRAME_H, FRAME_W, 3), np.uint8)
draw_board(BOARD_IMG)


def stamp_board(frame):
    if frame.shape == BOARD_IMG.shape:
        cv2.max(frame, BOARD_IMG, frame)
    else:  # unexpected capture size — fall back to live drawing
        draw_board(frame)


def _wedge_polygon(inner_r, outer_r, start_deg, end_deg, step=4):
    """Point list tracing an annular wedge, for fillPoly."""
    pts = []
    a = start_deg
    while a < end_deg:
        rad = math.radians(a)
        pts.append((CENTER[0] + outer_r * math.cos(rad), CENTER[1] + outer_r * math.sin(rad)))
        a += step
    rad = math.radians(end_deg)
    pts.append((CENTER[0] + outer_r * math.cos(rad), CENTER[1] + outer_r * math.sin(rad)))
    a = end_deg
    while a > start_deg:
        rad = math.radians(a)
        pts.append((CENTER[0] + inner_r * math.cos(rad), CENTER[1] + inner_r * math.sin(rad)))
        a -= step
    rad = math.radians(start_deg)
    pts.append((CENTER[0] + inner_r * math.cos(rad), CENTER[1] + inner_r * math.sin(rad)))
    return np.array(pts, np.int32)


def fill_zone(img, zone_id, color):
    """Solid fill of one zone (drawn onto an overlay, then blended once)."""
    if zone_id in ("C1", "C2"):
        a0, a1 = (90, 270) if zone_id == "C1" else (-90, 90)
        cv2.ellipse(img, CENTER, (C_RADIUS, C_RADIUS), 0, a0, a1, color, -1)
    else:
        label, idx = zone_id[0], int(zone_id[1:]) - 1
        layer = next(l for l in RING_LAYERS if l["label"] == label)
        start = layer["angle_offset"] + idx * 45
        poly = _wedge_polygon(layer["r_min"], layer["r_max"], start, start + 45)
        cv2.fillPoly(img, [poly], color)


def zone_center(zone_id):
    """Pixel centre of a zone — where its incoming note marker sits."""
    if zone_id in ("C1", "C2"):
        return (CENTER[0] + (-20 if zone_id == "C1" else 20), CENTER[1])
    label, idx = zone_id[0], int(zone_id[1:]) - 1
    layer = next(l for l in RING_LAYERS if l["label"] == label)
    mid_r = (layer["r_min"] + layer["r_max"]) / 2
    ang = math.radians(layer["angle_offset"] + idx * 45 + 22.5)
    return (int(CENTER[0] + mid_r * math.cos(ang)),
            int(CENTER[1] + mid_r * math.sin(ang)))


NOTE_COLORS = {
    "TAP": (0, 200, 255), "HOLD": (0, 200, 255),
    "TOUCH": (255, 150, 0), "TOUCH_HOLD": (255, 150, 0),
    "SLIDE": (255, 60, 210),
}


def draw_notes(frame, notes):
    """Incoming notes as approach circles that shrink onto their zone."""
    for n in notes:
        cx, cy = zone_center(n["zone"])
        p = n["progress"]
        col = NOTE_COLORS.get(n["kind"], (255, 255, 255))
        cv2.circle(frame, (cx, cy), int(30 + 95 * (1 - p)), col, 2, cv2.LINE_AA)
        if p > 0.82:  # about to be due — solid target
            cv2.circle(frame, (cx, cy), 26, col, -1 if p > 0.96 else 2, cv2.LINE_AA)


# mediapipe 1.x removed the legacy `mp.solutions.*` API.
# Hand tracking now goes through the Tasks API (HandLandmarker).
BaseOptions = mp.tasks.BaseOptions
HandLandmarker = mp.tasks.vision.HandLandmarker
HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
VisionRunningMode = mp.tasks.vision.RunningMode

# The "hit button" is the middle of the palm, not a fingertip — it stays put
# while the hand clenches into a fist. Average these landmarks to get it:
# wrist + the 4 finger knuckles (MCPs).
PALM_IDS = [0, 5, 9, 13, 17]

# --- smoothing / prediction ---------------------------------------------------
# Measured on this machine: ~25 hand detections/sec, ~55ms mean (75ms p90)
# capture->result latency. So the marker is drawn from data 2-3 frames old;
# the fix is to project it forward along the hand's velocity.
#
#   MIN_CUTOFF up   -> less lag everywhere, but trembles at rest come back
#   BETA       up   -> opens the filter sooner as the hand speeds up
#   D_CUTOFF   up   -> velocity estimate reacts faster (but noisier, and the
#                      projection multiplies that noise into the position)
MIN_CUTOFF = 1.0
BETA = 9.0
D_CUTOFF = 8.0
DECEL_SNAP = 5.0      # how much faster the velocity estimate drops vs rises

# How much of the (capture -> now) gap to cancel by projecting the palm along
# its velocity. 1.0 = aim the marker at the hand's true *current* position
# (tightest on fast moves, but can fling past when you stop suddenly). Lower
# it toward 0.5 if the marker overshoots; raise toward 1.0 if it still lags.
# Watch the grey debug dot (raw) vs the green one (projected) to tune.
LATENCY_COMP = 0.85
PREDICT_S = 0.0
MAX_EXTRAP_S = 0.09   # cap the projection window (~ the p90 pipeline latency)

# Debug: also draw the raw (unprojected) detection as a small grey dot, so you
# can see how much lead the prediction is adding and whether it overshoots.
SHOW_TRACKING_DEBUG = True


class OneEuroFilter:
    """One-Euro filter with decoupled update() / predict().

    update() folds in a fresh measurement — call it ONCE per new detection,
    never on a reused/stale result, or the velocity estimate collapses.
    predict(t) returns the position extrapolated to time t — call it every
    render frame, so the dot keeps gliding between detections.
    """

    def __init__(self, min_cutoff=1.0, beta=0.0, d_cutoff=1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.x_hat = None       # smoothed position
        self.dx_hat = 0.0       # smoothed velocity, units/sec
        self.t_hat = None       # capture time of the last measurement
        self._x_raw_prev = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def update(self, x, t):
        if self.x_hat is None or self.t_hat is None or t - self.t_hat > 0.3:
            self.x_hat, self.t_hat, self._x_raw_prev = x, t, x
            self.dx_hat = 0.0
            return
        dt = t - self.t_hat
        if dt <= 0.0:
            dt = 1e-3

        dx = (x - self._x_raw_prev) / dt
        speeding_up = abs(dx) >= abs(self.dx_hat) and dx * self.dx_hat >= 0.0
        # Ramp the velocity estimate UP smoothly (noise control) but let it
        # fall FAST when the hand slows or reverses — that's what stops the
        # projected marker from flying past the target when you stop.
        d_cut = self.d_cutoff if speeding_up else self.d_cutoff * DECEL_SNAP
        a_d = self._alpha(d_cut, dt)
        self.dx_hat = a_d * dx + (1.0 - a_d) * self.dx_hat

        cutoff = self.min_cutoff + self.beta * abs(self.dx_hat)
        a = self._alpha(cutoff, dt)
        self.x_hat = a * x + (1.0 - a) * self.x_hat

        self.t_hat = t
        self._x_raw_prev = x

    def predict(self, t):
        if self.x_hat is None:
            return None
        gap = min(max(t - self.t_hat, 0.0), MAX_EXTRAP_S)
        return self.x_hat + self.dx_hat * (gap * LATENCY_COMP + PREDICT_S)

# Per-hand gesture state, keyed by handedness ("Left" / "Right"), so two
# hands are tracked independently.
hand_state = {}


def get_hand_state(label):
    if label not in hand_state:
        hand_state[label] = {
            "was_fist": False,
            "flash_zones": set(),   # zones that were just hit
            "flash_until": 0.0,     # perf_counter time the hit flash ends
            "hold_start": None,     # perf_counter time the current fist began
            "hold_zones": set(),    # zones the fist landed on when it started
            "fx": OneEuroFilter(MIN_CUTOFF, BETA, D_CUTOFF),
            "fy": OneEuroFilter(MIN_CUTOFF, BETA, D_CUTOFF),
        }
    return hand_state[label]


def is_fist(hand_landmarks):
    """True when a majority of fingers are curled toward the wrist.

    hand_landmarks is the Tasks-API list of 21 NormalizedLandmark objects
    (indexed directly, unlike the old `.landmark[i]` API).
    """
    wrist = hand_landmarks[0]

    finger_tips = [8, 12, 16, 20]   # index, middle, ring, pinky tips
    finger_pips = [6, 10, 14, 18]   # their corresponding middle knuckles

    curled_count = 0
    for tip_id, pip_id in zip(finger_tips, finger_pips):
        tip = hand_landmarks[tip_id]
        pip = hand_landmarks[pip_id]

        dist_tip = math.hypot(tip.x - wrist.x, tip.y - wrist.y)
        dist_pip = math.hypot(pip.x - wrist.x, pip.y - wrist.y)

        if dist_tip < dist_pip:
            curled_count += 1

    return curled_count >= 3  # majority curled = call it a fist


def palm_center(hand_landmarks):
    """Normalized (x, y) of the palm middle, averaged over PALM_IDS."""
    xs = sum(hand_landmarks[i].x for i in PALM_IDS) / len(PALM_IDS)
    ys = sum(hand_landmarks[i].y for i in PALM_IDS) / len(PALM_IDS)
    return xs, ys


# Feed MediaPipe a smaller image than we display. Landmarks are normalized
# (0..1) so they map straight back onto the full-res frame, and the model
# runs a lot faster on fewer pixels. Lower = more detections per second
# (less gap to extrapolate across on fast moves) at a small hit to how
# precisely each landmark is placed.
DETECT_WIDTH = 320

# ---------------------------------------------------------------------------
# LIVE_STREAM mode: detection runs asynchronously on a worker thread and
# results arrive via this callback. The main loop never blocks on inference,
# so display FPS is limited only by the camera, not by the model.
# ---------------------------------------------------------------------------
latest_result = None
latest_result_ts = 0
result_seq = 0            # bumped on every callback, so the loop can tell
                          # a genuinely new detection from a reused one


def on_result(result, output_image, timestamp_ms):
    global latest_result, latest_result_ts, result_seq
    latest_result = result
    latest_result_ts = timestamp_ms
    result_seq += 1


options = HandLandmarkerOptions(
    base_options=BaseOptions(model_asset_path="hand_landmarker.task"),
    running_mode=VisionRunningMode.LIVE_STREAM,
    num_hands=2,
    min_hand_detection_confidence=0.6,
    # lower -> MediaPipe stays in cheap frame-to-frame tracking longer and
    # re-runs the expensive palm detector less often -> lower average latency
    min_tracking_confidence=0.5,
    result_callback=on_result,
)
landmarker = HandLandmarker.create_from_options(options)

CAMERA_INDEX = 1  # whatever index Camo landed on for you

# Motion blur is the other half of fast-tracking accuracy: a blurred hand
# gives the model a fuzzy, mis-placed landmark. Force a short exposure so
# each frame is crisp. Set to None to leave the camera on auto. Values are
# camera-specific (MSMF exposure is usually log2 seconds, so -6 ~= 1/64s).
# If you use Camo, its own in-app exposure/shutter control is more reliable.
MANUAL_EXPOSURE = None


class CameraStream:
    """Grabs frames on a background thread and only ever keeps the newest one.

    cap.read() decodes MJPEG and blocks; doing it off the main thread stops
    that latency (and its jitter) from stalling the display loop.
    """

    def __init__(self, index):
        self.cap = cv2.VideoCapture(index, cv2.CAP_MSMF)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        self.cap.set(cv2.CAP_PROP_FPS, 60)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if MANUAL_EXPOSURE is not None:
            self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)  # 0.25 = manual (MSMF)
            self.cap.set(cv2.CAP_PROP_EXPOSURE, MANUAL_EXPOSURE)
        self.frame = None
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while self.running:
            ok, f = self.cap.read()
            if ok:
                self.frame = f          # atomic reference swap, no lock needed
            else:
                time.sleep(0.005)

    def read(self):
        return self.frame

    def release(self):
        self.running = False
        self.thread.join(timeout=1.0)
        self.cap.release()


cam = CameraStream(CAMERA_INDEX)
print(f"Opening camera index {CAMERA_INDEX}... a window 'Hand Tracking Test' "
      f"should appear. Press 'q' in that window to quit.")

# --- load the chart + song, if playing --------------------------------------
note_mgr = None
song_playing = False
song_start_perf = 0.0
if PLAY_CHART:
    level = Path(__file__).parent / "levels" / LEVEL_NAME
    fields = load_maidata(level / "maidata.txt")
    note_mgr = NoteManager(get_events(fields, DIFFICULTY), lead_ms=NOTE_LEAD_MS)
    pygame.mixer.init()
    pygame.mixer.music.load(str(level / "track.mp3"))
    print(f"chart: {fields.get('title')} [slot {DIFFICULTY}] — {note_mgr.total} notes")
    pygame.mixer.music.play()
    song_start_perf = time.perf_counter()
    song_playing = True


def song_ms_now():
    """Audio position in ms (with offset), or None if not playing.

    pygame's get_pos() returns -1 once the track finishes; fall back to a
    wall clock so the last notes still resolve and the results screen shows.
    """
    if not song_playing:
        return None
    pos = pygame.mixer.music.get_pos()
    if pos < 0:
        pos = int((time.perf_counter() - song_start_perf) * 1000)
    return pos + AUDIO_OFFSET_MS


start = time.perf_counter()
last_ts = -1
last_frame = None
last_seq = -1            # result_seq we last folded into the filters
warned_no_cam = False

fps = 0.0
prev_time = time.perf_counter()

while True:
    frame = cam.read()
    if frame is None:
        if not warned_no_cam and time.perf_counter() - start > 3.0:
            print(f"Still no frames from camera index {CAMERA_INDEX} after 3s. "
                  f"Is it the right index? Is Camo actually streaming? "
                  f"Try a different CAMERA_INDEX.")
            warned_no_cam = True
        time.sleep(0.01)
        continue

    frame = cv2.flip(frame, 1)
    h, w, _ = frame.shape

    # --- hand off every *new* camera frame to the async detector ----------
    # MediaPipe's LIVE_STREAM graph keeps only one frame in flight and drops
    # the rest, so submitting eagerly just means it always works on the
    # freshest frame -> lowest possible hand-tracking latency.
    is_new = frame is not last_frame
    last_frame = frame
    if is_new:
        small = cv2.resize(frame, (DETECT_WIDTH, int(h * DETECT_WIDTH / w)))
        rgb_small = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_small)

        ts = int((time.perf_counter() - start) * 1000)
        if ts <= last_ts:
            ts = last_ts + 1
        last_ts = ts
        landmarker.detect_async(mp_image, ts)

    # --- draw the hit zones ---------------------------------------------
    stamp_board(frame)

    # --- advance the chart, draw incoming notes ------------------------
    song_ms = song_ms_now()
    if note_mgr is not None and song_ms is not None:
        draw_notes(frame, note_mgr.update(song_ms))

    # --- process the most recent result we have -------------------------
    result = latest_result
    now = time.perf_counter()
    fresh = result_seq != last_seq          # a detection we haven't folded in yet
    last_seq = result_seq
    # perf_counter time the freshly-arrived frame was actually captured
    sample_t = start + latest_result_ts / 1000.0

    hands_to_draw = []       # (px, py, fist, label) collected in pass 1
    overlay = None           # lazily-copied layer for all zone fills

    if result and result.hand_landmarks:
        for i, hand_landmarks in enumerate(result.hand_landmarks):
            # handedness is flipped because we mirrored the frame above
            raw = result.handedness[i][0].category_name
            label = "Left" if raw == "Right" else "Right"
            state = get_hand_state(label)

            # fold a new detection into the filters ONCE; on reused results
            # just keep extrapolating from what we already have
            if fresh:
                cx, cy = palm_center(hand_landmarks)
                state["fx"].update(cx, sample_t)
                state["fy"].update(cy, sample_t)
                state["fist"] = is_fist(hand_landmarks)
                state["raw_xy"] = (cx, cy)

            fx = state["fx"].predict(now)
            fy = state["fy"].predict(now)
            if fx is None:
                continue

            px = int(min(max(fx, 0.0), 1.0) * w)
            py = int(min(max(fy, 0.0), 1.0) * h)

            fist = state.get("fist", False)

            # the smoothed palm disc is the hit button — it can cover
            # several zones at once and every one of them counts
            hit_zones = zones_touching(px, py)

            if fist and not state["was_fist"]:
                if hit_zones:
                    state["flash_zones"] = set(hit_zones)
                    state["flash_until"] = now + 0.15
                    state["hold_start"] = now
                    state["hold_zones"] = set(hit_zones)
                    if note_mgr is not None and song_ms is not None:
                        note_mgr.judge(hit_zones, song_ms)
                    else:
                        print(f"HIT {sorted(hit_zones)} ({label})")

            elif not fist and state["was_fist"] and state["hold_start"] is not None:
                if note_mgr is None:
                    duration = now - state["hold_start"]
                    print(f"RELEASE after {duration:.2f}s in "
                          f"{sorted(state['hold_zones'])} ({label})")
                state["hold_start"] = None

            state["was_fist"] = fist

            # stage zone fills onto one shared overlay (blended once, below):
            # green while clenched, orange on hover, white flash after a hit
            zone_color = (0, 255, 0) if fist else (0, 180, 255)
            fill_ids = {z: zone_color for z in hit_zones}
            if now < state["flash_until"]:
                for z in state["flash_zones"]:
                    fill_ids[z] = (255, 255, 255)
            if fill_ids:
                if overlay is None:
                    overlay = frame.copy()
                for z, c in fill_ids.items():
                    fill_zone(overlay, z, c)

            raw = state.get("raw_xy")
            raw_px = (int(raw[0] * w), int(raw[1] * h)) if raw else None
            hands_to_draw.append((px, py, fist, label, raw_px))

    # one blend for every highlight this frame, then draw the palm markers
    if overlay is not None:
        cv2.addWeighted(overlay, 0.4, frame, 0.6, 0, frame)

    for px, py, fist, label, raw_px in hands_to_draw:
        if SHOW_TRACKING_DEBUG and raw_px is not None:
            cv2.circle(frame, raw_px, 5, (150, 150, 150), -1)      # raw detection
            cv2.line(frame, raw_px, (px, py), (150, 150, 150), 1)  # lead vector
        color = (0, 0, 255) if fist else (0, 255, 0)
        cv2.circle(frame, (px, py), PALM_HIT_RADIUS, color, 1)
        cv2.circle(frame, (px, py), 8, color, -1)
        tag = "FIST" if fist else "open"
        cv2.putText(frame, f"{label} {tag}", (px + 12, py),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)

    # --- smoothed display FPS --------------------------------------------
    curr_time = time.perf_counter()
    inst_fps = 1.0 / max(curr_time - prev_time, 1e-6)
    prev_time = curr_time
    fps = 0.9 * fps + 0.1 * inst_fps if fps else inst_fps
    cv2.putText(frame, f"FPS: {fps:.1f}", (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2, cv2.LINE_AA)

    # --- rhythm-game HUD ------------------------------------------------
    if note_mgr is not None:
        FONT = cv2.FONT_HERSHEY_SIMPLEX
        cv2.putText(frame, f"SCORE {note_mgr.score}", (w - 260, 40),
                    FONT, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(frame, f"ACC {note_mgr.accuracy() * 100:5.1f}%", (w - 260, 70),
                    FONT, 0.6, (200, 200, 200), 1, cv2.LINE_AA)
        if note_mgr.combo >= 3:
            txt = f"{note_mgr.combo} COMBO"
            (tw, _), _ = cv2.getTextSize(txt, FONT, 1.0, 2)
            cv2.putText(frame, txt, (w // 2 - tw // 2, 90), FONT, 1.0,
                        (0, 255, 255), 2, cv2.LINE_AA)

        if note_mgr.last_judgment and song_ms is not None:
            j, at = note_mgr.last_judgment
            if 0 <= song_ms - at < 550:
                jc = {"PERFECT": (0, 255, 120), "GREAT": (0, 220, 255),
                      "GOOD": (0, 160, 255), "MISS": (60, 60, 255)}[j]
                (tw, _), _ = cv2.getTextSize(j, FONT, 1.3, 3)
                cv2.putText(frame, j, (w // 2 - tw // 2, h // 2 - 110),
                            FONT, 1.3, jc, 3, cv2.LINE_AA)

        if note_mgr.finished:
            c = note_mgr.counts
            lines = [
                "RESULTS",
                f"Perfect {c['PERFECT']}   Great {c['GREAT']}",
                f"Good {c['GOOD']}   Miss {c['MISS']}",
                f"Max combo {note_mgr.max_combo}",
                f"Score {note_mgr.score}   Acc {note_mgr.accuracy() * 100:.2f}%",
                "press q to quit",
            ]
            box = frame.copy()
            cv2.rectangle(box, (w // 2 - 240, h // 2 - 130),
                          (w // 2 + 240, h // 2 + 130), (0, 0, 0), -1)
            cv2.addWeighted(box, 0.6, frame, 0.4, 0, frame)
            for k, line in enumerate(lines):
                cv2.putText(frame, line, (w // 2 - 220, h // 2 - 95 + k * 38),
                            FONT, 0.7, (255, 255, 255), 2, cv2.LINE_AA)

    cv2.imshow("Hand Tracking Test", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

if note_mgr is not None:
    c = note_mgr.counts
    print(f"\nfinal: score {note_mgr.score}  acc {note_mgr.accuracy() * 100:.2f}%  "
          f"max combo {note_mgr.max_combo}")
    print(f"  P {c['PERFECT']}  Gr {c['GREAT']}  Gd {c['GOOD']}  M {c['MISS']}"
          f"  (of {note_mgr.total})")
    pygame.mixer.music.stop()

landmarker.close()
cam.release()
cv2.destroyAllWindows()
