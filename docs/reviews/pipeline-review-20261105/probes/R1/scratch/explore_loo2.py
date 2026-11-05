import sys, random, math
sys.path.insert(0, sys.argv[1] + "/tools/den-register")
import rigid, synthetic
R, t = synthetic.camera_pose()
pts = synthetic.POINTS
fit_ids, chk_ids = synthetic.FIT, synthetic.CHECKS
cam = {k: synthetic.to_camera(R, t, v) for k, v in pts.items()}
for sigma in (0.005, 0.01, 0.015, 0.02):
    rng = random.Random(7)
    passed = loo_over = 0
    for trial in range(2000):
        off = {k: [rng.gauss(0, sigma) for _ in range(3)] for k in pts}
        room = {k: [pts[k][i] + off[k][i] for i in range(3)] for k in pts}
        res = rigid.fit([cam[k] for k in fit_ids], [room[k] for k in fit_ids])
        rr, tt = res["rotation"], res["translation"]
        chk = [math.dist(rigid.apply(rr, tt, cam[k]), room[k]) for k in chk_ids]
        if res["max_m"] <= 0.05 and max(chk) <= 0.05:
            passed += 1
            loo_over += res["loo_max_m"] > 0.05
    print(f"sigma {sigma*100:.1f} cm/axis: gate passes {passed}/2000; of those, error_bound (LOO) > 5 cm: {loo_over} ({loo_over/max(passed,1):.0%})")
