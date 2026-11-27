"""Rigid-body collision QA for assembly animations: no part may pass through another while it moves, and parts that
mate must touch without overlapping. Run it on every frame of an assembly/disassembly before rendering.

    groups = {"engine": [...objects...], "charge_pipe": [...], ...}   # each group moves as one rigid body
    hits = collide.check(groups, range(f0, f1 + 1), allow={("charge_pipe", "throttle")})
    collide.report(hits)            # [] means clean

Curves (pipes) and modifiers are evaluated; hidden-for-render objects are skipped on the frames they are hidden.
"""
import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree


def _tris_world(ob, dg):
    ev = ob.evaluated_get(dg)
    try:
        me = ev.to_mesh()
    except RuntimeError:
        return None
    if me is None or not len(me.polygons):
        ev.to_mesh_clear(); return None
    M = ev.matrix_world
    verts = [M @ v.co for v in me.vertices]
    polys = [tuple(p.vertices) for p in me.polygons]
    ev.to_mesh_clear()
    return verts, polys


def _visible(ob, dg):
    if ob.hide_render:
        return False
    ev = ob.evaluated_get(dg)
    return not getattr(ev, "hide_render", False)


def check(groups, frames, allow=(), scene=None, shrink=0.0):
    """Returns [(frame, groupA, groupB, n_overlapping_triangle_pairs)]. `allow`: pairs that may touch/overlap
    (e.g. a pipe welded to its flange). Static groups are cached until their transforms change."""
    sc = scene or bpy.context.scene
    allow = {tuple(sorted(p)) for p in allow}
    names = list(groups); cache = {}; hits = []
    for f in frames:
        sc.frame_set(f); dg = bpy.context.evaluated_depsgraph_get()
        trees = {}
        for g in names:
            obs = [o for o in groups[g] if o.type in ("MESH", "CURVE") and _visible(o, dg)]
            if not obs:
                continue
            key = tuple((o.name, tuple(round(x, 6) for row in o.evaluated_get(dg).matrix_world for x in row)) for o in obs)
            if g in cache and cache[g][0] == key:
                trees[g] = cache[g][1]; continue
            V, P = [], []
            for o in obs:
                r = _tris_world(o, dg)
                if not r:
                    continue
                off = len(V); V += r[0]; P += [tuple(i + off for i in p) for p in r[1]]
            if not P:
                continue
            t = BVHTree.FromPolygons(V, P, epsilon=0.0)
            cache[g] = (key, t); trees[g] = t
        ks = list(trees)
        for i in range(len(ks)):
            for j in range(i + 1, len(ks)):
                a, b = ks[i], ks[j]
                if tuple(sorted((a, b))) in allow:
                    continue
                ov = trees[a].overlap(trees[b])
                if ov:
                    hits.append((f, a, b, len(ov)))
    return hits


def report(hits, limit=40):
    if not hits:
        print("[collide] clean: no part passes through another"); return
    agg = {}
    for f, a, b, n in hits:
        agg.setdefault((a, b), []).append(f)
    for (a, b), fs in sorted(agg.items(), key=lambda kv: kv[1][0])[:limit]:
        print(f"[collide] {a} x {b}: frames {fs[0]}..{fs[-1]} ({len(fs)} frames)")


def detail(objs_a, objs_b, scene=None, n=3):
    """Which objects of two groups overlap and where (world points) - for fixing a reported hit."""
    dg = bpy.context.evaluated_depsgraph_get(); out = []
    def tree(o):
        r = _tris_world(o, dg)
        if not r:
            return None, None
        return BVHTree.FromPolygons(r[0], r[1], epsilon=0.0), r
    TA = [(o, *tree(o)) for o in objs_a if o.type in ("MESH", "CURVE") and _visible(o, dg)]
    TB = [(o, *tree(o)) for o in objs_b if o.type in ("MESH", "CURVE") and _visible(o, dg)]
    for oa, ta, ra in TA:
        if ta is None: continue
        for ob, tb, rb in TB:
            if tb is None: continue
            ov = ta.overlap(tb)
            if ov:
                pts = []
                for ia, ib in ov[:n]:
                    p = sum((ra[0][k] for k in ra[1][ia]), Vector()) / len(ra[1][ia])
                    pts.append(tuple(round(x, 3) for x in p))
                out.append((oa.name, ob.name, len(ov), pts))
    return out
