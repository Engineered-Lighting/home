"""den-live's live body: one person's points in the room frame from the camera's depth, as den-live-body-1 frames.

numpy and OpenCV (the detector's venv). body_worker.py runs it in its own process; nothing here reads a camera, writes
a file or opens a socket. Home draws the frames (app/src/home-3d/apartment-body-frame.mjs and apartment-body-layer.mjs).

Per frame, for the person den-live tracks:
1. The empty room. Learned in memory, never saved: the per-pixel median of LEARN_FRAMES frames once nobody has been
   tracked for EMPTY_S, and learned again after RELEARN_S of an empty den. Until there is one, no body is made.
2. The cut. A pixel is the person when it reads at least CUT_MM nearer than the empty room. Inside a limb capsule the
   pose places (7-11 cm), NEAR_LIMB_MM is enough: a leg resting against the sofa is still the person.
3. Solid regions only: a pixel stays when 15 of its 5x5 neighbours also passed (depth flicker comes in thin streaks),
   in regions of at least MIN_REGION_PX, within GATE_M of the person on the floor plan, between 2 cm and 2.4 m up.
4. The front: one pixel per SPACING_M on the body (staggered rows), depth smoothed over 7x7, normals from a wide
   baseline, flipped to face the camera.
5. The back, from what a body is: each row's run of body pixels is a cross-section, roughly an ellipse (depth 0.6 of
   its width for a torso, round for a limb), drawn behind the front, never past the empty room.
6. What the person holds: body pixels within WRIST_REACH_M of a wrist that the body's capsules don't explain (2.5 cm
   margin), the largest 4-connected region, at least OBJECT_MIN_M2, while the hands move slower than HAND_STILL_MPS,
   for OBJECT_FRAMES frames running. It gets no back fill.
7. Limbs nothing measured covers: points on capsules between the pose's joints, marked guessed.
Keys are depth-pixel keys (front i, back N+i, side 2N+2i+k, guessed 4N+k), so Home interpolates the same spot.
"""
import base64
import math

SCHEMA = "den-live-body-1"
HEIGHT, WIDTH = 400, 640
N = HEIGHT * WIDTH
EMPTY_S, LEARN_FRAMES, RELEARN_S = 8.0, 12, 120.0
CUT_MM, NEAR_LIMB_MM = 120, 40
NEAR_MM, FAR_MM = 300, 5000
GATE_M, MIN_REGION_PX, MIN_BODY_PX = 0.9, 300, 300
SPACING_M = 0.013   # one point per 13 mm of body: about 5-6k points for a reader, ~120 KB a frame as JSON
MAX_POINTS = 16384
WRIST_REACH_M, OBJECT_MARGIN_M, OBJECT_MIN_M2, HAND_STILL_MPS, OBJECT_FRAMES = 0.32, 0.025, 0.015, 0.3, 4
HEAD_RADIUS_M = 0.14
KIND_MEASURED, KIND_BACK, KIND_GUESSED, KIND_OBJECT = 0, 1, 3, 5
COCO = ("nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder", "left_elbow",
        "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle",
        "right_ankle")
# Where a limb lets the cut drop to NEAR_LIMB_MM: capsule radius around the bone.
NEAR_LIMBS = (("left_hip", "left_knee", 0.11), ("left_knee", "left_ankle", 0.09), ("right_hip", "right_knee", 0.11),
              ("right_knee", "right_ankle", 0.09), ("left_shoulder", "left_elbow", 0.08), ("left_elbow", "left_wrist", 0.07),
              ("right_shoulder", "right_elbow", 0.08), ("right_elbow", "right_wrist", 0.07))
# Limbs drawn as guesses where nothing measured covers them.
GUESS_LIMBS = (("left_shoulder", "left_elbow", 0.05), ("left_elbow", "left_wrist", 0.04), ("right_shoulder", "right_elbow", 0.05),
               ("right_elbow", "right_wrist", 0.04), ("left_hip", "left_knee", 0.075), ("left_knee", "left_ankle", 0.055),
               ("right_hip", "right_knee", 0.075), ("right_knee", "right_ankle", 0.055), ("left_hip", "right_hip", 0.08))
OBJECT_NEEDS = ("left_shoulder", "right_shoulder", "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip",
                "right_hip", "left_ear", "right_ear")


def _b64(array):
    return base64.b64encode(array.tobytes()).decode("ascii")


class BodyMaker:
    """One camera's body maker. left_k is the rectified-left intrinsics (fx, fy, cx, cy); room_from_left is (R, t),
    the registration's room_from_camera."""

    def __init__(self, np, cv2, *, left_k, room_from_left):
        self.np, self.cv2 = np, cv2
        fx, fy, cx, cy = (float(x) for x in left_k[:4])
        self.fx = fx
        v, u = np.mgrid[0:HEIGHT, 0:WIDTH]
        self.u, self.v = u.astype(np.int32), v.astype(np.int32)
        self.rx, self.ry = ((u - cx) / fx).astype(np.float32), ((v - cy) / fy).astype(np.float32)
        self.R, self.t = np.asarray(room_from_left[0], np.float64), np.asarray(room_from_left[1], np.float64)
        self.background = None
        self.empty_since = self.learned_at = None
        self.learning = []
        self.prev = self.prev_raw = None
        self.object_frames, self.hand_speed, self.last_wrists, self.last_at = 0, 0.0, None, None
        self.stats = {}

    # ---- frames -------------------------------------------------------------------------------------------------
    def to_room(self, cam):
        return cam @ self.R.T + self.t

    def to_left(self, room):
        return (self.np.asarray(room, float) - self.t) @ self.R

    def cam_points(self, z_mm, mask=None):
        """Camera-frame metres for the pixels in mask (or all), as an (n, 3) array."""
        np = self.np
        z = z_mm.astype(np.float32) / 1000.0
        if mask is None:
            return np.stack([self.rx * z, self.ry * z, z], -1)
        return np.stack([self.rx[mask] * z[mask], self.ry[mask] * z[mask], z[mask]], -1)

    # ---- the empty room -----------------------------------------------------------------------------------------
    def _learn(self, raw, now):
        np = self.np
        if self.empty_since is None:
            self.empty_since = now
        if now - self.empty_since < EMPTY_S:
            return
        if self.background is not None and self.learned_at is not None and now - self.learned_at < RELEARN_S:
            return
        self.learning.append(raw.copy())
        if len(self.learning) >= LEARN_FRAMES:
            stack = np.stack(self.learning).astype(np.float32)
            stack[stack == 0] = np.nan
            with np.errstate(all="ignore"):
                median = np.nanmedian(stack, axis=0)
                seen = np.sum(~np.isnan(stack), axis=0)
            self.background = np.where(seen >= LEARN_FRAMES // 2, np.nan_to_num(median), 0).astype(np.float32)
            self.learned_at, self.learning = now, []

    # ---- one frame ----------------------------------------------------------------------------------------------
    def step(self, depth_mm, *, person, now, capture_ts=None, seq=None, home="den"):
        """(frame or None, why). person: None when nobody is seen (the empty room may be learned); {track_id: None}
        when someone is seen but not tracked yet (no body, no learning); else {track_id, xy_m, joints: {name: (xyz,
        status)}} in the room frame. now: a monotonic clock (s)."""
        np, cv2 = self.np, self.cv2
        self.stats = {}
        raw = np.asarray(depth_mm, np.uint16).reshape(HEIGHT, WIDTH)
        z = raw.astype(np.float32)
        if self.prev is not None:
            close = (z > 0) & (self.prev > 0) & (np.abs(z - self.prev) < 0.04 * z)
            z = np.where(close, self.prev + 0.45 * (z - self.prev), z)
        motion = np.where((raw > 0) & (self.prev_raw > 0), np.abs(raw.astype(np.float32) - self.prev_raw), 0) if self.prev_raw is not None else np.zeros_like(z)
        self.prev, self.prev_raw = z, raw.astype(np.float32)
        if person is None:
            self._learn(raw, now)
            self.object_frames = 0
            return None, "nobody tracked"
        self.empty_since, self.learning = None, []
        if person.get("track_id") is None:
            self.object_frames = 0
            return None, "not tracked yet"
        if self.background is None:
            return None, "learning the empty room"
        b = self.background
        joints = {name: np.asarray(xyz, float) for name, (xyz, _status) in (person.get("joints") or {}).items() if xyz is not None}
        jl = {name: self.to_left(p) for name, p in joints.items()}
        valid = (z >= NEAR_MM) & (z <= FAR_MM)

        # 2. the cut, with the near-limb exception
        thr = np.full(z.shape, CUT_MM, np.float32)
        segs = [(jl[a], jl[c], r) for a, c, r in NEAR_LIMBS if a in jl and c in jl]
        if segs:
            cand = valid & (b > 0) & (z < b - NEAR_LIMB_MM) & (z >= b - CUT_MM)
            if cand.any():
                pts = self.cam_points(z, cand)
                near = np.zeros(len(pts), bool)
                for a, c, r in segs:
                    near |= _seg_dist(np, pts, a, c) < r
                thr[cand] = np.where(near, NEAR_LIMB_MM, CUT_MM)
        cut = valid & np.where(b > 0, z < b - thr, z < 3500)

        # 3. solid regions, near the person
        dens = cv2.boxFilter(cut.astype(np.float32), -1, (5, 5), normalize=False, borderType=cv2.BORDER_CONSTANT)
        mask = cut & (dens >= 15)
        if mask.sum() < MIN_BODY_PX:
            return None, "no body"
        room = np.zeros((HEIGHT, WIDTH, 3), np.float32)
        room[mask] = self.to_room(self.cam_points(z, mask))
        torso = [joints[k] for k in ("left_shoulder", "right_shoulder", "left_hip", "right_hip") if k in joints]
        centre = np.mean(torso, axis=0)[:2] if torso else np.asarray(person["xy_m"], float)
        gate = mask & (np.hypot(room[..., 0] - centre[0], room[..., 1] - centre[1]) <= GATE_M) & (room[..., 2] >= 0.02) & (room[..., 2] <= 2.4)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(gate.astype(np.uint8), connectivity=8)
        sizes = stats[1:, cv2.CC_STAT_AREA] if count > 1 else np.zeros(0, int)
        if not len(sizes) or sizes.max() < MIN_REGION_PX:
            return None, "no body"
        keep = np.zeros(count, bool)
        keep[1:] = sizes >= max(MIN_REGION_PX, 0.2 * sizes.max())
        body = keep[labels]

        # 6. the held object
        if all(k in jl for k in OBJECT_NEEDS):
            obj = self._held_object(body, z, jl, now)    # counts its own frames; None until it has held OBJECT_FRAMES
        else:
            obj, self.object_frames, self.last_wrists = None, 0, None
        if obj is None:
            obj = np.zeros_like(body)
        fill = body & ~obj

        # 4. the front
        bodyf = body.astype(np.float32)
        wsum = cv2.boxFilter(bodyf, -1, (7, 7), normalize=False, borderType=cv2.BORDER_CONSTANT)
        zsum = cv2.boxFilter(z * bodyf, -1, (7, 7), normalize=False, borderType=cv2.BORDER_CONSTANT)
        zs = np.where(wsum > 0, zsum / np.maximum(wsum, 1), z)
        step = np.maximum(1, np.rint(SPACING_M * self.fx * 1000.0 / np.maximum(z, 1))).astype(np.int32)
        stagger = ((self.v // step) & 1) * (step >> 1)
        lim = np.maximum(50.0, 0.04 * z)
        zp = np.pad(z, 1)
        nb = [zp[1:-1, :-2], zp[1:-1, 2:], zp[:-2, 1:-1], zp[2:, 1:-1]]
        smooth_edge = np.all([(n > 0) & (np.abs(n - z) <= lim) for n in nb], axis=0)
        front = body & (self.v % step == 0) & ((self.u + stagger) % step == 0) & smooth_edge
        fv, fu = np.nonzero(front)
        f_cam = np.stack([self.rx[fv, fu] * zs[fv, fu] / 1000, self.ry[fv, fu] * zs[fv, fu] / 1000, zs[fv, fu] / 1000], -1)
        f_room = self.to_room(f_cam)
        f_nrm = self._normals(z, fv, fu, step[fv, fu], f_cam)
        f_kind = np.where(obj[fv, fu], KIND_OBJECT, KIND_MEASURED).astype(np.uint8)
        f_key = (fv * WIDTH + fu).astype(np.uint32)
        f_mot = np.clip(motion[fv, fu] / 70.0, 0, 1)

        # 5. the back
        b_key, b_cam, b_ncam = self._back(fill, z, b)
        b_room = self.to_room(b_cam) if len(b_cam) else np.zeros((0, 3))
        b_nrm = b_ncam @ self.R.T if len(b_ncam) else np.zeros((0, 3))

        keys = [f_key, b_key]
        pos = [f_room, b_room]
        nrm = [f_nrm, b_nrm]
        kinds = [f_kind, np.full(len(b_key), KIND_BACK, np.uint8)]
        mots = [f_mot, np.zeros(len(b_key))]
        # 7. guessed limbs
        g_room, g_nrm = self._guesses(joints, np.concatenate(pos))
        if len(g_room):
            keys.append((4 * N + np.arange(len(g_room))).astype(np.uint32))
            pos.append(g_room); nrm.append(g_nrm); kinds.append(np.full(len(g_room), KIND_GUESSED, np.uint8)); mots.append(np.zeros(len(g_room)))
        keys, pos, nrm, kinds, mots = (np.concatenate(x) for x in (keys, pos, nrm, kinds, mots))
        inside = (pos[:, 2] >= 0.02) & (pos[:, 2] <= 2.4)
        keys, pos, nrm, kinds, mots = keys[inside], pos[inside], nrm[inside], kinds[inside], mots[inside]
        if len(keys) > MAX_POINTS:                       # front first, then back, then guesses: keys are in that order
            order = np.argsort(kinds == KIND_GUESSED, kind="stable")[:MAX_POINTS]
            keys, pos, nrm, kinds, mots = keys[order], pos[order], nrm[order], kinds[order], mots[order]
        head = np.zeros(len(keys), np.uint8)
        if "left_ear" in joints and "right_ear" in joints:
            hc = (joints["left_ear"] + joints["right_ear"]) / 2
            head[np.linalg.norm(pos - hc, axis=1) < HEAD_RADIUS_M] = 255
        order = np.argsort(keys)
        keys, pos, nrm, kinds, mots, head = keys[order], pos[order], nrm[order], kinds[order], mots[order], head[order]
        self.stats.update({"front": int(len(f_key)), "back": int(len(b_key)), "guessed": int(len(g_room)), "object": int((kinds == KIND_OBJECT).sum()),
                      "near_limb": bool(segs)})
        frame = {"schema": SCHEMA, "home": home, "track_id": str(person["track_id"])[:64], "seq": seq,
                 "capture_ts": round(float(capture_ts if capture_ts is not None else now), 3),
                 "points": {"count": int(len(keys)),
                            "key": _b64(keys.astype("<u4")),
                            "position_mm": _b64(np.clip(np.rint(pos * 1000), -32768, 32767).astype("<i2")),
                            "normal": _b64(np.clip(np.rint(nrm * 127), -127, 127).astype("i1")),
                            "kind": _b64(kinds.astype("u1")),
                            "motion": _b64(np.rint(mots * 255).astype("u1")),
                            "head": _b64(head.astype("u1"))}}
        if joints:
            frame["joints"] = [{"name": name, "xyz_m": [round(float(x), 4) for x in joints[name]],
                                "status": "measured" if status == "supported" else "uncertain"}
                               for name, (xyz, status) in (person.get("joints") or {}).items() if xyz is not None and name in COCO]
        if (kinds == KIND_OBJECT).any():
            frame["object"] = {"label": None}
        return frame, None

    def _normals(self, z, fv, fu, step, f_cam):
        """Room-frame normals by central differences over 2x the thinning step (at least 2 px), facing the camera."""
        np = self.np
        o = np.maximum(2, 2 * step)
        ul, ur = np.clip(fu - o, 0, WIDTH - 1), np.clip(fu + o, 0, WIDTH - 1)
        vu, vd = np.clip(fv - o, 0, HEIGHT - 1), np.clip(fv + o, 0, HEIGHT - 1)
        z0 = z[fv, fu]
        lim = np.maximum(60.0, 0.04 * z0) * o
        zl, zr, zu, zd = z[fv, ul], z[fv, ur], z[vu, fu], z[vd, fu]
        ok = np.all([(n > 0) & (np.abs(n - z0) < lim) for n in (zl, zr, zu, zd)], axis=0)
        p = lambda vv, uu, zz: np.stack([self.rx[vv, uu] * zz, self.ry[vv, uu] * zz, zz], -1) / 1000
        e = p(fv, ur, zr) - p(fv, ul, zl)
        f = p(vd, fu, zd) - p(vu, fu, zu)
        n = np.cross(e, f)
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
        flip = np.einsum("ij,ij->i", n, -f_cam) < 0
        n[flip] *= -1
        n[~ok] = 0
        return n @ self.R.T

    def _back(self, fill, z, b):
        """Back-fill keys, camera points and camera normals, row by row."""
        np = self.np
        keys, pts, nrms = [], [], []
        for v in np.flatnonzero(fill.any(axis=1)):
            us = np.flatnonzero(fill[v])
            zr = z[v, us]
            breaks = np.flatnonzero((np.diff(us) > 1) | (np.abs(np.diff(zr)) >= 150)) + 1
            for run in np.split(np.arange(len(us)), breaks):
                if len(run) < 3:
                    continue
                uu, zf = us[run], zr[run]
                zmed = float(np.median(zf))
                step = max(1, int(round(SPACING_M * self.fx * 1000 / zmed)))
                if v % step:
                    continue
                half, uc = len(run) / 2.0, (uu[0] + uu[-1]) / 2.0
                a_m = half * zmed / (1000 * self.fx)
                k = 0.6 if a_m >= 0.12 else 1.0 if a_m <= 0.06 else 1.0 - 0.4 * (a_m - 0.06) / 0.06
                b_mm = k * a_m * 1000
                x = (uu - uc) / half
                s = np.sqrt(np.maximum(0, 1 - x * x))
                zc = float(np.median(zf + b_mm * s))
                zb = zc + b_mm * s
                room = b[v, uu]
                zb = np.where((room > 0) & (zb > room - 20), room - 20, zb)
                stagger = ((v // step) & 1) * (step >> 1)
                take = ((uu + stagger) % step == 0) & (zb >= zf + 15)
                if take.any():
                    tu, tz = uu[take], zb[take]
                    keys.append(N + v * WIDTH + tu)
                    pts.append(np.stack([self.rx[v, tu] * tz, self.ry[v, tu] * tz, tz], -1) / 1000)
                    nx, nz = x[take] / a_m, s[take] / (k * a_m)
                    nn = np.stack([nx, np.zeros_like(nx), nz], -1)
                    nrms.append(nn / np.maximum(np.linalg.norm(nn, axis=1, keepdims=True), 1e-9))
                for end, xs in ((0, -1.0), (len(run) - 1, 1.0)):   # close the silhouette's sides
                    ue, zfe = uu[end], zf[end]
                    zbe = min(zc, b[v, ue] - 20) if b[v, ue] > 0 else zc
                    if zbe > zfe + 20:
                        for kk, f in enumerate((0.33, 0.66)):
                            ze = zfe + (zbe - zfe) * f
                            keys.append(np.array([2 * N + 2 * (v * WIDTH + ue) + kk]))
                            pts.append(np.array([[self.rx[v, ue] * ze, self.ry[v, ue] * ze, ze]]) / 1000)
                            nrms.append(np.array([[xs, 0.0, 0.0]]))
        if not keys:
            return np.zeros(0, np.uint32), np.zeros((0, 3)), np.zeros((0, 3))
        return np.concatenate(keys).astype(np.uint32), np.concatenate(pts), np.concatenate(nrms)

    def _guesses(self, joints, measured):
        """Points on limb capsules nothing measured covers (3 cm cells), with outward normals, room frame."""
        np = self.np
        C = 0.03
        if not len(measured):
            return np.zeros((0, 3)), np.zeros((0, 3))
        cells = np.floor(measured / C).astype(np.int64)
        offsets = np.array([(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)], np.int64)
        near = (cells[:, None, :] + offsets[None]).reshape(-1, 3)
        enc = lambda c: (c[:, 0] + 50000) * 10 ** 10 + (c[:, 1] + 50000) * 10 ** 5 + (c[:, 2] + 50000)
        covered = np.unique(enc(near))
        out_p, out_n = [], []
        for a, c, r in GUESS_LIMBS:
            if a not in joints or c not in joints:
                continue
            A, B = joints[a], joints[c]
            axis = B - A
            length = float(np.linalg.norm(axis))
            if length < 0.05 or length > 0.9:
                continue
            ax = axis / length
            ref = np.array([0, 0, 1.0]) if abs(ax[2]) < 0.9 else np.array([1.0, 0, 0])
            e1 = np.cross(ax, ref); e1 /= np.linalg.norm(e1)
            e2 = np.cross(ax, e1)
            rings, per = max(2, round(length / 0.028)), max(8, round(2 * math.pi * r / 0.032))
            f = np.repeat(np.arange(rings + 1) / rings, per)
            th = np.tile(np.arange(per) * 2 * math.pi / per, rings + 1) + (np.repeat(np.arange(rings + 1), per) & 1) * math.pi / per
            n = np.cos(th)[:, None] * e1 + np.sin(th)[:, None] * e2
            p = A + f[:, None] * axis + n * r
            free = ~np.isin(enc(np.floor(p / C).astype(np.int64)), covered) & (p[:, 2] >= 0.02)
            out_p.append(p[free]); out_n.append(n[free])
        if not out_p:
            return np.zeros((0, 3)), np.zeros((0, 3))
        return np.concatenate(out_p), np.concatenate(out_n)

    def _held_object(self, body, z, jl, now):
        """The held object's pixel mask once it has held OBJECT_FRAMES frames, else None."""
        np, cv2 = self.np, self.cv2
        mid = lambda a, c: (jl[a] + jl[c]) / 2
        chest, pelvis, head = mid("left_shoulder", "right_shoulder"), mid("left_hip", "right_hip"), mid("left_ear", "right_ear")
        segs = [(pelvis, chest, 0.17), (chest, head, 0.11)]
        for side in ("left", "right"):
            el, wr = jl[side + "_elbow"], jl[side + "_wrist"]
            d = wr - el
            d = d / max(float(np.linalg.norm(d)), 1e-9)
            segs += [(jl[side + "_shoulder"], el, 0.06), (el, wr, 0.045), (wr, wr + d * 0.09, 0.035)]
            if side + "_knee" in jl:
                segs.append((jl[side + "_hip"], jl[side + "_knee"], 0.095))
                if side + "_ankle" in jl:
                    segs.append((jl[side + "_knee"], jl[side + "_ankle"], 0.065))
        wl, wr = jl["left_wrist"], jl["right_wrist"]
        if self.last_wrists is not None and self.last_at is not None and now > self.last_at:
            sp = max(float(np.linalg.norm(wl - self.last_wrists[0])), float(np.linalg.norm(wr - self.last_wrists[1]))) / (now - self.last_at)
            self.hand_speed += (sp - self.hand_speed) * 0.5
        self.last_wrists, self.last_at = (wl, wr), now
        bv, bu = np.nonzero(body)
        pts = np.stack([self.rx[bv, bu] * z[bv, bu], self.ry[bv, bu] * z[bv, bu], z[bv, bu]], -1) / 1000
        reach = np.minimum(np.linalg.norm(pts - wl, axis=1), np.linalg.norm(pts - wr, axis=1)) <= WRIST_REACH_M
        explained = np.zeros(len(pts), bool)
        for a, c, r in segs:
            explained |= _seg_dist(np, pts, a, c) < r + OBJECT_MARGIN_M
        cand = np.zeros(body.shape, np.uint8)
        pick = reach & ~explained
        cand[bv[pick], bu[pick]] = 1
        count, labels, stats, _ = cv2.connectedComponentsWithStats(cand, connectivity=4)
        if count <= 1:
            self.object_frames = 0
            return None
        best = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        region = labels == best
        zr = z[region] / 1000.0
        area = float(np.sum((zr / self.fx) ** 2))
        ok = area >= OBJECT_MIN_M2 and self.hand_speed < HAND_STILL_MPS
        self.object_frames = self.object_frames + 1 if ok else 0
        self.stats["object_area_m2"] = round(area, 4)
        if self.object_frames < OBJECT_FRAMES:
            return None
        grown = region.copy()
        grown[:, 1:] |= region[:, :-1]
        grown[:, :-1] |= region[:, 1:]
        return grown & body


def _seg_dist(np, pts, a, c):
    ab = c - a
    l2 = max(float(ab @ ab), 1e-12)
    t = np.clip((pts - a) @ ab / l2, 0, 1)
    return np.linalg.norm(pts - (a + t[:, None] * ab), axis=1)
