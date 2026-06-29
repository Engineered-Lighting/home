"""Final verification battery for det_multiscale.detect_boards."""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv2
import numpy as np
import det_multiscale as dm

frame = r"C:\Users\Marcelo\AppData\Local\Temp\claude\camshots\kitchen_native.jpg"
img = cv2.imread(frame, cv2.IMREAD_GRAYSCALE)

# 1. main result + independent homography validation
t0 = time.time()
boards = dm.detect_boards(img)
dt = (time.time() - t0) * 1000
print(f"[1] boards={len(boards)} dots={[len(p) for p, _ in boards]} "
      f"runtime={dt:.0f}ms")
ok = True
for bi, (p, idx) in enumerate(boards):
    assert p.dtype == np.float32 and p.shape[1] == 2
    assert idx.dtype in (np.int32, np.int64, int) or np.issubdtype(idx.dtype, np.integer)
    H, _ = cv2.findHomography(idx.astype(np.float32), p, 0)
    proj = cv2.perspectiveTransform(
        idx.reshape(-1, 1, 2).astype(np.float64), H.astype(np.float64)).reshape(-1, 2)
    med = float(np.median(np.linalg.norm(proj - p, axis=1)))
    dup = len(np.unique(idx, axis=0)) == len(idx)
    print(f"    B{bi}: n={len(p)} med={med:.3f}px unique_cells={dup} "
          f"idx0based={idx.min(axis=0).tolist()}")
    ok &= med < 1.5 and dup and len(p) >= 20
print(f"    all gates: {ok}")

# 2. clutter-only crops must give 0
crops = {"floor/fridge": img[430:720, 0:620],
         "cabinets right": img[0:720, 960:1280],
         "doorway top": img[0:260, 230:640]}
for name, c in crops.items():
    z = dm.detect_boards(c)
    print(f"[2] clutter '{name}': {len(z)} (expect 0)")

# 3. synthetic random-dot clutter must give 0
rng = np.random.default_rng(7)
noise = (rng.uniform(0, 60, (720, 1280))).astype(np.uint8)
for k in range(250):
    x, y = rng.integers(10, 1270), rng.integers(10, 710)
    cv2.circle(noise, (int(x), int(y)), int(rng.integers(2, 6)), 255, -1)
z = dm.detect_boards(noise)
print(f"[3] synthetic random dots: {len(z)} boards (expect 0)")

# 4. rotation sanity: 90 deg rotate -> same 4 boards
rot = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
zb = dm.detect_boards(rot)
print(f"[4] rotated frame: boards={len(zb)} dots={[len(p) for p, _ in zb]}")

# 5. import from foreign cwd
print("[5] module file:", dm.__file__)
