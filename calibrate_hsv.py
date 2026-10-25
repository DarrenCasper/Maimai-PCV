"""Find the HSV range of one glove color, using sliders.

Run it once per color (light blue, orange, white):

    python calibrate_hsv.py        # camera 0 (the scrcpy /dev/video0)
    python calibrate_hsv.py 1      # another camera index

Hold the glove in front of the camera and move the sliders until the right
picture (the mask) is white on that color of the glove and black everywhere
else. A few stray dots are fine. Press p to print the range, then paste it into
BLUE / ORANGE / WHITE at the top of hand_tracking.py. Press q to quit.
"""

import sys

import cv2
import numpy as np

CAMERA_INDEX = int(sys.argv[1]) if len(sys.argv) > 1 else 0
WINDOW = "HSV calibration"


def nothing(_):
    pass


cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
if not cap.isOpened():
    raise SystemExit(f"Could not open camera {CAMERA_INDEX}. Is scrcpy running?")

cv2.namedWindow(WINDOW)
for name, start, top in [("H min", 90, 180), ("H max", 112, 180),
                         ("S min", 55, 255), ("S max", 255, 255),
                         ("V min", 150, 255), ("V max", 255, 255)]:
    cv2.createTrackbar(name, WINDOW, start, top, nothing)

print("Move the sliders until the mask is white only on one glove color. p = print, q = quit.")

while True:
    ok, frame = cap.read()
    if not ok:
        continue
    frame = cv2.flip(frame, 1)                       # same mirror as the game
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    low = tuple(cv2.getTrackbarPos(n, WINDOW) for n in ("H min", "S min", "V min"))
    high = tuple(cv2.getTrackbarPos(n, WINDOW) for n in ("H max", "S max", "V max"))
    mask = cv2.inRange(hsv, np.array(low), np.array(high))

    both = np.hstack([frame, cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)])
    both = cv2.resize(both, None, fx=0.5, fy=0.5)    # shrink so it fits the screen
    cv2.putText(both, f"{low} {high}", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    cv2.imshow(WINDOW, both)

    key = cv2.waitKey(1) & 0xFF
    if key == ord("q"):
        break
    if key == ord("p"):
        print(f"({low}, {high})")

cap.release()
cv2.destroyAllWindows()
