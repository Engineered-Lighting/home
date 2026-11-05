"""Small exact helpers for the E2 scorer: an assignment solver and the
one-sided binomial bound. Standard library only, deterministic."""
import math


def best_matching(weights):
    """Maximum-weight one-to-one matching of rows to columns.

    `weights` is a list of rows of non-negative numbers (rows may differ in
    count from columns). Returns (total, pairs) with pairs a sorted list of
    (row, col) whose weight is > 0. Ties resolve the same way every run.
    """
    rows = len(weights)
    cols = max((len(r) for r in weights), default=0)
    n = max(rows, cols)
    if n == 0:
        return 0, []
    top = max((w for r in weights for w in r), default=0)
    # Hungarian method (potentials form) on the square cost matrix top - w.
    cost = [[top - (weights[i][j] if i < rows and j < len(weights[i]) else 0) for j in range(n)] for i in range(n)]
    inf = float('inf')
    u, v = [0.0] * (n + 1), [0.0] * (n + 1)
    p, way = [0] * (n + 1), [0] * (n + 1)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minv, used = [inf] * (n + 1), [False] * (n + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], inf, 0
            for j in range(1, n + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    pairs = []
    for j in range(1, n + 1):
        i = p[j] - 1
        if i < rows and j - 1 < len(weights[i]) and weights[i][j - 1] > 0:
            pairs.append((i, j - 1))
    pairs.sort()
    return sum(weights[i][j] for i, j in pairs), pairs


def binomial_cdf(k, n, p):
    """P(X <= k) for X ~ Binomial(n, p)."""
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def upper_bound(failures, n, alpha=0.05):
    """One-sided (1 - alpha) Clopper-Pearson upper bound on a failure rate.

    With 0 failures this is exactly 1 - alpha ** (1 / n): about 0.139 at
    n = 20 and 0.095 at n = 30. None when n is 0.
    """
    if n <= 0:
        return None
    if failures >= n:
        return 1.0
    if failures == 0:
        return 1 - alpha ** (1 / n)
    lo, hi = failures / n, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if binomial_cdf(failures, n, mid) > alpha:
            lo = mid
        else:
            hi = mid
    return hi
