"""MakeHuman CC0 data -> rigged, face-capable character, WITHOUT the MPFB add-on.

Reads only data files (no third-party code runs):
  MH_DATA   = MPFB 'data' dir  (3dobjs/base.obj, targets/macrodetails, rigs/standard, mesh_metadata)
  MH_ASSETS = unzipped CC0 asset packs (skins/ clothes/ hair/ eyes/ eyebrows/ eyelashes/ teeth/ tongue/
              targets/faceunits (ARKit 52) targets/visemes (Meta 15))
Result (collection CHAR_<name>):
  <name>_rig   armature, Mixamo bone names (mixamorig:*) + eye.L/eye.R
  <name>_body  mesh with shape keys '!ex-<arkitName>' and 'viseme_<X>' (same names MPFB uses)
  proxies      eyes/brows/lashes/teeth/tongue/hair/clothes, fitted + skinned; face keys follow via drivers
"""
import bpy, os, json, gzip
import numpy as np

SCALE = 0.1  # MakeHuman decimeters -> meters


def _mh2bl(a):
    a = np.asarray(a, dtype=np.float64).reshape(-1, 3)
    return np.stack([a[:, 0], -a[:, 2], a[:, 1]], 1) * SCALE


# ---------------------------------------------------------------- parsing
def parse_obj(path):
    vs, vts, faces, fuv = [], [], [], []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("v "):
                p = line.split(); vs.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("vt "):
                p = line.split(); vts.append((float(p[1]), float(p[2])))
            elif line.startswith("f "):
                fi, ti = [], []
                for t in line.split()[1:]:
                    q = t.split("/")
                    fi.append(int(q[0]) - 1)
                    ti.append(int(q[1]) - 1 if len(q) > 1 and q[1] else -1)
                faces.append(fi); fuv.append(ti)
    return np.array(vs), (np.array(vts) if vts else None), faces, fuv


def read_target(path):
    op = gzip.open if path.endswith(".gz") else open
    idx, off = [], []
    with op(path, "rt") as f:
        for line in f:
            if line[:1].isdigit():
                p = line.split()
                idx.append(int(p[0])); off.append((float(p[1]), float(p[2]), float(p[3])))
    return np.array(idx, dtype=np.int64), _mh2bl(off)


def parse_mhclo(path):
    m = {"refs": [], "w": [], "off": [], "delete": [], "scales": {}, "material": None, "obj": None,
         "dir": os.path.dirname(path), "name": os.path.basename(path)[:-6]}
    status = ""
    with open(path, encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            w = line.split()
            if status == "v":
                if w[0].isnumeric():
                    if len(w) == 1:
                        v = int(w[0]); m["refs"].append((v, v, v)); m["w"].append((1, 0, 0)); m["off"].append((0, 0, 0))
                    else:
                        m["refs"].append((int(w[0]), int(w[1]), int(w[2])))
                        m["w"].append((float(w[3]), float(w[4]), float(w[5])))
                        m["off"].append((float(w[6]), float(w[7]), float(w[8])))
                    continue
                status = ""
            elif status == "d":
                if w[0].isnumeric() or w[0] == "-":
                    seq, prev = False, None
                    for t in w:
                        if t == "-":
                            seq = True
                        else:
                            v = int(t)
                            if seq and prev is not None:
                                m["delete"].extend(range(prev, v + 1)); seq = False
                            else:
                                m["delete"].append(v)
                            prev = v
                    continue
                status = ""
            k = w[0]
            if k == "obj_file": m["obj"] = os.path.join(m["dir"], w[1])
            elif k == "material": m["material"] = os.path.join(m["dir"], w[1])
            elif k in ("x_scale", "y_scale", "z_scale"): m["scales"][k] = (int(w[1]), int(w[2]), float(w[3]))
            elif k == "verts": status = "v"
            elif k == "delete_verts": status = "d"
            elif k == "name": m["name"] = w[1]
    m["refs"] = np.array(m["refs"], dtype=np.int64); m["w"] = np.array(m["w"]); m["off"] = np.array(m["off"])
    return m


def parse_mhmat(path):
    d = {"_dir": os.path.dirname(path)}
    if not path or not os.path.exists(path):
        return d
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            p = line.strip().split(None, 1)
            if len(p) == 2 and not p[0].startswith(("#", "//")):
                d[p[0]] = p[1]
    return d


def _tex_path(mat, key):
    v = mat.get(key)
    if not v:
        return None
    for cand in (os.path.join(mat["_dir"], v), os.path.join(mat["_dir"], os.path.basename(v))):
        if os.path.exists(cand):
            return cand
    return None


def fit_proxy(m, coords):
    """coords: (N,3) basemesh positions (Blender space, meters)."""
    def size(key, axis):
        if key in m["scales"]:
            a, b, d = m["scales"][key]
            return abs(coords[a, axis] - coords[b, axis]) / d
        return SCALE
    sx, sy, sz = size("x_scale", 0), size("y_scale", 2), size("z_scale", 1)  # y_scale=vertical, z_scale=depth
    r, w, o = m["refs"], m["w"], m["off"]
    pos = (coords[r[:, 0]] * w[:, 0:1] + coords[r[:, 1]] * w[:, 1:2] + coords[r[:, 2]] * w[:, 2:3])
    pos += np.stack([o[:, 0] * sx, -o[:, 2] * sz, o[:, 1] * sy], 1)
    return pos


# ---------------------------------------------------------------- blender helpers
def _mesh(name, verts, faces, fuv, vts, coll):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], faces)
    if vts is not None and len(vts):
        uv = me.uv_layers.new(name="UVMap")
        flat = []
        for ti in fuv:
            for t in ti:
                flat.extend(vts[t] if t >= 0 else (0.0, 0.0))
        uv.data.foreach_set("uv", flat)
    me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    me.update()
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    return ob


def _img(path):
    if not path:
        return None
    return bpy.data.images.load(path, check_existing=True)


def _set(node, name, val):
    if name in node.inputs:
        node.inputs[name].default_value = val


def make_material(name, kind, mat):
    """kind: skin|eye|teeth|tongue|hair|brow|cloth"""
    M = bpy.data.materials.new(name)
    M.use_nodes = True
    nt = M.node_tree; n = nt.nodes; L = nt.links
    bsdf = n.get("Principled BSDF")
    tex = _img(_tex_path(mat, "diffuseTexture"))
    uvn = None
    if tex:
        t = n.new("ShaderNodeTexImage"); t.image = tex; t.location = (-500, 250)
        if kind == "skin":
            hs = n.new("ShaderNodeHueSaturation"); hs.location = (-250, 250)
            hs.inputs["Saturation"].default_value = 1.08; hs.inputs["Value"].default_value = 1.0
            L.new(t.outputs["Color"], hs.inputs["Color"]); L.new(hs.outputs["Color"], bsdf.inputs["Base Color"])
        else:
            L.new(t.outputs["Color"], bsdf.inputs["Base Color"])
        if kind in ("hair", "brow") or mat.get("transparent", "False") == "True":
            L.new(t.outputs["Alpha"], bsdf.inputs["Alpha"])
            if hasattr(M, "surface_render_method"):
                M.surface_render_method = "DITHERED"
            elif hasattr(M, "blend_method"):
                M.blend_method = "HASHED"
    nrm = _tex_path(mat, "normalmapTexture")
    if nrm:
        ti = n.new("ShaderNodeTexImage"); ti.image = _img(nrm); ti.image.colorspace_settings.name = "Non-Color"
        nm = n.new("ShaderNodeNormalMap"); nm.inputs["Strength"].default_value = 0.6
        L.new(ti.outputs["Color"], nm.inputs["Color"]); L.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    presets = {
        "skin": dict(r=0.42, sss=0.22, spec=0.45),
        "eye": dict(r=0.04, sss=0.0, spec=0.6, coat=1.0),
        "teeth": dict(r=0.25, sss=0.12, spec=0.5),
        "tongue": dict(r=0.32, sss=0.3, spec=0.5),
        "hair": dict(r=0.38, sss=0.0, spec=0.35),
        "brow": dict(r=0.5, sss=0.0, spec=0.2),
        "cloth": dict(r=0.82, sss=0.0, spec=0.25, sheen=0.35),
    }[kind]
    _set(bsdf, "Roughness", presets["r"])
    _set(bsdf, "Specular IOR Level", presets["spec"])
    if presets.get("sss"):
        _set(bsdf, "Subsurface Weight", presets["sss"])
        _set(bsdf, "Subsurface Radius", (1.0, 0.35, 0.18))
        _set(bsdf, "Subsurface Scale", 0.012)
    if presets.get("coat"):
        _set(bsdf, "Coat Weight", presets["coat"]); _set(bsdf, "Coat Roughness", 0.02)
    if presets.get("sheen"):
        _set(bsdf, "Sheen Weight", presets["sheen"]); _set(bsdf, "Sheen Roughness", 0.4)
    if kind == "skin" and not nrm:  # micro pores: tiny procedural bump, UV independent
        co = n.new("ShaderNodeTexCoord"); nz = n.new("ShaderNodeTexNoise"); bp = n.new("ShaderNodeBump")
        nz.inputs["Scale"].default_value = 900.0; nz.inputs["Detail"].default_value = 4.0
        bp.inputs["Strength"].default_value = 0.035; bp.inputs["Distance"].default_value = 0.0004
        co.location = (-900, -300); nz.location = (-700, -300); bp.location = (-300, -300)
        L.new(co.outputs["Object"], nz.inputs["Vector"]); L.new(nz.outputs["Fac"], bp.inputs["Height"])
        L.new(bp.outputs["Normal"], bsdf.inputs["Normal"])
    M["s3d_kind"] = kind
    return M


def _assign_groups(ob, names, W, thresh=1e-3):
    """W: (nbones, nverts) dense weights."""
    for bi, bn in enumerate(names):
        col = W[bi]
        nz = np.nonzero(col > thresh)[0]
        if not len(nz):
            continue
        vg = ob.vertex_groups.new(name=bn)
        vals = np.round(col[nz], 3)
        for val in np.unique(vals):
            vg.add(nz[vals == val].tolist(), float(val), "REPLACE")


def _armature_mod(ob, rig):
    mod = ob.modifiers.new("Armature", "ARMATURE"); mod.object = rig
    ob.parent = rig
    return mod


# ---------------------------------------------------------------- macro
def macro_target_stack(data_dir, macro):
    cfg = json.load(open(os.path.join(data_dir, "targets", "macrodetails", "macro.json")))["macrotargets"]

    def comps(name, value):
        out = []
        for p in cfg[name]["parts"]:
            if p["lowest"] < value < p["highest"]:
                pct = (value - p["lowest"]) / (p["highest"] - p["lowest"])
                if p["low"]: out.append((p["low"], 1 - pct))
                if p["high"]: out.append((p["high"], pct))
        return out

    c = {k: comps(k, macro[k]) for k in ["gender", "age", "muscle", "weight", "proportions", "height", "cupsize", "firmness"]}
    T = []
    for race, rw in macro["race"].items():
        if rw > 1e-4:
            for a, aw in c["age"]:
                for g, gw in c["gender"]:
                    T.append((f"macrodetails/{race}-{g}-{a}", rw * gw * aw))
    for g, gw in c["gender"]:
        for a, aw in c["age"]:
            for m, mw in c["muscle"]:
                for wt, ww in c["weight"]:
                    base = gw * aw * mw * ww
                    T.append((f"macrodetails/universal-{g}-{a}-{m}-{wt}", base))
                    for h, hw in c["height"]:
                        T.append((f"macrodetails/height/{g}-{a}-{m}-{wt}-{h}", base * hw))
                    for pr, pw in c["proportions"]:
                        T.append((f"macrodetails/proportions/{g}-{a}-{m}-{wt}-{pr}", base * pw))
                    if g == "female":
                        for cu, cw in c["cupsize"]:
                            for fi, fw in c["firmness"]:
                                n = f"breast/{g}-{a}-{m}-{wt}-{cu}-{fi}"
                                if "averagecup-averagefirmness" in n or "-baby-" in n:
                                    continue
                                T.append((n, base * cw * fw))
    return [(n, w) for n, w in T if w > 0.01]


DEFAULT_MACRO = {"gender": 1.0, "age": 0.5, "muscle": 0.55, "weight": 0.5, "proportions": 0.75, "height": 0.55,
                 "cupsize": 0.5, "firmness": 0.5, "race": {"caucasian": 0.7, "african": 0.15, "asian": 0.15}}

DEFAULT_OUTFIT = {
    "skin": "skins/young_caucasian_male/young_caucasian_male.mhmat",
    "eyes": "eyes/high-poly/high-poly.mhclo", "eye_material": "eyes/materials/brown.mhmat",
    "eyebrows": "eyebrows/eyebrow001/eyebrow001.mhclo", "eyelashes": "eyelashes/eyelashes01/eyelashes01.mhclo",
    "teeth": "teeth/teeth_base/teeth_base.mhclo", "tongue": "tongue/tongue01/tongue01.mhclo",
    "hair": "hair/short02/short02.mhclo",
    "clothes": ["clothes/male_casualsuit06/male_casualsuit06.mhclo", "clothes/shoes01/shoes01.mhclo"],
}
FACE_PROXIES = ("eyebrows", "eyelashes", "teeth", "tongue", "eyes")


# ---------------------------------------------------------------- main
def build_character(name, mh_data, mh_assets, macro=None, outfit=None, face_keys=True, log=print):
    macro = {**DEFAULT_MACRO, **(macro or {})}
    outfit = {**DEFAULT_OUTFIT, **(outfit or {})}
    coll = bpy.data.collections.new(f"CHAR_{name}")
    bpy.context.scene.collection.children.link(coll)

    # 1. basemesh + macro shaping (baked into basis)
    V, VT, F, FUV = parse_obj(os.path.join(mh_data, "3dobjs", "base.obj"))
    base = _mh2bl(V)
    for tname, w in macro_target_stack(mh_data, macro):
        p = os.path.join(mh_data, "targets", tname + ".target.gz")
        if not os.path.exists(p):
            p = p[:-3]
        if os.path.exists(p):
            idx, off = read_target(p)
            base[idx] += off * w
        else:
            log("missing target", tname)
    vgroups = json.load(open(os.path.join(mh_data, "mesh_metadata", "basemesh_vertex_groups.json")))

    def grp(gname):
        out = []
        for a, b in vgroups.get(gname, []):
            out.extend(range(a, b + 1))
        return np.array(out, dtype=np.int64)

    body_idx = grp("body")
    base[:, 2] -= base[body_idx, 2].min()  # feet on ground
    body = _mesh(f"{name}_body", base, F, FUV, VT, coll)
    cubes = {g: base[grp(g)].mean(0) for g in vgroups if g.startswith("joint-")}

    # 2. rig (Mixamo naming) + eye bones
    rigdef = json.load(open(os.path.join(mh_data, "rigs", "standard", "rig.mixamo.json")))["bones"]

    def loc(info):
        s = info.get("strategy"); p = None
        if s == "CUBE": p = cubes.get(info["cube_name"])
        elif s == "MEAN" and info.get("vertex_indices"): p = base[info["vertex_indices"]].mean(0)
        elif s == "VERTEX": p = base[info["vertex_index"]]
        elif s == "XYZ": vv = base[info["vertex_indices"][:3]]; p = np.array([vv[0][0], vv[1][1], vv[2][2]])
        if p is None: p = np.array(info["default_position"])
        if "offset" in info: p = p + np.array(info["offset"])
        return tuple(float(x) for x in p)

    arm = bpy.data.armatures.new(f"{name}_rig")
    rig = bpy.data.objects.new(f"{name}_rig", arm)
    coll.objects.link(rig)
    vl = bpy.context.view_layer
    vl.objects.active = rig; rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.edit_bones
    for bn, bi in rigdef.items():
        b = eb.new(bn); b.head = loc(bi["head"]); b.tail = loc(bi["tail"]); b.roll = bi.get("roll", 0.0)
    for bn, bi in rigdef.items():
        if bi.get("parent"):
            eb[bn].parent = eb[bi["parent"]]
            eb[bn].use_connect = bool(bi.get("use_connect", False))
    head = eb["mixamorig:Head"]
    for side, cube in (("L", "joint-l-eye"), ("R", "joint-r-eye")):
        c = cubes[cube]
        b = eb.new(f"eye.{side}"); b.head = tuple(c); b.tail = (c[0], c[1] - 0.025, c[2]); b.parent = head
    bpy.ops.object.mode_set(mode="OBJECT")
    rig["s3d_rig"] = "mixamo"; rig["s3d_char"] = name
    if "joint-mouth" in cubes:
        rig["s3d_mouth"] = [float(x) for x in cubes["joint-mouth"]]
    bone_names = [b.name for b in arm.bones]
    bidx = {n: i for i, n in enumerate(bone_names)}

    # 3. weights
    wj = json.load(open(os.path.join(mh_data, "rigs", "standard", "weights.mixamo.json")))["weights"]
    W = np.zeros((len(bone_names), len(base)), dtype=np.float32)
    for bn, pairs in wj.items():
        if bn in bidx and pairs:
            a = np.array(pairs); W[bidx[bn], a[:, 0].astype(np.int64)] = a[:, 1]
    # helper geometry (tights/skirt/hair/teeth helpers) carries no weights -> borrow from nearest body vertex,
    # otherwise clothes fitted to helpers would stay behind when the body moves (torn pants, floating shoes)
    empty = np.nonzero(W.sum(0) == 0)[0]
    if len(empty):
        from mathutils.kdtree import KDTree
        kd = KDTree(len(body_idx))
        for i in body_idx:
            kd.insert(base[i], int(i))
        kd.balance()
        for i in empty:
            _, j, _ = kd.find(base[i])
            W[:, i] = W[:, j]
    s = W.sum(0); s[s == 0] = 1; W /= s
    _assign_groups(body, bone_names, W)
    _armature_mod(body, rig)

    # 4. face shape keys (ARKit + visemes), MPFB-compatible names
    key_offsets = {}
    if face_keys:
        body.shape_key_add(name="Basis", from_mix=False)
        for sub, prefix in (("faceunits", "!ex-"), ("visemes", "")):
            d = os.path.join(mh_assets, "targets", sub)
            if not os.path.isdir(d):
                log("no", sub, "pack"); continue
            for fn in sorted(os.listdir(d)):
                if not fn.endswith(".target"):
                    continue
                kname = prefix + fn[:-7]
                idx, off = read_target(os.path.join(d, fn))
                sk = body.shape_key_add(name=kname, from_mix=False); sk.value = 0.0  # 5.x defaults new keys to 1.0
                co = base.copy(); co[idx] += off
                sk.data.foreach_set("co", co.ravel())
                key_offsets[kname] = (idx, off)

    # 5. proxies
    deleted = set()
    proxies = {}

    def add_proxy(kind, rel, matrel=None):
        p = os.path.join(mh_assets, rel)
        if not os.path.exists(p):
            log("missing proxy", rel); return None
        m = parse_mhclo(p)
        pv, pvt, pf, pfuv = parse_obj(m["obj"])
        if len(pv) != len(m["refs"]):
            log("proxy vert mismatch", rel); return None
        pos = fit_proxy(m, base)
        ob = _mesh(f"{name}_{kind}_{m['name']}", pos, pf, pfuv, pvt, coll)
        deleted.update(m["delete"])
        mat = parse_mhmat(os.path.join(mh_assets, matrel) if matrel else m["material"])
        mkind = {"eyes": "eye", "eyebrows": "brow", "eyelashes": "brow", "teeth": "teeth", "tongue": "tongue",
                 "hair": "hair"}.get(kind, "cloth")
        ob.data.materials.append(make_material(f"{name}_{kind}_mat", mkind, mat))
        # skin weights: interpolate from body (eyes: rigid to eye bones)
        if kind == "eyes":
            for side, sel in (("L", pos[:, 0] > 0), ("R", pos[:, 0] <= 0)):
                vg = ob.vertex_groups.new(name=f"eye.{side}"); vg.add(np.nonzero(sel)[0].tolist(), 1.0, "REPLACE")
        else:
            r, w = m["refs"], m["w"]
            PW = W[:, r[:, 0]] * w[:, 0] + W[:, r[:, 1]] * w[:, 1] + W[:, r[:, 2]] * w[:, 2]
            s2 = PW.sum(0); s2[s2 == 0] = 1; PW /= s2
            _assign_groups(ob, bone_names, PW)
        _armature_mod(ob, rig)
        if kind in FACE_PROXIES and kind != "eyes" and key_offsets:
            ob.shape_key_add(name="Basis", from_mix=False)
            for kname, (idx, off) in key_offsets.items():
                co = base.copy(); co[idx] += off
                ppos = fit_proxy(m, co)
                if np.abs(ppos - pos).max() < 2e-5:
                    continue
                sk = ob.shape_key_add(name=kname, from_mix=False); sk.value = 0.0; sk.data.foreach_set("co", ppos.ravel())
                fc = sk.driver_add("value"); drv = fc.driver; drv.type = "AVERAGE"
                var = drv.variables.new(); var.type = "SINGLE_PROP"
                var.targets[0].id_type = "KEY"; var.targets[0].id = body.data.shape_keys
                var.targets[0].data_path = f'key_blocks["{kname}"].value'
        if kind not in ("eyebrows", "eyelashes", "hair", "eyes", "teeth", "tongue"):
            sd = ob.modifiers.new("Subdiv", "SUBSURF"); sd.levels = 0; sd.render_levels = 1
        ob["s3d_part"] = kind
        proxies.setdefault(kind, []).append(ob)
        return ob

    add_proxy("eyes", outfit["eyes"], outfit.get("eye_material"))
    for k in ("eyebrows", "eyelashes", "teeth", "tongue", "hair"):
        if outfit.get(k):
            add_proxy(k, outfit[k])
    for c in outfit.get("clothes", []):
        add_proxy("clothes", c)

    # 6. hide helpers + body under clothes, skin material, subdivision
    vis = np.zeros(len(base), dtype=bool); vis[body_idx] = True
    if deleted:
        vis[np.array(sorted(deleted), dtype=np.int64)] = False
    vg = body.vertex_groups.new(name="s3d_visible"); vg.add(np.nonzero(vis)[0].tolist(), 1.0, "REPLACE")
    mk = body.modifiers.new("HideHelpers", "MASK"); mk.vertex_group = "s3d_visible"
    sd = body.modifiers.new("Subdiv", "SUBSURF"); sd.levels = 0; sd.render_levels = 1
    body.data.materials.append(make_material(f"{name}_skin", "skin", parse_mhmat(os.path.join(mh_assets, outfit["skin"]))))
    body["s3d_part"] = "body"
    rig["s3d_height"] = float(base[body_idx, 2].max())
    log(f"character {name}: height {rig['s3d_height']:.2f} m, bones {len(bone_names)}, "
        f"face keys {len(key_offsets)}, proxies {sum(len(v) for v in proxies.values())}")
    return coll, rig, body


if __name__ == "__main__":  # blender -b -P mhchar.py -- name mh_data mh_assets out.blend [macro.json]
    import sys
    a = sys.argv[sys.argv.index("--") + 1:]
    macro = json.load(open(a[4])) if len(a) > 4 else None
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    build_character(a[0], a[1], a[2], macro=macro)
    bpy.ops.wm.save_as_mainfile(filepath=a[3])
