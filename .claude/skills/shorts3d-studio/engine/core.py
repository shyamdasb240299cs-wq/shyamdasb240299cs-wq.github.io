"""Core utilities: Blender-version compat, easing, time tracks, bulk keyframe writer, math helpers."""
import bpy, math, random
from mathutils import Vector, Quaternion, Matrix, Euler
from mathutils.bvhtree import BVHTree

V = bpy.app.version
LOG = []


def log(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s)
    print("[s3d]", s)


# ------------------------------------------------------------------ compat
def eevee_id():
    ids = {i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    return "BLENDER_EEVEE" if "BLENDER_EEVEE" in ids and V >= (5, 0, 0) else (
        "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in ids else "BLENDER_EEVEE")


def compositor_tree(scene, create=True):
    """Return a compositor node tree for 4.x (scene.node_tree) or 5.x (compositing_node_group)."""
    if hasattr(scene, "compositing_node_group"):
        ng = scene.compositing_node_group
        if ng is None and create:
            ng = bpy.data.node_groups.new("s3d_comp", "CompositorNodeTree")
            scene.compositing_node_group = ng
        return ng
    scene.use_nodes = True
    return scene.node_tree


def set_input(node, names, value):
    for n in ([names] if isinstance(names, str) else names):
        if n in node.inputs:
            node.inputs[n].default_value = value
            return True
    return False


# ------------------------------------------------------------------ easing
def clamp(x, a=0.0, b=1.0):
    return a if x < a else b if x > b else x


def smooth(t):  # smoothstep
    t = clamp(t); return t * t * (3 - 2 * t)


def minjerk(t):  # human point-to-point reach profile (bell-shaped velocity)
    t = clamp(t); return t ** 3 * (10 - 15 * t + 6 * t * t)


def ease_out(t):
    t = clamp(t); return 1 - (1 - t) ** 3


def ease_in(t):
    t = clamp(t); return t ** 3


def overshoot(t, s=1.4):
    t = clamp(t) - 1; return 1 + t * t * ((s + 1) * t + s)


EASE = {"lin": lambda t: clamp(t), "smooth": smooth, "mj": minjerk, "out": ease_out, "in": ease_in,
        "back": overshoot, "hold": lambda t: 0.0 if t < 1 else 1.0}


def lerp(a, b, t):
    return a + (b - a) * t


def vlerp(a, b, t):
    return Vector(a).lerp(Vector(b), t)


def qslerp(a, b, t):
    return Quaternion(a).slerp(Quaternion(b), t)


def bezier_arc(p0, p1, t, lift=0.0, side=None):
    """Point along a gentle arc from p0 to p1 (humans never move hands in straight lines)."""
    p0, p1 = Vector(p0), Vector(p1)
    mid = (p0 + p1) / 2 + Vector((0, 0, lift))
    if side is not None:
        mid += Vector(side)
    a = p0.lerp(mid, t); b = mid.lerp(p1, t)
    return a.lerp(b, t)


# ------------------------------------------------------------------ noise
class Noise1D:
    """Smooth deterministic 1D value noise (for breathing variation, sway, handheld cam)."""
    def __init__(self, seed=0, n=512):
        r = random.Random(seed)
        self.v = [r.uniform(-1, 1) for _ in range(n)]
        self.n = n

    def __call__(self, x):
        i = math.floor(x); f = x - i
        a = self.v[i % self.n]; b = self.v[(i + 1) % self.n]
        return lerp(a, b, smooth(f))

    def fbm(self, x, oct=3):
        s, amp, fr = 0.0, 1.0, 1.0
        for _ in range(oct):
            s += self(x * fr) * amp; amp *= 0.5; fr *= 2.0
        return s / 1.75


# ------------------------------------------------------------------ tracks
class Track:
    """Piecewise-eased keyed channel over time (seconds). value types: float | Vector | Quaternion."""
    def __init__(self, default):
        self.keys = []  # (t, value, ease_name)
        self.default = default

    def key(self, t, v, ease="smooth"):
        self.keys.append((float(t), v, ease))
        self.keys.sort(key=lambda k: k[0])
        return self

    def at(self, t):
        k = self.keys
        if not k:
            return self.default
        if t <= k[0][0]:
            return k[0][1]
        if t >= k[-1][0]:
            return k[-1][1]
        for i in range(1, len(k)):
            if t <= k[i][0]:
                t0, a, _ = k[i - 1]; t1, b, e = k[i]
                u = EASE.get(e, smooth)((t - t0) / max(1e-6, t1 - t0))
                if isinstance(a, Quaternion):
                    return a.slerp(b, u)
                if isinstance(a, Vector):
                    return a.lerp(b, u)
                return a + (b - a) * u
        return k[-1][1]

    def set_at(self, t, v, ease=None):
        """Replace the value of the key at time t (deferred targets solved in pass B)."""
        for i, (tk, vk, ek) in enumerate(self.keys):
            if abs(tk - t) < 1e-6:
                self.keys[i] = (tk, v, ease or ek); return self
        return self.key(t, v, ease or "smooth")

    def last_time(self):
        return self.keys[-1][0] if self.keys else 0.0


# ------------------------------------------------------------------ bulk keyframes
class KeyWriter:
    """Collect per-frame samples and write F-Curves in bulk (fast; Blender 4.2 .. 5.x layered actions)."""
    def __init__(self):
        self.data = {}  # (id, path, index) -> {frame: value}

    def put(self, idb, path, frame, value, index=None):
        if index is None and hasattr(value, "__len__"):
            for i, x in enumerate(value):
                self.data.setdefault((idb, path, i), {})[frame] = float(x)
        else:
            self.data.setdefault((idb, path, index or 0), {})[frame] = float(value)

    @staticmethod
    def _fcurves(idb):
        ad = idb.animation_data or idb.animation_data_create()
        act = ad.action
        if act is None:
            act = bpy.data.actions.new(f"s3d_{idb.name}")
            ad.action = act
        if hasattr(act, "layers"):  # 4.4+ layered actions
            if not act.slots:
                slot = act.slots.new(id_type=idb.id_type, name=idb.name)
            else:
                slot = act.slots[0]
            try:
                ad.action_slot = slot
            except Exception:
                pass
            layer = act.layers[0] if act.layers else act.layers.new("s3d")
            strip = layer.strips[0] if layer.strips else layer.strips.new(type="KEYFRAME")
            return strip.channelbag(slot, ensure=True).fcurves
        return act.fcurves

    def write(self):
        groups = {}
        for (idb, path, idx), fr in self.data.items():
            groups.setdefault(idb, []).append((path, idx, fr))
        n = 0
        for idb, chans in groups.items():
            fcs = self._fcurves(idb)
            for path, idx, fr in chans:
                old = fcs.find(path, index=idx)
                if old is not None:
                    fcs.remove(old)
                fc = fcs.new(path, index=idx)
                frames = sorted(fr)
                fc.keyframe_points.add(len(frames))
                co = []
                for f in frames:
                    co += [f, fr[f]]
                fc.keyframe_points.foreach_set("co", co)
                fc.keyframe_points.foreach_set("interpolation", [1] * len(frames))  # LINEAR
                fc.update(); n += 1
        self.data.clear()
        return n


# ------------------------------------------------------------------ geometry helpers
def world_bbox(ob):
    pts = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return mn, mx


def bvh_world(ob, depsgraph=None, evaluated=True):
    dg = depsgraph or bpy.context.evaluated_depsgraph_get()
    if evaluated:
        ev = ob.evaluated_get(dg)
        me = ev.to_mesh()
        verts = [ob.matrix_world @ v.co for v in me.vertices]
        polys = [tuple(p.vertices) for p in me.polygons]
        ev.to_mesh_clear()
    else:
        verts = [ob.matrix_world @ v.co for v in ob.data.vertices]
        polys = [tuple(p.vertices) for p in ob.data.polygons]
    return BVHTree.FromPolygons(verts, polys, epsilon=0.0)


def look_quat(direction, up=(0, 0, 1), track="-Z", upaxis="Y"):
    return Vector(direction).normalized().to_track_quat(track, upaxis)


def yaw_quat(yaw):
    return Quaternion((0, 0, 1), yaw)


def rest_local_rot(bone, delta_arm, parent_delta=None):
    """Convert an armature-space rotation delta (about the bone head) into a pose-bone local quaternion.
    delta_arm: total rotation applied to this bone in armature space; parent_delta: the parent's total delta."""
    B = bone.matrix_local.to_3x3()
    D = delta_arm.to_matrix() if isinstance(delta_arm, Quaternion) else delta_arm
    if parent_delta is not None:
        P = parent_delta.to_matrix() if isinstance(parent_delta, Quaternion) else parent_delta
        D = P.inverted() @ D
    return (B.inverted() @ D @ B).to_quaternion()


def ensure_coll(name, parent=None):
    c = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    p = parent or bpy.context.scene.collection
    if c.name not in p.children:
        try:
            p.children.link(c)
        except RuntimeError:
            pass
    return c


def empty(name, loc=(0, 0, 0), coll=None, size=0.05, kind="PLAIN_AXES"):
    e = bpy.data.objects.get(name) or bpy.data.objects.new(name, None)
    e.empty_display_type = kind; e.empty_display_size = size
    e.location = loc
    (coll or bpy.context.scene.collection).objects.link(e) if e.name not in (coll or bpy.context.scene.collection).objects else None
    return e


def hexcol(h, a=1.0):
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    lin = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return (lin(r), lin(g), lin(b), a)
