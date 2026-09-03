"""Rhythm-game layer: turns parsed chart events into on-screen notes and
judges the player's hits against the audio clock. Pure logic — no cv2 / pygame.
"""

# --- chart position -> board zone id -------------------------------------
# Ring taps 1-8 land on the outer wedge ring (D); touch notes keep their
# own letter. This keeps ring notes and touch notes on separate rings so a
# hit is never ambiguous.
RING_TO_ZONE = {str(i): f"D{i}" for i in range(1, 9)}


def pos_to_zone(pos):
    if pos in ("C", "C1"):
        return "C1"
    if pos == "C2":
        return "C2"
    if len(pos) == 2 and pos[0] in "ABDE" and pos[1] in "12345678":
        return pos
    return RING_TO_ZONE.get(pos)


# --- timing windows (milliseconds, absolute error) ---------------------
W_PERFECT, W_GREAT, W_GOOD, W_MISS = 45, 90, 140, 200
SCORE = {"PERFECT": 100, "GREAT": 70, "GOOD": 40, "MISS": 0}
JUDGEABLE = {"TAP", "HOLD", "TOUCH", "TOUCH_HOLD", "SLIDE"}


class NoteManager:
    def __init__(self, events, lead_ms=1200):
        self.notes = []
        for e in events:
            if e["type"] not in JUDGEABLE:
                continue
            src = e["start"] if e["type"] == "SLIDE" else e.get("pos", "")
            zone = pos_to_zone(src)
            if zone is None:
                continue
            self.notes.append({
                "t": e["time_ms"],
                "zone": zone,
                "kind": e["type"],
                "judged": None,     # None | "PERFECT" | "GREAT" | "GOOD" | "MISS"
                "progress": 0.0,    # 0 = just spawned, 1 = due now
            })
        self.notes.sort(key=lambda n: n["t"])

        self.lead_ms = lead_ms
        self.score = 0
        self.combo = 0
        self.max_combo = 0
        self.counts = {"PERFECT": 0, "GREAT": 0, "GOOD": 0, "MISS": 0}
        self.last_judgment = None       # (text, song_ms_when)
        self._miss_cursor = 0

    @property
    def total(self):
        return len(self.notes)

    @property
    def finished(self):
        return self._miss_cursor >= len(self.notes)

    def update(self, song_ms):
        """Auto-miss notes whose window has fully closed, then return the
        notes currently on screen (each with a fresh `progress`)."""
        while self._miss_cursor < len(self.notes):
            n = self.notes[self._miss_cursor]
            if n["judged"] is not None:
                self._miss_cursor += 1
            elif song_ms - n["t"] > W_MISS:
                self._apply(n, "MISS", song_ms)
                self._miss_cursor += 1
            else:
                break

        active = []
        for n in self.notes:
            if n["judged"] is not None:
                continue
            dt = n["t"] - song_ms
            if dt > self.lead_ms:
                break
            if dt >= -W_MISS:
                n["progress"] = max(0.0, min(1.0, 1 - dt / self.lead_ms))
                active.append(n)
        return active

    def judge(self, zones, song_ms):
        """The player clenched over `zones` (a set of zone ids) at song_ms.
        Match the nearest pending note in those zones. Return the judgment
        string, or None if nothing was close enough."""
        best, best_err = None, None
        for n in self.notes:
            if n["judged"] is not None or n["zone"] not in zones:
                continue
            err = abs(n["t"] - song_ms)
            if err > W_GOOD:
                continue
            if best is None or err < best_err:
                best, best_err = n, err
        if best is None:
            return None

        j = ("PERFECT" if best_err <= W_PERFECT
             else "GREAT" if best_err <= W_GREAT
             else "GOOD")
        self._apply(best, j, song_ms)
        return j

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
        got = sum(self.counts[k] * SCORE[k] for k in self.counts)
        return got / (graded * SCORE["PERFECT"])


if __name__ == "__main__":
    from maidata import get_events, load_maidata

    fields = load_maidata("levels/メズマライザー/maidata.txt")
    nm = NoteManager(get_events(fields, 4))
    print(f"{nm.total} judgeable notes")
    zones = {}
    for n in nm.notes:
        zones[n["zone"]] = zones.get(n["zone"], 0) + 1
    print("per zone:", dict(sorted(zones.items())))
