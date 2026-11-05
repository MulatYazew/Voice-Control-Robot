# Stereo depth (ELP-USB3DGS1200P01)

The ELP board is two synchronised global-shutter cameras on one USB cable. It sends **one frame with
left | right side by side** (3200x1200 = 2 x 1600x1200, MJPEG). It does **not** output depth by itself:
`backend/stereo_depth` calibrates the pair, rectifies it and computes depth with OpenCV StereoSGBM.

What it adds to the app: the **rim height of a cup** measured by stereo, used by `point_at_cup` /
`tocca` instead of the fixed `touch.object_height_m`. Table X/Y still come from the existing marker
calibration (pixel -> base frame homography). Nothing in `stereo_depth` moves the robot; if the
service is off, down, or gives an implausible value, the gate uses the old paths unchanged.

## Steps (Windows, from the repo root, venv active)

```powershell
pip install opencv-python numpy        # opencv-python (not headless) for the calibration window
cd backend

# 0. aim the camera (no calibration needed)
python -m stereo_depth preview

# 1. checkerboard -> data\stereo\checkerboard.png; print at 100 %, glue flat, MEASURE a square
python -m stereo_depth board
# (no printer: show the PNG full screen on another laptop/tablet and measure a square on the screen)

# 2. calibrate the pair (any place; does not depend on where the camera is mounted)
#    camera still, move the board: near/far, tilted, every corner of the image; 15-20 pairs
python -m stereo_depth calibrate --square-mm 24.6       # your measured value
#    -> config\stereo_calib.npz, prints fx fy cx cy -> paste into config\robot.yaml camera_intrinsics

# 3. run the service (keep this window open)
python -m stereo_depth serve
#    open http://127.0.0.1:8720  and click the image: distance and height above the table
```

Then start the app with the stereo camera as its camera:

```powershell
$env:CAMERA_SOURCE="url"; $env:CAMERA_URL="http://127.0.0.1:8720/frame.jpg"; $env:STEREO_URL="http://127.0.0.1:8720"
powershell -ExecutionPolicy Bypass -File .\start.ps1 real
```

**Redo the marker table calibration after switching to this camera** (the image is now 1600x1200
rectified left; the old phone calibration does not apply).

## Good to know

* Calibration is valid as long as nobody twists the M12 lenses. Moving the whole camera is fine
  (only the marker table calibration must be redone).
* Check after calibrating: `rms_stereo` below ~0.5 px, `baseline_mm` close to the lens distance
  measured with a ruler.
* Depth needs texture. A plain white board gives few points; the table plane is taken from the
  printed ArUco when it is visible (best), else fitted with RANSAC on the depth points.
* The left ~256 px of the image have no depth (SGBM search range, `--num-disp 128` at half
  resolution). Objects closer than ~25-30 cm are out of range too.
* `/api/height` returns the 90th percentile height of the depth points inside the box that are
  above the table: the rim of a cup.

## HTTP API (pixels of /frame.jpg)

| Endpoint | Returns |
| --- | --- |
| `/frame.jpg` (`/shot.jpg`) | rectified left image |
| `/depth.jpg` | coloured disparity, red = near |
| `/api/status` | frame size, intrinsics, baseline, calibration rms |
| `/api/point?u=&v=` | `xyz_m` (camera frame), `distance_m`, `height_m` above the table |
| `/api/height?x0=&y0=&w=&h=` | `height_m`, `points`, `plane` (`marker`/`ransac`), `frame_size` |

Tests: `python -m pytest tests/test_stereo_depth.py tests/test_stereo_hook.py`
(synthetic tilted table + 9 cm block, marker plane, service end-to-end, gate fallbacks).
