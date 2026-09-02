import cv2
import mediapipe as mp
import numpy as np
import time
import math
import threading

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
# One-Euro filter: smooths hard when the hand is slow (kills jitter) and
# opens right up when the hand is fast (near pass-through, almost no lag).
#   MIN_CUTOFF up   -> less lag everywhere, but trembles at rest come back
#   BETA       up   -> opens the filter sooner as the hand speeds up
#                      (this is THE knob for "keep up with fast moves")
#   D_CUTOFF   up   -> velocity estimate reacts faster, so BETA kicks in
#                      immediately on a flick instead of a few frames late
MIN_CUTOFF = 1.0
BETA = 9.0
D_CUTOFF = 10.0

# How much of the measured capture->display latency to cancel by projecting
# the palm forward along its velocity. 1.0 = dot sits at the hand's true
# *current* position (best for fast tracking, can overshoot on sharp
# reversals); 0.0 = dot lags by the full pipeline latency but never
# overshoots. PREDICT_S adds a fixed extra lead on top.
LATENCY_COMP = 0.7
PREDICT_S = 0.0
MAX_EXTRAP_S = 0.06   # never extrapolate further than this (hitch guard)


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
        a_d = self._alpha(self.d_cutoff, dt)
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
DETECT_WIDTH = 384

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

start = time.perf_counter()
last_ts = -1
last_frame = None
last_seq = -1            # result_seq we last folded into the filters

fps = 0.0
prev_time = time.perf_counter()

while True:
    frame = cam.read()
    if frame is None:
        time.sleep(0.001)
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
                    print(f"HIT {sorted(hit_zones)} ({label})")
                    state["flash_zones"] = set(hit_zones)
                    state["flash_until"] = now + 0.15
                    state["hold_start"] = now
                    state["hold_zones"] = set(hit_zones)
                else:
                    print(f"FIST outside board ({label})")

            elif not fist and state["was_fist"] and state["hold_start"] is not None:
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

            hands_to_draw.append((px, py, fist, label))

    # one blend for every highlight this frame, then draw the palm markers
    if overlay is not None:
        cv2.addWeighted(overlay, 0.4, frame, 0.6, 0, frame)

    for px, py, fist, label in hands_to_draw:
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

    cv2.imshow("Hand Tracking Test", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

landmarker.close()
cam.release()
cv2.destroyAllWindows()
