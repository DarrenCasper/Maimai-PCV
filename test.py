import threading
import time
from pathlib import Path

import cv2
import pygame

import render
from chart_parser import parse_chart
from hand_tracking import GloveTracker
from maidata import get_events, load_maidata
from rhythm import NoteManager

PLAY_CHART = True
LEVEL_NAME = "メズマライザー"
DIFFICULTY = 6
AUDIO_OFFSET_MS = 0
NOTE_SPEED = 5.0
NOTE_LEAD_MS = int(3600 / NOTE_SPEED)
CAMERA_INDEX = 0
RUN_PARSER_TEST = False


def test_chart_parser():
    events = parse_chart("1,,A1,,{8}1-3[4:1],,C1h[4:1],,,4h[4:1],,1/4>6[8:3],", bpm=120)
    print(f"{len(events)} events parsed:")
    for e in events:
        print(" ", e)


class CameraStream:
    def __init__(self, index):
        self.cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.frame = None
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self.running:
            ok, frame = self.cap.read()
            if ok:
                self.frame = frame
            else:
                time.sleep(0.005)

    def read(self):
        return self.frame

    def release(self):
        self.running = False
        time.sleep(0.1)
        self.cap.release()


class SongClock:
    def __init__(self, track_path):
        pygame.mixer.init()
        pygame.mixer.music.load(str(track_path))
        pygame.mixer.music.play()
        self.start = time.perf_counter()

    def ms(self):
        pos = pygame.mixer.music.get_pos()
        if pos < 0:
            pos = int((time.perf_counter() - self.start) * 1000)
        return pos + AUDIO_OFFSET_MS

    def stop(self):
        pygame.mixer.music.stop()


def main():
    camera = CameraStream(CAMERA_INDEX)
    level = Path(__file__).parent / "levels" / LEVEL_NAME
    backdrop = render.load_backdrop(level / "bg.jpg")

    manager, clock = None, None
    if PLAY_CHART:
        fields = load_maidata(level / "maidata.txt")
        manager = NoteManager(get_events(fields, DIFFICULTY), lead_ms=NOTE_LEAD_MS)
        clock = SongClock(level / "track.mp3")
        print(f"chart: {fields.get('title')} [slot {DIFFICULTY}] - {manager.total} notes")

    print(f"Camera index {CAMERA_INDEX}. Press q in the window to quit.")

    tracker = GloveTracker()
    last_raw, camera_img, boxes = None, None, []
    start = prev = time.perf_counter()
    warned = False

    while True:
        raw = camera.read()
        if raw is None:
            if not warned and time.perf_counter() - start > 3:
                print(f"No frames from camera {CAMERA_INDEX} after 3s. Is scrcpy running? "
                      f"Try another CAMERA_INDEX.")
                warned = True
            time.sleep(0.01)
            continue

        if raw is not last_raw:
            last_raw = raw
            camera_img = cv2.flip(render.fit_cover(raw), 1)
            boxes = tracker.update(camera_img)

        now = time.perf_counter()
        scene = backdrop.copy()
        render.draw_board(scene)

        song_ms = clock.ms() if clock else None
        if manager is not None:
            notes = manager.update(song_ms)
            manager.judge(boxes, song_ms)
            render.draw_notes(scene, notes)

        render.draw_boxes(scene, boxes)
        render.draw_preview(scene, camera_img, boxes)

        fps = 1 / max(now - prev, 1e-6)
        prev = now
        render.draw_hud(scene, manager, song_ms, fps)
        if manager is not None and manager.finished:
            render.draw_results(scene, manager)

        cv2.imshow("Maimai PCV", scene)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    if manager is not None:
        print(f"final: score {manager.score}  acc {manager.accuracy() * 100:.1f}%  "
              f"perfect {manager.counts['PERFECT']}  miss {manager.counts['MISS']}  "
              f"max combo {manager.max_combo}")
        clock.stop()
    camera.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    if RUN_PARSER_TEST:
        test_chart_parser()
    else:
        main()
