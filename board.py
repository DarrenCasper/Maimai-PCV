import math

FRAME_W, FRAME_H = 1280, 720
CENTER = (FRAME_W // 2, FRAME_H // 2)

RING_LAYERS = [
    {"label": "B", "r_min": 40,  "r_max": 110, "angle_offset": 22.5},
    {"label": "E", "r_min": 110, "r_max": 180, "angle_offset": 0},
    {"label": "A", "r_min": 180, "r_max": 250, "angle_offset": 22.5},
    {"label": "D", "r_min": 250, "r_max": 320, "angle_offset": 0},
]
C_RADIUS = 40

TARGET_RADIUS = 44


def zone_center(zone_id):
    if zone_id in ("C1", "C2"):
        return (CENTER[0] + (-20 if zone_id == "C1" else 20), CENTER[1])
    label, idx = zone_id[0], int(zone_id[1:]) - 1
    layer = next(l for l in RING_LAYERS if l["label"] == label)
    mid_r = (layer["r_min"] + layer["r_max"]) / 2
    ang = math.radians(layer["angle_offset"] + idx * 45 + 22.5)
    return (int(CENTER[0] + mid_r * math.cos(ang)),
            int(CENTER[1] + mid_r * math.sin(ang)))
