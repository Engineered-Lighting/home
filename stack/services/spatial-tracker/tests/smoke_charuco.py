"""Manual smoke test (not collected by pytest): validates calib.calibrate_charuco
against synthetic renders of a ChArUco board viewed through a known camera.

Renders the board (a plane at z=0 in board coordinates) into several views via
homographies H = K @ [r1 r2 t] @ S and checks the recovered fx/fy/cx/cy.
"""
import math
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from calib import calibrate_charuco  # noqa: E402

COLS, ROWS, SQUARE_MM = 7, 5, 40.0
VIEW = (1280, 720)
K_TRUE = np.array([[950.0, 0, 640.0], [0, 950.0, 360.0], [0, 0, 1.0]])


def board_image():
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_100)
    board = cv2.aruco.CharucoBoard((COLS, ROWS), SQUARE_MM / 1000.0,
                                   SQUARE_MM / 1000.0 * 0.75, d)
    px_per_square = 120
    img = board.generateImage((COLS * px_per_square, ROWS * px_per_square),
                              marginSize=0)
    m_per_px = (SQUARE_MM / 1000.0) / px_per_square
    return img, m_per_px


def render_view(img, m_per_px, rx, ry, tz, tx=0.0, ty=0.0):
    """Render the board plane through K_TRUE with rotation rx/ry (rad) and a
    camera tz meters in front of the board center."""
    bw_m = img.shape[1] * m_per_px
    bh_m = img.shape[0] * m_per_px
    Rx, _ = cv2.Rodrigues(np.array([rx, 0, 0.0]))
    Ry, _ = cv2.Rodrigues(np.array([0, ry, 0.0]))
    R = Rx @ Ry
    # plane point (X, Y, 0): x_cam = X*r1 + Y*r2 + t  ->  H = K [r1 r2 t] P
    # where P maps board-image pixels to centered board meters
    Rt = np.column_stack([R[:, 0], R[:, 1], np.array([tx, ty, tz])])
    px_to_centered_m = np.array([[m_per_px, 0, -bw_m / 2.0],
                                 [0, m_per_px, -bh_m / 2.0],
                                 [0, 0, 1.0]])
    H = K_TRUE @ Rt @ px_to_centered_m
    return cv2.warpPerspective(img, H, VIEW, borderValue=255)


def main():
    img, m_per_px = board_image()
    views = []
    poses = [
        (0.15, 0.0, 0.45), (-0.2, 0.1, 0.5), (0.1, -0.25, 0.5),
        (0.3, 0.2, 0.55), (-0.15, -0.2, 0.45), (0.0, 0.3, 0.5),
        (0.25, -0.1, 0.6), (-0.3, 0.05, 0.55),
    ]
    for rx, ry, tz in poses:
        views.append(render_view(img, m_per_px, rx, ry, tz))
    result = calibrate_charuco(views, COLS, ROWS, SQUARE_MM)
    K = np.array(result["K"])
    print(f"n_frames_used={result['n_frames_used']} rms={result['rms_px']:.3f}px")
    print(f"fx={K[0,0]:.1f} fy={K[1,1]:.1f} cx={K[0,2]:.1f} cy={K[1,2]:.1f}"
          f"  (truth fx=fy=950, cx=640, cy=360)")
    assert result["n_frames_used"] >= 5
    assert result["rms_px"] < 2.0
    assert abs(K[0, 0] - 950) / 950 < 0.05
    assert abs(K[1, 1] - 950) / 950 < 0.05
    assert abs(K[0, 2] - 640) < 40
    assert abs(K[1, 2] - 360) < 40
    print("CHARUCO SMOKE OK")


if __name__ == "__main__":
    main()
