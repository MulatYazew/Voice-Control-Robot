"""Depth from the ELP stereo USB camera (ELP-USB3DGS1200P01).

The camera sends one MJPEG frame with the left and right images side by side. This package
calibrates the pair once with a printed checkerboard, rectifies both images and computes depth
with OpenCV StereoSGBM. It runs as its own small HTTP service (default port 8720):

    /frame.jpg         rectified LEFT image: use it as the app camera (CAMERA_SOURCE=url)
    /depth.jpg         coloured depth of the same view (red = near)
    /api/status        calibration, frame size, intrinsics of /frame.jpg
    /api/point?u=&v=   3D point (m, camera frame) under one pixel of /frame.jpg
    /api/height?x0=&y0=&w=&h=   height (m) of the top of the object in that box above the table

Pixels in every endpoint are pixels of /frame.jpg, so a box found by the app's cup detector
can be sent as is. Nothing here moves the robot.
"""
