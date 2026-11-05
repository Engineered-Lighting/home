import sys, random, math
sys.path.insert(0, sys.argv[1] + "/tools/den-register")
import rigid, synthetic
R, t = synthetic.camera_pose()
pts = synthetic.POINTS
fit_ids, chk_ids = synthetic.FIT, synthetic.CHECKS
cam = {k: synthetic.to_camera(R, t, v) for k, v in pts.items()}
rng = random.Random(1)
best = None
for trial in range(4000):
    sigma = rng.choice([0.01, 0.015, 0.02, 0.025])
    off = {k: [rng.gauss(0, sigma) for _ in range(3)] for k in pts}
    room = {k: [pts[k][i] + off[k][i] for i in range(3)] for k in pts}
    res = rigid.fit([cam[k] for k in fit_ids], [room[k] for k in fit_ids])
    rr, tt = res["rotation"], res["translation"]
    chk = [math.dist(rigid.apply(rr, tt, cam[k]), room[k]) for k in chk_ids]
    if res["max_m"] <= 0.05 and max(chk) <= 0.05 and res["loo_max_m"] > 0.05:
        if best is None or res["loo_max_m"] > best[0]:
            best = (res["loo_max_m"], res["max_m"], max(chk), sigma, off)
        cnt = (cnt + 1) if 'cnt' in dir() else 1
print("count", cnt if 'cnt' in dir() else 0, "of 4000")
print(best[:4])
print({k: [round(x, 4) for x in v] for k, v in best[4].items()})
