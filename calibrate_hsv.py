"""Interactive HSV range picker for hand_tracking.py.

Opens the camera with H/S/V min+max trackbars and shows the live mask next
to the raw feed, so you can dial in a range that isolates your hand (skin
tone) or a glove/marker color without guessing. Move the sliders until the
mask window is white where your hand is and black everywhere else — a
little noise is fine, hand_tracking.py's morphology + min-area filter clean
that up.

Run:  python calibrate_hsv.py [camera_index]

Press 'p' any time to print the current range as a ready-to-paste Python
tuple. Press 'q' to quit.
"""

import sys

import cv2
import numpy as np

CAMERA_INDEX = int(sys.argv[1]) if len(sys.argv) > 1 else 1

WINDOW = "HSV calibration"
cv2.namedWindow(WINDOW)


def _nop(_):
    pass


cv2.createTrackbar("H min", WINDOW, 0, 180, _nop)
cv2.createTrackbar("H max", WINDOW, 20, 180, _nop)
cv2.createTrackbar("S min", WINDOW, 20, 255, _nop)
cv2.createTrackbar("S max", WINDOW, 150, 255, _nop)
cv2.createTrackbar("V min", WINDOW, 70, 255, _nop)
cv2.createTrackbar("V max", WINDOW, 255, 255, _nop)

cap = cv2.VideoCapture(CAMERA_INDEX)
if not cap.isOpened():
    print(f"Couldn't open camera index {CAMERA_INDEX}. Try passing a "
          f"different index, e.g. `python calibrate_hsv.py 0`.")
    raise SystemExit(1)

print("Move the sliders so the mask window is white on your hand, black "
      "elsewhere. Press 'p' to print the range, 'q' to quit.")

while True:
    ok, frame = cap.read()
    if not ok:
        continue
    frame = cv2.flip(frame, 1)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    lo = (cv2.getTrackbarPos("H min", WINDOW), cv2.getTrackbarPos("S min", WINDOW),
          cv2.getTrackbarPos("V min", WINDOW))
    hi = (cv2.getTrackbarPos("H max", WINDOW), cv2.getTrackbarPos("S max", WINDOW),
          cv2.getTrackbarPos("V max", WINDOW))
    mask = cv2.inRange(hsv, np.array(lo), np.array(hi))

    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    masked_preview = cv2.bitwise_and(frame, frame, mask=mask)
    stacked = np.hstack([frame, masked_preview])
    cv2.putText(stacked, f"lo={lo} hi={hi}  (p=print, q=quit)", (10, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2, cv2.LINE_AA)
    cv2.imshow(WINDOW, stacked)

    key = cv2.waitKey(1) & 0xFF
    if key == ord('q'):
        break
    if key == ord('p'):
        print(f"({lo}, {hi})")

cap.release()
cv2.destroyAllWindows()
