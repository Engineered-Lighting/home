"""den-live's live body: one person's points in the room frame from the camera's depth, as den-live-body-1 frames.

numpy and OpenCV (the detector's venv). detect_worker.py runs it in the detector's own process, through BodyChannel
below, on the bundle it just detected on; nothing here reads a camera, writes a file or opens a socket. Home draws the
frames (app/src/home-3d/apartment-body-frame.mjs and apartment-body-layer.mjs).

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
        self.fx, self.k = fx, (fx, fy, cx, cy)
        self._v0 = self._u0 = 0
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
        joints = {name: np.asarray(xyz, float) for name, (xyz, _status) in (person.get("joints") or {}).items() if xyz is not None}
        torso = [joints[k] for k in ("left_shoulder", "right_shoulder", "left_hip", "right_hip") if k in joints]
        centre = np.mean(torso, axis=0)[:2] if torso else np.asarray(person["xy_m"], float)
        # Only the window the person can be in: the gate's box (GATE_M around them, floor to 2.4 m) seen by the camera.
        v0, v1, u0, u1 = self._window(centre)
        full = (self.rx, self.ry, self.u, self.v)
        self.rx, self.ry, self.u, self.v = (a[v0:v1, u0:u1] for a in full)
        self._v0, self._u0 = v0, u0
        try:
            return self._person(z[v0:v1, u0:u1], motion[v0:v1, u0:u1], self.background[v0:v1, u0:u1], joints, centre, person, now,
                                capture_ts, seq, home)
        finally:
            self.rx, self.ry, self.u, self.v = full
            self._v0 = self._u0 = 0

    def _window(self, centre):
        """(v0, v1, u0, u1): the image rows and columns the gate's box covers, with a margin; the whole frame when part of
        the box is behind or beside the camera."""
        np = self.np
        corners = np.array([[centre[0] + dx, centre[1] + dy, zz] for dx in (-GATE_M, GATE_M) for dy in (-GATE_M, GATE_M) for zz in (0.0, 2.4)])
        cam = self.to_left(corners)
        if (cam[:, 2] < 0.3).any():
            return 0, HEIGHT, 0, WIDTH
        fx, fy, cx, cy = self.k
        us, vs = cam[:, 0] / cam[:, 2] * fx + cx, cam[:, 1] / cam[:, 2] * fy + cy
        m = 12
        u0, u1 = int(max(0, np.floor(us.min()) - m)), int(min(WIDTH, np.ceil(us.max()) + m))
        v0, v1 = int(max(0, np.floor(vs.min()) - m)), int(min(HEIGHT, np.ceil(vs.max()) + m))
        if u1 - u0 < 16 or v1 - v0 < 16:
            return 0, HEIGHT, 0, WIDTH
        return v0, v1, u0, u1

    def _person(self, z, motion, b, joints, centre, person, now, capture_ts, seq, home):
        """Steps 2 to 7 on the person's window (z, motion and b cut to it; self.rx, ry, u, v are its views)."""
        np, cv2 = self.np, self.cv2
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
        room = np.zeros(z.shape + (3,), np.float32)
        room[mask] = self.to_room(self.cam_points(z, mask))
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
        f_key = (self.v[fv, fu] * WIDTH + self.u[fv, fu]).astype(np.uint32)
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
                           "near_limb": bool(segs), "window_px": int(z.size)})
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
        h, w = z.shape
        ul, ur = np.clip(fu - o, 0, w - 1), np.clip(fu + o, 0, w - 1)
        vu, vd = np.clip(fv - o, 0, h - 1), np.clip(fv + o, 0, h - 1)
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
        """Back-fill keys, camera points and camera normals: every row's runs of body pixels at once, as ellipses."""
        np = self.np
        none = (np.zeros(0, np.uint32), np.zeros((0, 3)), np.zeros((0, 3)))
        V, U = np.nonzero(fill)                     # row-major: each row's pixels left to right
        if not len(V):
            return none
        Z = z[V, U]
        h = fill.shape[0]
        counts = np.bincount(V, minlength=h)
        starts = np.cumsum(counts) - counts
        rows = np.flatnonzero(counts)
        # Each row's thinning step from its middle depth; rows the thinning skips cost nothing more.
        row_step = np.zeros(h, np.int64)
        row_step[rows] = np.maximum(1, np.rint(SPACING_M * self.fx * 1000 / Z[starts[rows] + counts[rows] // 2])).astype(np.int64)
        kept_row = np.zeros(h, bool)
        kept_row[rows] = (rows + self._v0) % row_step[rows] == 0
        pick = kept_row[V]
        V, U, Z = V[pick], U[pick], Z[pick]
        if not len(V):
            return none
        # Runs: a new one at each row, gap, or depth jump (an arm in front of the chest).
        new = np.ones(len(V), bool)
        new[1:] = (V[1:] != V[:-1]) | (U[1:] != U[:-1] + 1) | (np.abs(Z[1:] - Z[:-1]) >= 150)
        run = np.cumsum(new) - 1
        r0 = np.flatnonzero(new)
        rlen = np.diff(np.append(r0, len(V)))
        r1 = r0 + rlen - 1

        def run_median(values):                     # each run's median, by sorting within runs
            order = np.lexsort((values, run))
            return values[order][r0 + rlen // 2]

        ok = rlen >= 3
        zmed = run_median(Z)
        half = rlen / 2.0
        uc = (U[r0] + U[r1]) / 2.0
        a_m = half * zmed / (1000 * self.fx)
        k = np.where(a_m >= 0.12, 0.6, np.where(a_m <= 0.06, 1.0, 1.0 - 0.4 * (a_m - 0.06) / 0.06))
        b_mm = k * a_m * 1000
        x = (U - uc[run]) / half[run]
        sq = np.sqrt(np.maximum(0, 1 - x * x))
        zc = run_median(Z + b_mm[run] * sq)
        zb = zc[run] + b_mm[run] * sq
        room = b[V, U]
        zb = np.where((room > 0) & (zb > room - 20), room - 20, zb)
        step = row_step[V]
        gv = V + self._v0                           # the frame's own rows and columns: its thinning and its keys
        stagger = ((gv // step) & 1) * (step >> 1)
        take = ok[run] & ((U + self._u0 + stagger) % step == 0) & (zb >= Z + 15)
        tv, tu, tz = V[take], U[take], zb[take]
        keys = [N + (gv[take] * WIDTH + tu + self._u0)]
        pts = [np.stack([self.rx[tv, tu] * tz, self.ry[tv, tu] * tz, tz], -1) / 1000]
        nn = np.stack([x[take] / a_m[run[take]], np.zeros(len(tv)), sq[take] / (k * a_m)[run[take]]], -1)
        nrms = [nn / np.maximum(np.linalg.norm(nn, axis=1, keepdims=True), 1e-9)]
        for ends, xs in ((r0, -1.0), (r1, 1.0)):    # close the silhouette's sides: two points across each end
            ev, eu, zfe = V[ends], U[ends], Z[ends]
            room_e = b[ev, eu]
            zbe = np.where(room_e > 0, np.minimum(zc, room_e - 20), zc)
            side = ok & (zbe > zfe + 20)
            for kk, f in enumerate((0.33, 0.66)):
                ze = (zfe + (zbe - zfe) * f)[side]
                sv, su = ev[side], eu[side]
                keys.append(2 * N + 2 * ((sv + self._v0) * WIDTH + su + self._u0) + kk)
                pts.append(np.stack([self.rx[sv, su] * ze, self.ry[sv, su] * ze, ze], -1) / 1000)
                nrms.append(np.tile([xs, 0.0, 0.0], (len(sv), 1)))
        return np.concatenate(keys).astype(np.uint32), np.concatenate(pts), np.concatenate(nrms)

    def _guesses(self, joints, measured):
        """Points on limb capsules nothing measured covers (3 cm cells), with outward normals, room frame."""
        np = self.np
        C = 0.03
        if not len(measured):
            return np.zeros((0, 3)), np.zeros((0, 3))
        offsets = np.array([(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)], np.int64)
        enc = lambda c: (c[..., 0] + 50000) * 10 ** 10 + (c[..., 1] + 50000) * 10 ** 5 + (c[..., 2] + 50000)
        occupied = np.unique(enc(np.floor(measured / C).astype(np.int64)))   # a capsule point is covered when a cell next to it is
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
            around = enc(np.floor(p / C).astype(np.int64)[:, None, :] + offsets[None])
            free = ~np.isin(around, occupied).any(axis=1) & (p[:, 2] >= 0.02)
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
        reach = np.flatnonzero(np.minimum(np.linalg.norm(pts - wl, axis=1), np.linalg.norm(pts - wr, axis=1)) <= WRIST_REACH_M)
        near = pts[reach]
        explained = np.zeros(len(near), bool)
        for a, c, r in segs:                        # only pixels within a wrist's reach are measured against the body
            explained |= _seg_dist(np, near, a, c) < r + OBJECT_MARGIN_M
        cand = np.zeros(body.shape, np.uint8)
        pick = reach[~explained]
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


def depth_of(np, data):
    """A camera bundle's depth_mm plane (400x640 uint16, rectified-left aligned), as den-take reads it."""
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.getinfo("depth_mm.npy").file_size > 1 << 21:
            raise ValueError("depth_mm is too large")
        depth = np.load(io.BytesIO(archive.read("depth_mm.npy")), allow_pickle=False)
    if depth.dtype != np.dtype("<u2") or depth.shape != (HEIGHT, WIDTH):
        raise ValueError("depth_mm must be 400x640 uint16")
    return depth


class BodyChannel:
    """The body step's side of detect_worker.py's protocol. The worker keeps the bundle it last detected on; the loop
    then asks for that frame's body, with the person it tracked, without sending the bundle again.

    {kind: "setup_body", setup: {left_k, room_from_left, home}} -> {body_ready: true}, or {body_refused: type name}
    {kind: "body", id, now, capture_ts, seq, person}            -> {body_id, frame, why, compute_s, stats}, or
                                                                    {body_id, error: type name}
    """

    def __init__(self):
        self.maker, self.home = None, "den"

    def handle(self, header, kept):
        """kept: (frame id, bundle bytes) the worker detected last, or None."""
        import time
        if header.get("kind") == "setup_body":
            try:
                import cv2
                import numpy as np
                cv2.setNumThreads(1)   # the LA unit's CPUQuota is one and a half cores, mostly the detector's
                setup = header["setup"]
                self.maker = BodyMaker(np, cv2, left_k=setup["left_k"], room_from_left=setup["room_from_left"])
                self.home = str(setup.get("home") or "den")
                return {"body_ready": True}
            except Exception as exc:  # a type name only
                return {"body_refused": type(exc).__name__}
        body_id = header.get("id")
        try:
            if self.maker is None:
                return {"body_id": body_id, "error": "NotSetUp"}
            if kept is None or kept[0] != body_id:
                return {"body_id": body_id, "frame": None, "why": "frame not kept"}
            start = time.perf_counter()
            frame, why = self.maker.step(depth_of(self.maker.np, kept[1]), person=header.get("person"), now=float(header["now"]),
                                         capture_ts=header.get("capture_ts"), seq=header.get("seq"), home=self.home)
            return {"body_id": body_id, "frame": frame, "why": why, "compute_s": round(time.perf_counter() - start, 4),
                    "stats": self.maker.stats}
        except Exception as exc:  # one bad frame is a reply, not the end; a type name only, never its text
            return {"body_id": body_id, "error": type(exc).__name__}


def _seg_dist(np, pts, a, c):
    ab = c - a
    l2 = max(float(ab @ ab), 1e-12)
    t = np.clip((pts - a) @ ab / l2, 0, 1)
    return np.linalg.norm(pts - (a + t[:, None] * ab), axis=1)
