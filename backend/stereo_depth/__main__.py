"""python -m stereo_depth {board|preview|calibrate|serve}

    board      write the checkerboard PNG to print (data/stereo/checkerboard.png)
    preview    live left|right view to aim the camera (no calibration needed)
    calibrate  show the board to the camera in 15-20 poses, SPACE to keep a pair, C to compute
    serve      HTTP service on :8720 (needs calibrate first)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CALIB = ROOT / "config" / "stereo_calib.npz"


def cmd_board(a) -> None:
    from .calib import board_png
    p = board_png(ROOT / "data" / "stereo" / "checkerboard.png", a.square_mm)
    print(f"Wrote {p}\nPrint it at 100% (no 'fit to page'), glue it flat on cardboard, MEASURE one square in mm.")


def _keys() -> set[int]:
    """Keys from the picture window and, on Windows, from this terminal.

    The picture window only receives a key while it is the focused window.
    Typing C in the PowerShell that launched calibrate has to count too.
    """
    found: set[int] = set()
    key = cv2.waitKey(20) & 0xFF
    if key != 255:
        found.add(key)
        if 65 <= key <= 90:
            found.add(key + 32)
    if sys.platform.startswith("win"):
        import msvcrt
        while msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                msvcrt.getwch()
                continue
            if ch.isalpha():
                found.add(ord(ch.lower()))
            elif ch == "\x03":
                found.add(3)
            else:
                found.add(ord(ch))
    return found


def _show(name: str, img: np.ndarray, width: int = 1600) -> set[int]:
    s = min(1.0, width / img.shape[1])
    cv2.imshow(name, cv2.resize(img, None, fx=s, fy=s))
    return _keys()


def cmd_preview(a) -> None:
    from .capture import StereoCamera
    cam = StereoCamera(a.cam)
    left, _ = cam.wait_first()
    print(f"camera {cam.index}, {left.shape[1]}x{left.shape[0]} per eye. q = quit")
    while True:
        pair = cam.read()
        if pair is None:
            continue
        both = np.hstack(pair)
        h, w = both.shape[:2]
        for i in (1, 2):
            for x0 in (0, w // 2):
                cv2.line(both, (x0 + w // 2 * i // 3, 0), (x0 + w // 2 * i // 3, h), (0, 255, 0), 2)
            cv2.line(both, (0, h * i // 3), (w, h * i // 3), (0, 255, 0), 2)
        if _show("stereo preview (q quits)", both) & {ord("q"), 27, 3}:
            break
    cam.close()
    cv2.destroyAllWindows()


def cmd_calibrate(a) -> None:
    from .calib import align_board_order, calibrate, find_corners
    from .capture import StereoCamera
    cam = StereoCamera(a.cam)
    left, _ = cam.wait_first()
    size = (left.shape[1], left.shape[0])
    save_dir = ROOT / "data" / "stereo" / "calib_pairs"
    save_dir.mkdir(parents=True, exist_ok=True)
    L, R = [], []
    print(f"camera {cam.index}, {size[0]}x{size[1]} per eye. Board square = {a.square_mm} mm.")
    print("Move the board: near/far, tilted left/right/up/down, flat on the table, all corners of the image.")
    print("SPACE = keep (both green), C = compute (>= 12 pairs), Q = quit.")
    print("Click the picture window first, or type that key in this terminal.")
    while True:
        pair = cam.read()
        if pair is None:
            continue
        l, r = pair
        # 0.75, not 0.4: a screen lying flat is smaller in the frame, and the
        # extra shrink used to drop its squares below what the detector accepts.
        small = [cv2.resize(x, None, fx=0.75, fy=0.75) for x in (l, r)]
        found = [find_corners(x, fast=True) for x in small]
        view = []
        for img, c in zip(small, found):
            img = img.copy()
            if c is not None:
                cv2.drawChessboardCorners(img, (9, 6), c, True)
            view.append(img)
        both = np.hstack(view)
        ok_both = all(c is not None for c in found)
        cv2.putText(both, f"pairs: {len(L)}   {'BOTH SEE BOARD - press SPACE' if ok_both else 'board not seen in both'}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0) if ok_both else (0, 0, 255), 2)
        keys = _show("calibrate (SPACE keep, C compute, Q quit)", both)
        if keys & {ord("q"), 27, 3}:
            break
        if ord(" ") in keys and ok_both:
            cl, cr = find_corners(l), find_corners(r)
            if cl is None or cr is None:
                print("  sharp detection failed, hold the board still and try again")
                continue
            cl, cr = align_board_order(cl, cr)
            L.append(cl)
            R.append(cr)
            cv2.imwrite(str(save_dir / f"{len(L):02d}_left.png"), l)
            cv2.imwrite(str(save_dir / f"{len(L):02d}_right.png"), r)
            print(f"  kept pair {len(L)}")
        if ord("c") in keys:
            if len(L) < 12:
                print(f"  need >= 12 pairs, have {len(L)}")
                continue
            print("computing ...")
            cal, info = calibrate(L, R, size, a.square_mm / 1000.0)
            cal.save(a.out)
            from .calib import Rectifier
            intr = Rectifier(cal).intrinsics()
            print(f"saved {a.out}\n{info}")
            if info["rms_stereo"] < 0.5:
                print("rms_stereo is under 0.5 px. Check baseline_mm against the distance between the two lenses.")
                print("Paste into config/robot.yaml -> camera_intrinsics (these are the pixels of /frame.jpg):")
                for k, v in intr.items():
                    print(f"  {k}: {v}")
            else:
                print(f"rms_stereo {info['rms_stereo']:.3f} px is above 0.5. Do not paste this into robot.yaml.")
                print("Add more tilted views, including the screen flat on the table, then press C again.")
            break
    cam.close()
    cv2.destroyAllWindows()


def cmd_serve(a) -> None:
    from .calib import StereoCalibration
    from .capture import StereoCamera
    from .service import DepthService, serve
    if not Path(a.calib).exists():
        sys.exit(f"{a.calib} not found: run  python -m stereo_depth calibrate  first")
    cal = StereoCalibration.load(a.calib)
    w, h = cal.size
    cam = StereoCamera(a.cam, (2 * w, h))
    cam.wait_first()
    svc = DepthService(cam, cal, marker_size_m=a.marker_m, scale=a.scale, num_disp=a.num_disp)
    print(svc.status())
    serve(svc, a.host, a.port)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m stereo_depth")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("board")
    b.add_argument("--square-mm", type=float, default=25.0)
    pv = sub.add_parser("preview")
    pv.add_argument("--cam", type=int)
    c = sub.add_parser("calibrate")
    c.add_argument("--cam", type=int)
    c.add_argument("--square-mm", type=float, required=True, help="MEASURED square size of the printed board")
    c.add_argument("--out", default=str(DEFAULT_CALIB))
    s = sub.add_parser("serve")
    s.add_argument("--cam", type=int)
    s.add_argument("--calib", default=str(DEFAULT_CALIB))
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8720)
    s.add_argument("--marker-m", type=float, default=0.05, help="printed ArUco side (config/robot.yaml marker.size_m)")
    s.add_argument("--scale", type=float, default=0.5, help="disparity resolution (0.5 = half)")
    s.add_argument("--num-disp", type=int, default=128)
    a = p.parse_args(argv)
    {"board": cmd_board, "preview": cmd_preview, "calibrate": cmd_calibrate, "serve": cmd_serve}[a.cmd](a)


if __name__ == "__main__":
    main()
