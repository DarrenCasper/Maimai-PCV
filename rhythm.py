"""Rhythm-game layer: turns parsed chart events into on-screen notes and
judges the player's hits against the audio clock. Pure logic — no cv2 / pygame.

Judging is deliberately simple: a note is a PERFECT when a glove box overlaps
its target circle while the note is inside the hit window, otherwise MISS.
"""

from board import TARGET_RADIUS, zone_center

# --- chart position -> board zone id -------------------------------------
# Lane notes 1-8 land on the outer ring (D); touch notes keep their own letter.
# Like the real maimai, lane 1 is the top-right button and the lanes go
# clockwise. On this board the top-right outer zone is D7, so lane 1 -> D7,
# lane 2 -> D8, lane 3 -> D1, and so on.
RING_TO_ZONE = {str(lane): f"D{(lane + 5) % 8 + 1}" for lane in range(1, 9)}


def pos_to_zone(pos):
    if pos in ("C", "C1"):
        return "C1"
    if pos == "C2":
        return "C2"
    if len(pos) == 2 and pos[0] in "ABDE" and pos[1] in "12345678":
        return pos
    return RING_TO_ZONE.get(pos)


# A note can be hit from this long before it is due until this long after.
# Once the window closes without an overlap, it is a MISS.
HIT_WINDOW_MS = 200
SCORE = {"PERFECT": 100, "MISS": 0}
JUDGEABLE = {"TAP", "HOLD", "TOUCH", "TOUCH_HOLD", "SLIDE"}


def box_hits_circle(box, cx, cy, r):
    """True if the (x0, y0, x1, y1) box overlaps the circle."""
    x0, y0, x1, y1 = box
    nx = min(max(cx, x0), x1)
    ny = min(max(cy, y0), y1)
    return (nx - cx) ** 2 + (ny - cy) ** 2 <= r * r


class NoteManager:
    def __init__(self, events, lead_ms=1200):
        self.notes = []
        for e in events:
            if e["type"] not in JUDGEABLE:
                continue
            path, duration = None, 0
            if e["type"] == "SLIDE":
                # the star travels through these lane buttons and is hit at the last one
                zones = [z for z in (pos_to_zone(w) for w in e["waypoints"]) if z]
                if len(zones) < 2:
                    continue
                path = [zone_center(z) for z in zones]
                duration = max(e["duration_ms"], 1)
                zone = zones[-1]
            else:
                zone = pos_to_zone(e.get("pos", ""))
                if zone is None:
                    continue
            self.notes.append({
                "start_t": e["time_ms"],           # when the note reaches its first spot
                "t": e["time_ms"] + duration,      # when it must be hit (a slide: its end)
                "zone": zone,
                "xy": zone_center(zone),           # where it must be hit
                "path": path,                      # slides only: list of (x, y) lane spots
                "duration": duration,
                "kind": e["type"],
                "judged": None,      # None | "PERFECT" | "MISS"
                "progress": 0.0,     # 0 = just spawned, 1 = at its first spot
                "travel": 0.0,       # slides only: 0 = at the start, 1 = at the end
            })
        self.notes.sort(key=lambda n: n["t"])

        self.lead_ms = lead_ms
        self.score = 0
        self.combo = 0
        self.max_combo = 0
        self.counts = {"PERFECT": 0, "MISS": 0}
        self.last_judgment = None       # (text, song_ms_when)
        self._cursor = 0                # first note not yet resolved in order

    @property
    def total(self):
        return len(self.notes)

    @property
    def finished(self):
        return self._cursor >= len(self.notes)

    def update(self, song_ms):
        """Auto-miss notes whose window has closed, then return the notes
        currently on screen (each with a fresh `progress`)."""
        while self._cursor < len(self.notes):
            n = self.notes[self._cursor]
            if n["judged"] is not None:
                self._cursor += 1
            elif song_ms - n["t"] > HIT_WINDOW_MS:
                self._apply(n, "MISS", song_ms)
                self._cursor += 1
            else:
                break

        active = []
        for n in self.notes[self._cursor:]:
            if n["judged"] is not None or n["start_t"] - song_ms > self.lead_ms:
                continue
            n["progress"] = max(0.0, min(1.0, 1 - (n["start_t"] - song_ms) / self.lead_ms))
            if n["path"]:
                n["travel"] = max(0.0, min(1.0, (song_ms - n["start_t"]) / n["duration"]))
            active.append(n)
        return active

    def judge(self, boxes, song_ms):
        """Mark every pending note in its hit window whose target circle
        overlaps one of `boxes` as PERFECT. Returns the notes just hit."""
        hits = []
        if not boxes:
            return hits
        for n in self.notes[self._cursor:]:
            if n["t"] - song_ms > HIT_WINDOW_MS:
                break
            if n["judged"] is not None:
                continue
            x, y = n["xy"]
            if any(box_hits_circle(b, x, y, TARGET_RADIUS) for b in boxes):
                self._apply(n, "PERFECT", song_ms)
                hits.append(n)
        return hits

    def _apply(self, note, judgment, song_ms):
        note["judged"] = judgment
        self.counts[judgment] += 1
        self.score += SCORE[judgment]
        if judgment == "MISS":
            self.combo = 0
        else:
            self.combo += 1
            self.max_combo = max(self.max_combo, self.combo)
        self.last_judgment = (judgment, song_ms)

    def accuracy(self):
        graded = sum(self.counts.values())
        if not graded:
            return 0.0
        return self.counts["PERFECT"] / graded


if __name__ == "__main__":
    from maidata import get_events, load_maidata

    fields = load_maidata("levels/メズマライザー/maidata.txt")
    nm = NoteManager(get_events(fields, 4))
    print(f"{nm.total} judgeable notes")
    zones = {}
    for n in nm.notes:
        zones[n["zone"]] = zones.get(n["zone"], 0) + 1
    print("per zone:", dict(sorted(zones.items())))
