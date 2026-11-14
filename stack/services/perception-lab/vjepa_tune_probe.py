"""Den V-JEPA 2.1 probes on frozen tokens (AL-T, #327): numpy only.

Offline research tool, never imported by the controller release. Pure
functions and small classes: no store, network, clock, GPU or torch. It runs
the same on the workstation (synthetic tests), on LA's CPU next to the private
feature cache, or anywhere numpy exists.

Inputs are frozen V-JEPA 2.1 ViT-B tokens of one clip, pooled to a small grid
(default 4 x 12 x 12 = 576 tokens of 768 dims; any T x S x D works):

  mean        : mean over all tokens                         -> D
  mean_motion : mean plus mean |token_t+1 - token_t|         -> 2D
  attentive   : single-query multi-head attention pooling over tokens with a
                shared low-rank key projection, then a linear readout. The
                query starts near zero, so training starts at mean pooling.

Every probe is a softmax over the den classes, trained by deterministic
full- or mini-batch Adam with class weights (inverse frequency) and L2.
Calibration is one temperature fitted on held-back sessions. Smoothing runs
causally over a session's clip sequence. Buckets turn calibrated, smoothed
probabilities into the enum words JEV may receive; no number leaves.
"""
import math

import numpy as np

CLASSES = ("reading", "computer_work", "phone_use", "resting", "transition")
TEMPERATURES = tuple(round(0.5 + 0.1 * i, 2) for i in range(36))  # 0.5 .. 4.0
BUCKET_WORDS = ("high", "medium", "low")
NOT_CALIBRATED = "not_calibrated"


# ---- Features ----------------------------------------------------------------

class Subset:
    """Rows of a (memmapped) token array without copying them: [i] and [slice]
    index into the chosen rows only."""

    def __init__(self, array, rows):
        self.array, self.rows = array, np.asarray(rows, dtype=np.int64)
        self.shape = (len(self.rows),) + tuple(array.shape[1:])

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        chosen = self.rows[index]
        if np.ndim(chosen) == 0:
            return self.array[int(chosen)]
        order = np.argsort(chosen, kind="stable")
        block = self.array[chosen[order]]
        out = np.empty_like(block)
        out[order] = block
        return out

def grid_tokens(tokens, grid):
    """[N, T*S, D] -> [N, T, S, D] for the cache's (T, H, W) grid."""
    t = grid[0]
    n, length, dim = tokens.shape
    if length % t:
        raise ValueError("token count does not match the temporal grid")
    return tokens.reshape(n, t, length // t, dim)


def pooled_features(tokens, grid, kind, chunk=256):
    """Clip vectors for the linear probes (float32), computed in chunks."""
    if len(tokens) > chunk:
        return np.concatenate([pooled_features(tokens[i:i + chunk], grid, kind, chunk)
                               for i in range(0, len(tokens), chunk)])
    x = np.asarray(tokens, dtype=np.float32)
    if kind == "mean":
        return x.mean(axis=1)
    if kind == "mean_motion":
        g = grid_tokens(x, grid)
        motion = np.abs(g[:, 1:] - g[:, :-1]).mean(axis=(1, 2)) if g.shape[1] > 1 else np.zeros_like(g[:, 0, 0])
        return np.concatenate([x.mean(axis=1), motion], axis=1)
    raise ValueError("unknown pooled feature kind: %s" % kind)


class Standardizer:
    """Per-dimension mean/std from the training rows only (no fold leakage)."""

    def __init__(self, x, axis=0):
        self.mean = x.mean(axis=axis, keepdims=True)
        self.std = x.std(axis=axis, keepdims=True) + 1e-6

    def __call__(self, x):
        return (x - self.mean) / self.std


def token_standardizer(tokens, chunk=256):
    """One mean/std per dimension over every training token, accumulated in
    chunks so a float16 memmap of thousands of clips never loads at once."""
    dim = tokens.shape[-1]
    total, sq, count = np.zeros(dim), np.zeros(dim), 0
    for start in range(0, len(tokens), chunk):
        block = np.asarray(tokens[start:start + chunk], dtype=np.float64).reshape(-1, dim)
        total += block.sum(axis=0)
        sq += (block * block).sum(axis=0)
        count += len(block)
    s = Standardizer.__new__(Standardizer)
    mean = total / max(1, count)
    s.mean = mean.reshape(1, 1, -1).astype(np.float32)
    s.std = (np.sqrt(np.maximum(sq / max(1, count) - mean * mean, 0)) + 1e-6).reshape(1, 1, -1).astype(np.float32)
    return s


def class_weights(y, n_classes):
    """Inverse-frequency sample weights normalised to mean 1; absent classes 0."""
    counts = np.bincount(y, minlength=n_classes).astype(np.float64)
    per = np.where(counts > 0, counts.sum() / np.maximum(counts, 1) / max(1, (counts > 0).sum()), 0.0)
    w = per[y]
    return w / w.mean()


def softmax(z):
    z = z - z.max(axis=-1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=-1, keepdims=True)


class Adam:
    def __init__(self, params, lr):
        self.lr, self.t = lr, 0
        self.m = {k: np.zeros_like(v) for k, v in params.items()}
        self.v = {k: np.zeros_like(v) for k, v in params.items()}

    def step(self, params, grads):
        self.t += 1
        for k, g in grads.items():
            self.m[k] = 0.9 * self.m[k] + 0.1 * g
            self.v[k] = 0.999 * self.v[k] + 0.001 * g * g
            mh = self.m[k] / (1 - 0.9 ** self.t)
            vh = self.v[k] / (1 - 0.999 ** self.t)
            params[k] -= self.lr * mh / (np.sqrt(vh) + 1e-8)


# ---- Probes ------------------------------------------------------------------

class LinearProbe:
    """Multinomial logistic regression on standardised clip vectors."""

    kind = "linear"

    def __init__(self, n_classes, *, l2=1e-3, steps=400, lr=0.05, seed=0):
        self.n_classes, self.l2, self.steps, self.lr, self.seed = n_classes, l2, steps, lr, seed

    def fit(self, x, y):
        x = np.asarray(x, dtype=np.float64)
        self.scale = Standardizer(x)
        xs = self.scale(x)
        n, d = xs.shape
        Y = np.eye(self.n_classes)[y]
        w = class_weights(y, self.n_classes)[:, None]
        rng = np.random.default_rng(self.seed)
        params = {"W": rng.normal(0, 1e-3, (d, self.n_classes)), "b": np.zeros(self.n_classes)}
        opt = Adam(params, self.lr)
        for _ in range(self.steps):
            G = (softmax(xs @ params["W"] + params["b"]) - Y) * w / n
            opt.step(params, {"W": xs.T @ G + self.l2 * params["W"], "b": G.sum(axis=0)})
        self.params = params
        return self

    def logits(self, x):
        return self.scale(np.asarray(x, dtype=np.float64)) @ self.params["W"] + self.params["b"]


class AttentiveProbe:
    """Efficient attentive probe: H heads, one learned query each, a shared
    key projection D -> H*dk, values = the standardised tokens themselves, and
    a linear readout of the H pooled vectors. Parameters: D*H*dk + H*dk +
    H*D*C + C (D=768, H=2, dk=16, C=5: about 32 K)."""

    kind = "attentive"

    def __init__(self, n_classes, *, heads=2, key_dim=16, l2=1e-3, epochs=40, batch=32, lr=2e-3, seed=0):
        self.n_classes, self.heads, self.key_dim = n_classes, heads, key_dim
        self.l2, self.epochs, self.batch, self.lr, self.seed = l2, epochs, batch, lr, seed

    def _init(self, dim):
        rng = np.random.default_rng(self.seed)
        h, k = self.heads, self.key_dim
        return {"Wk": rng.normal(0, 1 / math.sqrt(dim), (dim, h * k)).astype(np.float32),
                "q": rng.normal(0, 0.01, (h, k)).astype(np.float32),
                "Wo": np.zeros((h * dim, self.n_classes), dtype=np.float32),
                "bo": np.zeros(self.n_classes, dtype=np.float32)}

    def forward(self, params, x):
        """x [B, L, D] standardised -> (logits, cache)."""
        b, length, dim = x.shape
        h, k = self.heads, self.key_dim
        keys = (x.reshape(b * length, dim) @ params["Wk"]).reshape(b, length, h, k)
        scores = (keys * params["q"]).sum(axis=-1).transpose(0, 2, 1) / math.sqrt(k)  # [B, H, L]
        attn = softmax(scores)
        pooled = np.matmul(attn, x).reshape(b, h * dim)
        return pooled @ params["Wo"] + params["bo"], (keys, attn, pooled)

    def gradients(self, params, x, Y, w):
        """Loss and exact gradients of the weighted mean cross-entropy plus L2."""
        b, length, dim = x.shape
        h, k = self.heads, self.key_dim
        z, (keys, attn, pooled) = self.forward(params, x)
        p = softmax(z.astype(np.float64))
        loss = float(-(w * np.log(p[np.arange(b), Y.argmax(1)] + 1e-12)).sum() / b)
        loss += 0.5 * self.l2 * float((params["Wk"] ** 2).sum() + (params["Wo"] ** 2).sum())
        dz = ((p - Y) * w[:, None] / b).astype(np.float32)
        grads = {"Wo": pooled.T @ dz + self.l2 * params["Wo"], "bo": dz.sum(axis=0)}
        dpooled = (dz @ params["Wo"].T).reshape(b, h, dim)
        dattn = np.matmul(dpooled, x.transpose(0, 2, 1))  # [B, H, L]
        dscores = attn * (dattn - (dattn * attn).sum(axis=-1, keepdims=True)) / math.sqrt(k)
        ds = dscores.transpose(0, 2, 1)  # [B, L, H]
        grads["q"] = (ds[..., None] * keys).sum(axis=(0, 1))
        dkeys = (ds[..., None] * params["q"]).reshape(b * length, h * k)
        grads["Wk"] = x.reshape(b * length, dim).T @ dkeys + self.l2 * params["Wk"]
        return loss, grads

    def fit(self, tokens, y):
        """tokens: an array, memmap or Subset of [N, L, D]; batches are cast to float32 one at a time."""
        self.scale = token_standardizer(tokens)
        n = len(y)
        params = self._init(tokens.shape[-1])
        Y = np.eye(self.n_classes, dtype=np.float32)[y]
        w = class_weights(y, self.n_classes).astype(np.float32)
        opt = Adam(params, self.lr)
        rng = np.random.default_rng(self.seed + 1)
        for _ in range(self.epochs):
            order = rng.permutation(n)
            for start in range(0, n, self.batch):
                idx = order[start:start + self.batch]
                idx = np.sort(idx)
                batch = self.scale(np.asarray(tokens[idx], dtype=np.float32))
                _, grads = self.gradients(params, batch, Y[idx], w[idx])
                opt.step(params, grads)
        self.params = params
        return self

    def logits(self, tokens, batch=64):
        out = [self.forward(self.params, self.scale(np.asarray(tokens[i:i + batch], dtype=np.float32)))[0]
               for i in range(0, len(tokens), batch)]
        return np.concatenate(out).astype(np.float64) if out else np.zeros((0, self.n_classes))


class Ensemble:
    """Mean logits of the same probe over several seeds."""

    def __init__(self, make, seeds):
        self.members = [make(seed) for seed in seeds]

    def fit(self, x, y):
        for member in self.members:
            member.fit(x, y)
        return self

    def logits(self, x):
        return np.mean([m.logits(x) for m in self.members], axis=0)


def build(spec, n_classes):
    """(ensemble, feature kind) from one config probe spec."""
    seeds = spec.get("seeds", [0, 1, 2])
    if spec["probe"] == "linear":
        args = {k: spec[k] for k in ("l2", "steps", "lr") if k in spec}
        return Ensemble(lambda s: LinearProbe(n_classes, seed=s, **args), seeds), spec["features"]
    if spec["probe"] == "attentive":
        args = {k: spec[k] for k in ("heads", "key_dim", "l2", "epochs", "batch", "lr") if k in spec}
        return Ensemble(lambda s: AttentiveProbe(n_classes, seed=s, **args), seeds), "tokens"
    raise ValueError("unknown probe: %s" % spec["probe"])


# ---- Calibration -------------------------------------------------------------

def nll(logits, y, temperature=1.0):
    p = softmax(logits / temperature)
    return float(-np.log(p[np.arange(len(y)), y] + 1e-12).mean())


def fit_temperature(logits, y):
    """The grid temperature with the lowest mean NLL (1.0 when no rows)."""
    if len(y) == 0:
        return 1.0
    return min(TEMPERATURES, key=lambda t: (nll(logits, y, t), abs(t - 1.0)))


# ---- Causal smoothing over one session's clip sequence --------------------------

def smooth(probabilities, times, spec):
    """Causal smoothing of [N, C] probabilities in time order. A gap longer
    than spec['max_gap_s'] (default 8 s) resets the state.

    none      : unchanged
    ema       : p_t = a * raw_t + (1 - a) * p_{t-1}           (spec['alpha'])
    window    : mean of the last k clips within the run        (spec['k'])
    hmm       : forward filter, sticky transitions             (spec['stay'])
                emission likelihood = raw_t / class prior; prior = uniform
    """
    p = np.asarray(probabilities, dtype=np.float64)
    kind = spec.get("kind", "none")
    if kind == "none" or len(p) == 0:
        return p.copy()
    order = np.argsort(times, kind="stable")
    t = np.asarray(times, dtype=np.float64)[order]
    raw = p[order]
    out = np.zeros_like(raw)
    gap = spec.get("max_gap_s", 8.0)
    c = raw.shape[1]
    state, run = None, []
    for i in range(len(raw)):
        if i == 0 or t[i] - t[i - 1] > gap:
            state, run = None, []
        if kind == "ema":
            a = spec.get("alpha", 0.5)
            state = raw[i] if state is None else a * raw[i] + (1 - a) * state
        elif kind == "window":
            run = (run + [raw[i]])[-int(spec.get("k", 3)):]
            state = np.mean(run, axis=0)
        elif kind == "hmm":
            stay = spec.get("stay", 0.9)
            transition = np.full((c, c), (1 - stay) / max(1, c - 1))
            np.fill_diagonal(transition, stay)
            prior = np.full(c, 1.0 / c) if state is None else state @ transition
            post = prior * np.maximum(raw[i], 1e-9)
            state = post / post.sum()
        else:
            raise ValueError("unknown smoothing: %s" % kind)
        out[i] = state
    result = np.empty_like(out)
    result[order] = out
    return result


# ---- Buckets and the JEV-facing facts ------------------------------------------

def precision_at(scores, positives, tau):
    chosen = scores >= tau
    n = int(chosen.sum())
    return (float(positives[chosen].mean()) if n else None), n


def wilson_lower(successes, n, z=1.959964):
    if n == 0:
        return 0.0
    p = successes / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - spread) / (1 + z * z / n)


def choose_buckets(scores, positives, targets, *, min_support, effective_n=None):
    """{'high': tau|None, 'medium': tau|None} for one class (one-vs-rest).

    A word's threshold is the smallest tau whose chosen rows reach the target
    precision, with at least min_support rows, judged on the Wilson lower
    bound when effective_n (independent trials per chosen row) is given.
    'medium' never exceeds 'high'. None means the word is never emitted."""
    scores = np.asarray(scores, dtype=np.float64)
    positives = np.asarray(positives, dtype=bool)
    taus = np.unique(np.round(scores, 4))
    result = {}
    for word in ("high", "medium"):
        best = None
        for tau in taus:
            precision, n = precision_at(scores, positives, tau)
            if precision is None or n < min_support:
                continue
            judged = precision
            if effective_n is not None:
                trials = max(1, int(n * effective_n))
                judged = wilson_lower(round(precision * trials), trials)
            if judged >= targets[word]:
                best = float(tau)
                break
        result[word] = best
    if result["high"] is not None and result["medium"] is not None:
        result["medium"] = min(result["medium"], result["high"])
    return result


def bucket(p, thresholds):
    if thresholds is None:
        return NOT_CALIBRATED
    if thresholds.get("high") is not None and p >= thresholds["high"]:
        return "high"
    if thresholds.get("medium") is not None and p >= thresholds["medium"]:
        return "medium"
    return "low"


def den_facts(probabilities, buckets, *, calibration):
    """The proposed vjepa-den-projection-2 'facts' for one (smoothed) clip:
    enum words only. probabilities: {class: p}; buckets: {class: thresholds}
    from choose_buckets, or a missing class (never emitted as high/medium)."""
    words = {c: bucket(float(probabilities[c]), buckets.get(c)) for c in CLASSES}
    supported = [c for c in CLASSES if words[c] == "high"]
    top = supported[0] if len(supported) == 1 else "mixed" if supported else "none"
    return {"activity": words, "top_high": top, "calibration": calibration}


def facts_tokens():
    """Every string den_facts may emit (for an egress allowlist)."""
    return frozenset(CLASSES) | set(BUCKET_WORDS) | {NOT_CALIBRATED, "mixed", "none", "activity", "top_high",
                                                      "calibration", "den_cv_v1", "unvalidated"}
