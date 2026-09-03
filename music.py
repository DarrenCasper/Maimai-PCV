import time
from pathlib import Path

import pygame

from maidata import get_events, list_charts, load_maidata

# Level folder (simai layout: track.mp3 + maidata.txt + art), resolved
# relative to this file so cwd doesn't matter.
LEVEL_DIR = Path(__file__).parent / "levels" / "メズマライザー"
SONG_PATH = LEVEL_DIR / "track.mp3"
MAIDATA_PATH = LEVEL_DIR / "maidata.txt"

DIFFICULTY = 4          # 2=Basic 3=Advanced 4=Expert 5=Master 6=Re:Master
AUDIO_OFFSET_MS = 0     # +ve = treat notes as later; tune by ear later
LEAD_TIME_MS = 1500     # how long before its hit-time a note "exists" on screen
MISS_MS = 200           # how far past hit-time before we call it gone
TEST_SECONDS = 25       # stop the sync test after this; None = play whole song


def get_delta_ms(note_time_ms, audio_ms, offset_ms=0):
    """Positive = note still coming. Negative = note's moment has passed."""
    return note_time_ms - (audio_ms + offset_ms)


def get_progress(delta_ms, lead_time_ms):
    """0.0 = note just spawned, 1.0 = due right now (clamped both ends)."""
    return min(1.0, max(0.0, 1 - (delta_ms / lead_time_ms)))


fields = load_maidata(MAIDATA_PATH)
print(f"{fields.get('title')} — {fields.get('artist')}")
print("charts available:", {s: f"{n} Lv{lv}" for s, (n, lv) in list_charts(fields).items()})

events = get_events(fields, DIFFICULTY)
print(f"loaded slot {DIFFICULTY}: {len(events)} events, "
      f"last at {events[-1]['time_ms'] / 1000:.1f}s\n")

pygame.mixer.init()
pygame.mixer.music.load(str(SONG_PATH))
pygame.mixer.music.play()

spawn_i = 0   # next note that hasn't entered the lead window yet
pass_i = 0    # next note that hasn't left the window yet

while pass_i < len(events):
    audio_ms = pygame.mixer.music.get_pos()
    if audio_ms == -1 or not pygame.mixer.music.get_busy():
        print("song ended")
        break
    if TEST_SECONDS is not None and audio_ms > TEST_SECONDS * 1000:
        print(f"\nstopping sync test at {TEST_SECONDS}s "
              f"({pass_i}/{len(events)} events consumed)")
        break

    # notes that just came into view
    while spawn_i < len(events):
        d = get_delta_ms(events[spawn_i]["time_ms"], audio_ms, AUDIO_OFFSET_MS)
        if d > LEAD_TIME_MS:
            break
        n = events[spawn_i]
        tgt = n.get("pos") or f"{n['start']}{n['shape']}{n['end']}"
        print(f"[{audio_ms/1000:6.2f}s] SPAWN  #{spawn_i:<4} {n['type']:<10} {tgt:<6} "
              f"due in {d:+5.0f}ms")
        spawn_i += 1

    # notes whose moment has fully passed
    while pass_i < len(events):
        d = get_delta_ms(events[pass_i]["time_ms"], audio_ms, AUDIO_OFFSET_MS)
        if d > -MISS_MS:
            break
        print(f"[{audio_ms/1000:6.2f}s]  gone  #{pass_i}")
        pass_i += 1

    time.sleep(1 / 60)

pygame.mixer.music.stop()
pygame.quit()
