"""Den room map from a phone LiDAR scan (Scaniverse GLB/OBJ), and OAK-to-mesh ICP.

Local files only, numpy only (the Jetson's Lab venv has no scipy/trimesh/cv2).
Nothing here reads a camera or contacts a host; the scan stays on the machine
that runs this.

Frame, as tools/spatial-pipeline/20_register_frame.py and
app/data/apartment/frame.json: 'z_up_metric_floor0', right-handed, Z up,
metres, floor plane z=0, +X along the long axis of the floor footprint's
minimum-area rectangle, origin at that footprint's minimum corner. The scan
is glTF Y-up (Scaniverse OBJ exports are Y-up too); T_mesh maps scan file
coordinates into the frame.

Camera registration: point-to-plane ICP of a medianed OAK depth snapshot
(rectified-left metres) against surface samples of the mesh, coarse to fine,
initialized from the OAK's own floor fit (height, tilt, roll) plus a rough
camera position marked in the mesh and a heading (given or swept). Output is
a 4x4 rectified_left -> z_up_metric_floor0 matrix with a fit-quality report;
it refuses above the RMS or below the inlier thresholds.
"""
import hashlib
import json
import math
import struct
from pathlib import Path

FRAME = 'z_up_metric_floor0'
GLB_MAGIC = 0x46546C67
COMPONENTS = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}
COMPONENT_TYPES = {5120: 'i1', 5121: 'u1', 5122: '<i2', 5123: '<u2', 5125: '<u4', 5126: '<f4'}
NORMALIZE = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0}
UNSUPPORTED_EXTENSIONS = {'KHR_draco_mesh_compression', 'EXT_meshopt_compression'}
MAX_MESH_BYTES = 768*1024**2
SAMPLE_SPACING_M = 0.02
MAX_SAMPLES = 1_500_000
# ICP: (correspondence radius = voxel cell in metres, iterations) coarse to fine.
COARSE_SCHEDULE = ((0.30, 6), (0.15, 6))
FINE_SCHEDULE = ((0.30, 4), (0.15, 8), (0.08, 10), (0.04, 15))
EVAL_CELL_M = 0.05
INLIER_PLANE_M = 0.03
MAX_RMS_M = 0.03
MIN_INLIER_FRACTION = 0.5
MAX_RANGE_M = 6.0
# Scan-to-frame conversions for points clicked in viewers.
VIEWER_FRAMES = ('scan', 'den', 'blender')


def _np():
    import numpy as np
    return np


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------- loading

def load_mesh(path):
    """(vertices (N,3) float64, faces (M,3) int64) in the file's own coordinates."""
    path = Path(path)
    size = path.stat().st_size
    if not 0 < size <= MAX_MESH_BYTES:
        raise ValueError('mesh file empty or larger than the bound')
    suffix = path.suffix.lower()
    if suffix == '.glb':
        return load_glb(path.read_bytes())
    if suffix == '.obj':
        return load_obj(path.read_bytes())
    if suffix == '.gltf':
        raise ValueError('export the scan as binary .glb (or .obj), not .gltf with side files')
    raise ValueError('mesh must be .glb or .obj')


def _accessor(gltf, binary, index):
    np = _np()
    accessor = gltf['accessors'][index]
    if accessor.get('sparse'):
        raise ValueError('sparse glTF accessors are not supported')
    kind, count = accessor['componentType'], int(accessor['count'])
    if kind not in COMPONENT_TYPES or accessor['type'] not in COMPONENTS:
        raise ValueError('unsupported glTF accessor type')
    comps = COMPONENTS[accessor['type']]
    dtype = np.dtype(COMPONENT_TYPES[kind])
    view = gltf['bufferViews'][accessor['bufferView']]
    if view.get('buffer', 0) != 0:
        raise ValueError('only the GLB binary chunk is supported as a buffer')
    start = int(view.get('byteOffset', 0))+int(accessor.get('byteOffset', 0))
    element = dtype.itemsize*comps
    stride = int(view.get('byteStride') or element)
    if count and start+stride*(count-1)+element > len(binary):
        raise ValueError('glTF accessor exceeds the binary chunk')
    array = np.ndarray((count, comps), dtype=dtype, buffer=binary, offset=start, strides=(stride, dtype.itemsize)).copy()
    array = array.astype(np.float64) if accessor['type'] != 'SCALAR' or kind == 5126 else array.astype(np.int64)
    if accessor.get('normalized') and kind in NORMALIZE:
        array = np.maximum(array/NORMALIZE[kind], -1.0)
    return array


def _node_matrix(node):
    np = _np()
    if 'matrix' in node:
        return np.asarray(node['matrix'], dtype=np.float64).reshape(4, 4).T  # column-major
    x, y, z, w = node.get('rotation', [0.0, 0.0, 0.0, 1.0])
    rotation = np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                         [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                         [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])
    matrix = np.eye(4)
    matrix[:3, :3] = rotation*np.asarray(node.get('scale', [1.0, 1.0, 1.0]))[None, :]
    matrix[:3, 3] = node.get('translation', [0.0, 0.0, 0.0])
    return matrix


def load_glb(data):
    np = _np()
    if len(data) < 20:
        raise ValueError('truncated GLB')
    magic, version, length = struct.unpack_from('<III', data, 0)
    if magic != GLB_MAGIC or version != 2 or length > len(data):
        raise ValueError('not a glTF 2.0 binary (.glb)')
    offset, gltf, binary = 12, None, b''
    while offset+8 <= length:
        chunk_length, chunk_type = struct.unpack_from('<II', data, offset)
        chunk = data[offset+8:offset+8+chunk_length]
        if chunk_type == 0x4E4F534A:
            gltf = json.loads(chunk.decode('utf-8'))
        elif chunk_type == 0x004E4942:
            binary = chunk
        offset += 8+chunk_length
    if gltf is None:
        raise ValueError('GLB without a JSON chunk')
    required = set(gltf.get('extensionsRequired') or [])
    if required & UNSUPPORTED_EXTENSIONS:
        raise ValueError('compressed GLB (%s) is not supported: export .obj or an uncompressed .glb'
                         % ', '.join(sorted(required & UNSUPPORTED_EXTENSIONS)))
    nodes = gltf.get('nodes') or []
    scenes = gltf.get('scenes') or []
    if scenes:
        roots = scenes[gltf.get('scene', 0)].get('nodes') or []
    else:
        children = {c for node in nodes for c in node.get('children') or []}
        roots = [i for i in range(len(nodes)) if i not in children]
    vertices, faces, total = [], [], 0
    stack = [(i, np.eye(4)) for i in roots]
    seen = set()
    while stack:
        index, parent = stack.pop()
        if index in seen:
            raise ValueError('cyclic glTF node graph')
        seen.add(index)
        node = nodes[index]
        world = parent @ _node_matrix(node)
        if 'mesh' in node:
            for primitive in gltf['meshes'][node['mesh']].get('primitives') or []:
                if primitive.get('mode', 4) != 4 or 'POSITION' not in primitive.get('attributes', {}):
                    continue
                positions = _accessor(gltf, binary, primitive['attributes']['POSITION'])[:, :3]
                if 'indices' in primitive:
                    indices = _accessor(gltf, binary, primitive['indices']).reshape(-1)
                else:
                    indices = np.arange(len(positions))
                if len(indices) % 3 or (len(indices) and (indices.min() < 0 or indices.max() >= len(positions))):
                    raise ValueError('invalid glTF triangle indices')
                vertices.append(positions @ world[:3, :3].T+world[:3, 3])
                faces.append(indices.reshape(-1, 3)+total)
                total += len(positions)
        stack.extend((child, world) for child in node.get('children') or [])
    if not faces:
        raise ValueError('GLB has no triangle meshes')
    return np.concatenate(vertices), np.concatenate(faces).astype(np.int64)


def load_obj(data):
    """OBJ 'v' and 'f' records (polygons fan-triangulated, negative indices allowed)."""
    np = _np()
    vertices, faces = [], []
    for raw in data.splitlines():
        if raw.startswith(b'v '):
            parts = raw.split()
            vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif raw.startswith(b'f '):
            count = len(vertices)
            polygon = []
            for token in raw.split()[1:]:
                value = int(token.split(b'/')[0])
                polygon.append(value-1 if value > 0 else count+value)
            for i in range(1, len(polygon)-1):
                faces.append((polygon[0], polygon[i], polygon[i+1]))
    if not vertices or not faces:
        raise ValueError('OBJ has no vertices or faces')
    faces = np.asarray(faces, dtype=np.int64)
    if faces.min() < 0 or faces.max() >= len(vertices):
        raise ValueError('OBJ face index out of range')
    return np.asarray(vertices, dtype=np.float64), faces


# ---------------------------------------------------------------- frame

def rotation_between(a, b):
    """Rotation taking unit vector a onto unit vector b (as pipeline_util)."""
    np = _np()
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    a, b = a/np.linalg.norm(a), b/np.linalg.norm(b)
    v, c = np.cross(a, b), float(a @ b)
    if np.linalg.norm(v) < 1e-9:
        if c > 0:
            return np.eye(3)
        axis = np.cross(a, [1.0, 0, 0]) if abs(a[0]) < 0.9 else np.cross(a, [0, 1.0, 0])
        axis /= np.linalg.norm(axis)
        return 2*np.outer(axis, axis)-np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3)+vx+vx @ vx/(1+c)


def rot_z(angle_rad):
    np = _np()
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def convex_hull(points):
    """Monotone-chain hull of 2D points (counter-clockwise)."""
    np = _np()
    pts = np.unique(np.asarray(points, dtype=np.float64), axis=0)
    if len(pts) < 3:
        return pts

    def half(sequence):
        hull = []
        for p in sequence:
            while len(hull) >= 2 and ((hull[-1][0]-hull[-2][0])*(p[1]-hull[-2][1])
                                      -(hull[-1][1]-hull[-2][1])*(p[0]-hull[-2][0])) <= 0:
                hull.pop()
            hull.append(tuple(p))
        return hull
    lower, upper = half(pts), half(pts[::-1])
    return np.asarray(lower[:-1]+upper[:-1])


def min_area_rect(points):
    """(long-axis angle in radians within (0, pi], (long, short) side lengths).

    (0, pi] matches 20_register_frame.py's cv2.minAreaRect use (angle in
    (0, 90] deg, plus 90 when the first side is the short one).
    """
    np = _np()
    hull = convex_hull(points)
    if len(hull) < 3:
        raise ValueError('floor footprint is degenerate')
    best = None
    for a, b in zip(hull, np.roll(hull, -1, axis=0)):
        theta = math.atan2(b[1]-a[1], b[0]-a[0])
        rotated = hull @ rot_z(-theta)[:2, :2].T
        span = rotated.max(axis=0)-rotated.min(axis=0)
        area = float(span[0]*span[1])
        if best is None or area < best[0]-1e-12:
            best = (area, theta, span)
    _, theta, span = best
    long_axis = theta if span[0] >= span[1] else theta+math.pi/2
    long_axis = math.fmod(long_axis, math.pi)
    if long_axis <= 1e-12:
        long_axis += math.pi
    return long_axis, (float(max(span)), float(min(span)))


def scan_frame(vertices, faces, *, up='y'):
    """T_mesh (scan -> z_up_metric_floor0) and floor statistics.

    The frame follows tools/spatial-pipeline/20_register_frame.py (read-only
    port): floor normal to +Z and floor to z=0, the floor footprint's long
    axis to +X, the minimum corner of all rotated vertices to x=y=0. On the LA
    Scaniverse scan this reproduces its registration.json rotation to within
    about 1 degree.

    Floor: near-horizontal faces in the lowest quarter of the height range; the
    area-weighted height mode seeds the plane (sofa seats and low tables are
    horizontal too, but the floor has the most area), then least-squares
    refinement on faces within 3 cm.
    """
    np = _np()
    axis = {'y': [0.0, 1.0, 0.0], 'z': [0.0, 0.0, 1.0]}[up]
    up_vector = np.asarray(axis)
    v = np.asarray(vertices, dtype=np.float64)
    tri = v[np.asarray(faces)]
    cross = np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0])
    area = 0.5*np.linalg.norm(cross, axis=1)
    ok = area > 1e-12
    normals = np.zeros_like(cross)
    normals[ok] = cross[ok]/(2*area[ok, None])
    centres = tri.mean(axis=1)
    heights = centres @ up_vector
    lo, hi = float(heights.min()), float(heights.max())
    candidate = ok & (np.abs(normals @ up_vector) > 0.9) & (heights < lo+0.25*(hi-lo))
    if area[candidate].sum() < 0.5:
        raise ValueError('no floor found: check --up (Scaniverse GLB/OBJ are Y-up)')
    bins = np.arange(lo, lo+0.25*(hi-lo)+0.04, 0.02)
    histogram, edges = np.histogram(heights[candidate], bins=bins, weights=area[candidate])
    seed = float(edges[int(np.argmax(histogram))]+0.01)
    normal, point = up_vector.copy(), up_vector*seed
    for _ in range(3):
        keep = candidate & (np.abs((centres-point) @ normal) < 0.03)
        weights = area[keep]
        point = np.average(centres[keep], axis=0, weights=weights)
        d = centres[keep]-point
        normal = np.linalg.svd((d*weights[:, None]).T @ d)[0][:, -1]
        if normal @ up_vector < 0:
            normal = -normal
    residual = (centres[keep]-point) @ normal
    rms = float(np.sqrt(np.average(residual**2, weights=weights)))
    r1 = rotation_between(normal, [0.0, 0.0, 1.0])
    floor_z = float((r1 @ point)[2])
    rotated = v @ r1.T
    near_floor = rotated[np.abs(rotated[:, 2]-floor_z) < 0.06][:, :2]
    grid = np.unique(np.round(near_floor/0.02).astype(np.int64), axis=0)*0.02
    yaw, (long_side, short_side) = min_area_rect(grid)
    rotation = rot_z(-yaw) @ r1
    # Origin as 20_register_frame.py computes it: the minimum x, y of every
    # rotated vertex (its docstring says the floor's bbox; the code uses all).
    minimum = (v @ rotation.T)[:, :2].min(axis=0)
    matrix = np.eye(4)
    matrix[:3, :3] = rotation
    matrix[:3, 3] = [-minimum[0], -minimum[1], -floor_z]
    placed = v @ rotation.T+matrix[:3, 3]
    extents = placed.max(axis=0)-placed.min(axis=0)
    return {'T_mesh': [[float(x) for x in row] for row in matrix], 'frame': FRAME, 'source_up': up,
            'floor_rms_mm': round(rms*1000, 2), 'floor_area_m2': round(float(weights.sum()), 2),
            'floor_footprint_m': [round(long_side, 3), round(short_side, 3)],
            'extents_m': [round(float(x), 3) for x in extents],
            'z_range_m': [round(float(placed[:, 2].min()), 3), round(float(placed[:, 2].max()), 3)]}


def apply(matrix, points):
    np = _np()
    m = np.asarray(matrix, dtype=np.float64)
    return np.asarray(points, dtype=np.float64) @ m[:3, :3].T+m[:3, 3]


def viewer_to_den(point, frame, t_mesh):
    """A point clicked in a viewer -> den frame. 'scan' is the file's own
    coordinates (CloudCompare/MeshLab); 'blender' is Blender's default glTF/OBJ
    import, which stores file (x, y, z) as (x, -z, y)."""
    np = _np()
    p = np.asarray(point, dtype=np.float64)
    if p.shape != (3,) or not np.all(np.isfinite(p)):
        raise ValueError('finite 3D point required')
    if frame == 'den':
        return p
    if frame == 'blender':
        p = np.array([p[0], p[2], -p[1]])
    elif frame != 'scan':
        raise ValueError('frame must be one of %s' % ', '.join(VIEWER_FRAMES))
    return apply(t_mesh, p[None, :])[0]


# ---------------------------------------------------------------- surface samples

def voxel_keys(points, cell):
    np = _np()
    k = np.floor(np.asarray(points)/cell).astype(np.int64)+(1 << 20)
    if k.min() < 0 or k.max() >= (1 << 21):
        raise ValueError('points outside the voxel key range')
    return (k[:, 0] << 42) | (k[:, 1] << 21) | k[:, 2]


def voxel_downsample(points, normals, cell):
    """One representative per voxel: mean point and the principal axis of the
    normals' outer products (sign-free, so inconsistent winding does not cancel)."""
    np = _np()
    keys = voxel_keys(points, cell)
    order = np.argsort(keys, kind='stable')
    sorted_keys = keys[order]
    starts = np.flatnonzero(np.r_[True, sorted_keys[1:] != sorted_keys[:-1]])
    counts = np.diff(np.r_[starts, len(sorted_keys)])
    mean = np.add.reduceat(np.asarray(points, dtype=np.float64)[order], starts, axis=0)/counts[:, None]
    n = np.asarray(normals, dtype=np.float64)[order]
    outer = np.add.reduceat((n[:, :, None]*n[:, None, :]).reshape(-1, 9), starts, axis=0).reshape(-1, 3, 3)
    _, vectors = np.linalg.eigh(outer)
    return mean, vectors[:, :, 2], sorted_keys[starts]


def sample_surface(vertices, faces, *, spacing=SAMPLE_SPACING_M, seed=0, max_samples=MAX_SAMPLES):
    """Area-weighted surface samples, voxel-downsampled to 'spacing' (points, normals)."""
    np = _np()
    tri = np.asarray(vertices, dtype=np.float64)[np.asarray(faces)]
    cross = np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0])
    area = 0.5*np.linalg.norm(cross, axis=1)
    ok = area > 1e-12
    tri, cross, area = tri[ok], cross[ok], area[ok]
    count = int(min(max_samples, max(1000, 4*area.sum()/(spacing*spacing))))
    rng = np.random.default_rng(seed)
    chosen = rng.choice(len(tri), size=count, p=area/area.sum())
    r1, r2 = np.sqrt(rng.random(count)), rng.random(count)
    t = tri[chosen]
    points = (1-r1)[:, None]*t[:, 0]+(r1*(1-r2))[:, None]*t[:, 1]+(r1*r2)[:, None]*t[:, 2]
    normals = cross[chosen]/(2*area[chosen, None])
    # Every face also contributes its centroid, so small faces are never missed.
    points = np.concatenate([points, tri.mean(axis=1)])
    normals = np.concatenate([normals, cross/(2*area[:, None])])
    mean, normal, _ = voxel_downsample(points, normals, spacing)
    return mean, normal


class VoxelIndex:
    """Nearest representative within one cell, via 27 sorted-key lookups.

    Points are voxel-downsampled at 'cell', so every point within 'cell' of a
    query lies in the query's voxel or one of its 26 neighbours.
    """

    OFFSETS = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]

    def __init__(self, points, normals, cell):
        np = _np()
        self.cell = float(cell)
        mean, normal, keys = voxel_downsample(points, normals, cell)
        self.points, self.normals, self.keys = mean, normal, keys
        self._shift = np.array([1 << 42, 1 << 21, 1], dtype=np.int64)

    def nearest(self, queries, max_distance=None):
        np = _np()
        max_distance = self.cell if max_distance is None else min(max_distance, self.cell)
        q = np.asarray(queries, dtype=np.float64)
        base = voxel_keys(q, self.cell)
        best = np.full(len(q), np.inf)
        index = np.full(len(q), -1, dtype=np.int64)
        last = len(self.keys)-1
        for offset in self.OFFSETS:
            key = base+int(np.dot(offset, self._shift))
            position = np.minimum(np.searchsorted(self.keys, key), last)
            hit = self.keys[position] == key
            d2 = np.where(hit, ((self.points[position]-q)**2).sum(axis=1), np.inf)
            better = d2 < best
            best[better], index[better] = d2[better], position[better]
        index[best > max_distance*max_distance] = -1
        return index, np.sqrt(best)


def build_levels(points, normals, cells):
    return {cell: VoxelIndex(points, normals, cell) for cell in sorted(set(cells))}


# ---------------------------------------------------------------- ICP

def _rodrigues(omega):
    np = _np()
    theta = float(np.linalg.norm(omega))
    if theta < 1e-12:
        return np.eye(3)
    k = omega/theta
    kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3)+math.sin(theta)*kx+(1-math.cos(theta))*kx @ kx


def _orthonormal(rotation):
    np = _np()
    u, _, vt = np.linalg.svd(rotation)
    r = u @ vt
    if np.linalg.det(r) < 0:
        u[:, -1] *= -1
        r = u @ vt
    return r


def icp(source, levels, initial, schedule):
    """Point-to-plane ICP with Huber weights; returns (matrix, last-iteration stats)."""
    np = _np()
    matrix = np.asarray(initial, dtype=np.float64).copy()
    stats = {'status': 'converged', 'iterations': 0}
    for cell, iterations in schedule:
        index = levels[cell]
        for _ in range(iterations):
            moved = source @ matrix[:3, :3].T+matrix[:3, 3]
            nearest, _ = index.nearest(moved, cell)
            use = nearest >= 0
            if use.sum() < 30:
                return matrix, {'status': 'lost_correspondences', 'iterations': stats['iterations'], 'cell': cell}
            p, q, n = moved[use], index.points[nearest[use]], index.normals[nearest[use]]
            residual = ((p-q)*n).sum(axis=1)
            k = cell/3
            weight = np.where(np.abs(residual) <= k, 1.0, k/np.maximum(np.abs(residual), 1e-12))
            jacobian = np.hstack([np.cross(p, n), n])
            hessian = (jacobian*weight[:, None]).T @ jacobian
            gradient = (jacobian*weight[:, None]).T @ residual
            hessian += np.eye(6)*1e-9*max(1.0, float(np.trace(hessian)))
            step = -np.linalg.solve(hessian, gradient)
            update = np.eye(4)
            update[:3, :3], update[:3, 3] = _rodrigues(step[:3]), step[3:]
            matrix = update @ matrix
            matrix[:3, :3] = _orthonormal(matrix[:3, :3])
            stats['iterations'] += 1
            if np.linalg.norm(step[:3]) < 1e-5 and np.linalg.norm(step[3:]) < 1e-5:
                break
    return matrix, stats


def evaluate(source, index, matrix, *, inlier_plane_m=INLIER_PLANE_M):
    """Fit quality over every source point: inliers have a mesh representative
    within the index cell and a point-to-plane residual within inlier_plane_m."""
    np = _np()
    moved = source @ matrix[:3, :3].T+matrix[:3, 3]
    nearest, _ = index.nearest(moved, index.cell)
    use = nearest >= 0
    residual = np.full(len(source), np.inf)
    residual[use] = np.abs(((moved[use]-index.points[nearest[use]])*index.normals[nearest[use]]).sum(axis=1))
    inlier = residual <= inlier_plane_m
    count = int(inlier.sum())
    result = {'points': int(len(source)), 'inliers': count, 'inlier_fraction': count/max(1, len(source)),
              'rms_m': float(np.sqrt(np.mean(residual[inlier]**2))) if count else None,
              'median_abs_m': float(np.median(residual[inlier])) if count else None}
    if count >= 6:
        p = moved[inlier]
        n = index.normals[nearest[inlier]]
        jacobian = np.hstack([np.cross(p-matrix[:3, 3], n), n])
        eigen = np.linalg.eigvalsh(jacobian.T @ jacobian/count)
        result['constraint_eigen_min_max'] = [float(eigen[0]), float(eigen[-1])]
    return result


def scale_check(source, index, matrix, *, iterations=6):
    """OAK-to-mesh scale about the camera centre (7-DOF refinement, diagnostic only).

    A rigid fit hides a scale error by moving the camera, so this re-fits with
    one extra parameter: points map to c + s (R p + t - c). 'scale' < 1 means
    the OAK sees the room larger than the mesh. Scale is unidentifiable when
    only three perpendicular planes are visible; 'standard_error' says so.
    """
    np = _np()
    matrix = np.asarray(matrix, dtype=np.float64).copy()
    scale, covariance = 1.0, None
    for _ in range(iterations):
        centre = matrix[:3, 3]
        moved = centre+scale*(source @ matrix[:3, :3].T)
        nearest, _ = index.nearest(moved, index.cell)
        use = nearest >= 0
        if use.sum() < 100:
            return {'status': 'unknown', 'reason': 'too_few_correspondences'}
        p, q, n = moved[use], index.points[nearest[use]], index.normals[nearest[use]]
        residual = ((p-q)*n).sum(axis=1)
        keep = np.abs(residual) <= INLIER_PLANE_M
        p, n, residual = p[keep], n[keep], residual[keep]
        jacobian = np.hstack([np.cross(p-centre, n), n, ((p-centre)*n).sum(axis=1)[:, None]])
        hessian = jacobian.T @ jacobian+np.eye(7)*1e-9
        step = -np.linalg.solve(hessian, jacobian.T @ residual)
        sigma = float(np.sqrt(np.mean(residual**2)))
        covariance = np.linalg.inv(hessian)*sigma*sigma
        rotation = _rodrigues(step[:3])
        matrix[:3, :3] = _orthonormal(rotation @ matrix[:3, :3])
        matrix[:3, 3] = centre+step[3:6]
        scale *= math.exp(step[6])
    if covariance is None:
        return {'status': 'unknown', 'reason': 'no_iterations'}
    error = float(math.sqrt(max(covariance[6, 6], 0.0)))
    return {'status': 'estimated' if error < 0.005 else 'unconstrained', 'scale': scale, 'standard_error': error}


def level_rotation(floor_normal_camera):
    """Camera -> level frame (x right, y forward on the floor, z up)."""
    np = _np()
    up = np.asarray(floor_normal_camera, dtype=np.float64)
    up = up/np.linalg.norm(up)
    forward = np.array([0.0, 0.0, 1.0])-up[2]*up
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, up)
    return np.vstack([right, forward, up])


def initial_pose(floor, camera_xy, heading_deg):
    """Camera -> den guess: the OAK floor fit fixes height, tilt and roll; the
    marked position and heading (optical axis on the floor, degrees CCW from +X)
    fix the rest."""
    np = _np()
    matrix = np.eye(4)
    matrix[:3, :3] = rot_z(math.radians(heading_deg-90.0)) @ level_rotation(floor['normal_camera'])
    matrix[:3, 3] = [camera_xy[0], camera_xy[1], floor['camera_height_m']]
    return matrix


def pose_summary(matrix):
    np = _np()
    m = np.asarray(matrix, dtype=np.float64)
    optical = m[:3, :3] @ np.array([0.0, 0.0, 1.0])
    image_right = m[:3, :3] @ np.array([1.0, 0.0, 0.0])
    return {'camera_position_m': [round(float(v), 4) for v in m[:3, 3]],
            'heading_deg': round(math.degrees(math.atan2(optical[1], optical[0])) % 360, 2),
            'tilt_down_deg': round(-math.degrees(math.asin(max(-1.0, min(1.0, optical[2])))), 2),
            'roll_deg': round(math.degrees(math.asin(max(-1.0, min(1.0, image_right[2])))), 2)}


def _pose_distance(a, b):
    np = _np()
    rotation = a[:3, :3].T @ b[:3, :3]
    angle = math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(rotation)-1)/2))))
    return float(np.linalg.norm(a[:3, 3]-b[:3, 3])), angle


def register(source, mesh_points, mesh_normals, floor, camera_xy, *, heading_deg=None, sweep_step_deg=15,
             max_rms_m=MAX_RMS_M, min_inlier_fraction=MIN_INLIER_FRACTION, max_range_m=MAX_RANGE_M, seed=0):
    """Register camera points (rectified-left metres) to the den mesh.

    Candidate headings: heading_deg +-20 deg, or a full sweep. Each candidate
    runs a coarse ICP on a subsample; the best three run the fine schedule and
    are evaluated on every in-range point. Refuses (status 'refused') above
    max_rms_m or below min_inlier_fraction; reports the runner-up basin.
    """
    np = _np()
    source = np.asarray(source, dtype=np.float64)
    ranges = np.linalg.norm(source, axis=1)
    source = source[(ranges > 0.3) & (ranges < max_range_m)]
    if len(source) < 500:
        raise ValueError('too few in-range depth points for registration')
    rng = np.random.default_rng(seed)
    coarse_source = source[rng.choice(len(source), size=min(4000, len(source)), replace=False)]
    fine_source = source[rng.choice(len(source), size=min(15000, len(source)), replace=False)]
    cells = [c for c, _ in COARSE_SCHEDULE+FINE_SCHEDULE]+[0.10, EVAL_CELL_M]
    levels = build_levels(mesh_points, mesh_normals, cells)
    headings = ([heading_deg+d for d in (-20, -10, 0, 10, 20)] if heading_deg is not None
                else list(range(0, 360, int(sweep_step_deg))))
    coarse = []
    for heading in headings:
        start = initial_pose(floor, camera_xy, heading)
        matrix, _ = icp(coarse_source, levels, start, COARSE_SCHEDULE)
        score = evaluate(coarse_source, levels[0.10], matrix, inlier_plane_m=0.06)['inlier_fraction']
        coarse.append((score, heading, matrix))
    coarse.sort(key=lambda c: -c[0])
    finals = []
    for score, heading, matrix in coarse[:3]:
        refined, stats = icp(fine_source, levels, matrix, FINE_SCHEDULE)
        quality = evaluate(source, levels[EVAL_CELL_M], refined)
        finals.append({'matrix': refined, 'quality': quality, 'initial_heading_deg': heading,
                       'coarse_score': score, 'icp': stats})
    finals.sort(key=lambda f: (-f['quality']['inlier_fraction'], f['quality']['rms_m'] or 1.0))
    best = finals[0]
    runner = next((f for f in finals[1:] if min(_pose_distance(f['matrix'], best['matrix'])[0]/0.25,
                                                  _pose_distance(f['matrix'], best['matrix'])[1]/10) > 1), None)
    quality = best['quality']
    reasons = []
    if quality['rms_m'] is None or quality['rms_m'] > max_rms_m:
        reasons.append('rms_above_%.3f_m' % max_rms_m)
    if quality['inlier_fraction'] < min_inlier_fraction:
        reasons.append('inlier_fraction_below_%.2f' % min_inlier_fraction)
    summary = pose_summary(best['matrix'])
    start = initial_pose(floor, camera_xy, best['initial_heading_deg'])
    moved_m, turned_deg = _pose_distance(start, best['matrix'])
    warnings = []
    if moved_m > 1.0:
        warnings.append('camera ended %.2f m from the marked position: check --camera-at' % moved_m)
    height_gap = summary['camera_position_m'][2]-floor['camera_height_m']
    if abs(height_gap) > 0.05:
        warnings.append('registered camera height differs from the OAK floor fit by %.3f m' % height_gap)
    scale = scale_check(fine_source, levels[0.04], best['matrix'])
    if scale['status'] == 'estimated' and abs(scale['scale']-1) > 0.02:
        warnings.append('OAK-to-mesh scale %.3f: check the scan scale with a tape' % scale['scale'])
    if runner is not None and runner['quality']['inlier_fraction'] > quality['inlier_fraction']-0.05:
        warnings.append('another pose fits almost as well (inliers %.2f vs %.2f): check heading'
                        % (runner['quality']['inlier_fraction'], quality['inlier_fraction']))
    return {'status': 'refused' if reasons else 'accepted', 'reasons': reasons, 'warnings': warnings,
            'method': 'mesh_icp_point_to_plane', 'source_frame': 'rectified_left', 'target_frame': FRAME,
            'matrix': [[float(v) for v in row] for row in best['matrix']], **summary,
            'rms_m': quality['rms_m'], 'inlier_fraction': quality['inlier_fraction'], 'quality': quality,
            'scale_check': scale,
            'thresholds': {'max_rms_m': max_rms_m, 'min_inlier_fraction': min_inlier_fraction,
                           'inlier_plane_m': INLIER_PLANE_M, 'eval_cell_m': EVAL_CELL_M, 'max_range_m': max_range_m},
            'height_check': {'oak_floor_fit_m': floor['camera_height_m'], 'registered_m': summary['camera_position_m'][2]},
            'moved_from_init': {'metres': round(moved_m, 3), 'degrees': round(turned_deg, 2)},
            'initial': {'camera_xy': [float(v) for v in camera_xy], 'heading_deg': best['initial_heading_deg'],
                        'headings_tried': len(headings)},
            'runner_up': None if runner is None else {'inlier_fraction': runner['quality']['inlier_fraction'],
                                                       'rms_m': runner['quality']['rms_m'], **pose_summary(runner['matrix'])},
            'icp': best['icp']}


# ---------------------------------------------------------------- files

def save_mesh_map(path, points, normals, frame, *, source_name, source_sha256, spacing):
    np = _np()
    meta = {'schema': 'den-mesh-map-1', 'frame': FRAME, **frame, 'source_name': source_name,
            'source_sha256': source_sha256, 'sample_spacing_m': spacing, 'samples': int(len(points))}
    arrays = {'points': np.asarray(points, dtype=np.float32), 'normals': np.asarray(normals, dtype=np.float32),
              'meta_json': np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8)}
    if hasattr(path, 'write'):
        np.savez_compressed(path, **arrays)
    else:
        with open(path, 'wb') as target:
            np.savez_compressed(target, **arrays)
    return meta


def load_mesh_map(path):
    np = _np()
    with np.load(path, allow_pickle=False) as archive:
        meta = json.loads(archive['meta_json'].tobytes())
        if meta.get('schema') != 'den-mesh-map-1' or meta.get('frame') != FRAME:
            raise ValueError('not a den mesh map')
        return archive['points'].astype(np.float64), archive['normals'].astype(np.float64), meta


def write_obj(path, vertices, faces):
    """Geometry-only OBJ (den frame) for viewers that show picked coordinates."""
    np = _np()
    with open(path, 'w', encoding='ascii') as target:
        target.write('# den room map, z_up_metric_floor0 metres (Z up)\n')
        np.savetxt(target, vertices, fmt='v %.4f %.4f %.4f')
        np.savetxt(target, np.asarray(faces)+1, fmt='f %d %d %d')
