"""Procedural powertrain parts for explainers: V8 (block, heads, valve covers, intake plenum, exhaust manifolds,
front accessories, moving pistons/rods/crank for x-ray), turbocharger parts (compressor wheel with splitter blades,
turbine wheel, shaft, centre housing, snail volutes), intercooler, wastegate, pipes that can 'grow', and flow streams
(Geometry Nodes particles running along a curve: blue intake air, orange exhaust).

Engine space: X = left, Y = back (front of engine at -Y, like vehicle canonical space), Z = up, origin on the crank axis.
All sizes are real (metres). Materials are physically based so the same parts look right in a studio or inside a car.
"""
import bpy, math, bmesh
import numpy as np
from mathutils import Vector, Matrix, Euler, Quaternion
from . import core

# ------------------------------------------------------------------ materials
def _mat(name):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True; nt = m.node_tree; nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial"); b = nt.nodes.new("ShaderNodeBsdfPrincipled"); nt.links.new(b.outputs[0], out.inputs[0])
    return m, nt, b, out


def _bump(nt, b, scale, strength, detail=8.0, kind="noise"):
    tc = nt.nodes.new("ShaderNodeTexCoord")
    t = nt.nodes.new("ShaderNodeTexNoise") if kind == "noise" else nt.nodes.new("ShaderNodeTexVoronoi")
    t.inputs["Scale"].default_value = scale
    if kind == "noise":
        t.inputs["Detail"].default_value = detail
    nt.links.new(tc.outputs["Object"], t.inputs["Vector"])
    bp = nt.nodes.new("ShaderNodeBump"); bp.inputs["Strength"].default_value = strength
    nt.links.new(t.outputs[0], bp.inputs["Height"]); nt.links.new(bp.outputs[0], b.inputs["Normal"])
    return t


def mats(brand="#2EC4E6"):
    M = {}
    m, nt, b, _ = _mat("pt_cast_alu"); core.set_input(b, "Base Color", (0.55, 0.56, 0.57, 1)); core.set_input(b, "Metallic", 1.0)
    core.set_input(b, "Roughness", 0.42); _bump(nt, b, 900.0, 0.12); M["alu"] = m
    m, nt, b, _ = _mat("pt_machined"); core.set_input(b, "Base Color", (0.8, 0.8, 0.82, 1)); core.set_input(b, "Metallic", 1.0)
    core.set_input(b, "Roughness", 0.18); core.set_input(b, "Anisotropic", 0.6); M["machined"] = m
    m, nt, b, _ = _mat("pt_iron"); core.set_input(b, "Base Color", (0.13, 0.12, 0.115, 1)); core.set_input(b, "Metallic", 0.85)
    core.set_input(b, "Roughness", 0.62); _bump(nt, b, 1200.0, 0.2); M["iron"] = m
    m, nt, b, _ = _mat("pt_steel"); core.set_input(b, "Base Color", (0.7, 0.7, 0.72, 1)); core.set_input(b, "Metallic", 1.0)
    core.set_input(b, "Roughness", 0.22); M["steel"] = m
    m, nt, b, _ = _mat("pt_crinkle"); core.set_input(b, "Base Color", (0.018, 0.018, 0.02, 1)); core.set_input(b, "Roughness", 0.5)
    _bump(nt, b, 2500.0, 0.35); M["crinkle"] = m
    m, nt, b, _ = _mat("pt_rubber"); core.set_input(b, "Base Color", (0.02, 0.02, 0.022, 1)); core.set_input(b, "Roughness", 0.7); M["rubber"] = m
    m, nt, b, _ = _mat("pt_brand"); core.set_input(b, "Base Color", core.hexcol(brand)); core.set_input(b, "Metallic", 0.9)
    core.set_input(b, "Roughness", 0.28); core.set_input(b, "Coat Weight", 0.6); M["brand"] = m
    m, nt, b, _ = _mat("pt_inconel"); core.set_input(b, "Base Color", (0.32, 0.3, 0.28, 1)); core.set_input(b, "Metallic", 1.0)
    core.set_input(b, "Roughness", 0.35); M["inconel"] = m
    M["hot"] = hot_material("pt_hot_iron", (0.13, 0.12, 0.115))
    M["hot_inconel"] = hot_material("pt_hot_inconel", (0.32, 0.3, 0.28), metallic=1.0, rough=0.35)
    return M


def hot_material(name, base, metallic=0.85, rough=0.6):
    """Metal that glows like hot exhaust parts; animate with set_heat(mat, frame, 0..1)."""
    m, nt, b, out = _mat(name)
    core.set_input(b, "Base Color", (*base, 1)); core.set_input(b, "Metallic", metallic); core.set_input(b, "Roughness", rough)
    _bump(nt, b, 1200.0, 0.2)
    heat = nt.nodes.new("ShaderNodeValue"); heat.name = "heat"; heat.outputs[0].default_value = 0.0
    bb = nt.nodes.new("ShaderNodeBlackbody"); tmp = nt.nodes.new("ShaderNodeMapRange")
    tmp.inputs["To Min"].default_value = 700.0; tmp.inputs["To Max"].default_value = 1250.0
    nt.links.new(heat.outputs[0], tmp.inputs["Value"]); nt.links.new(tmp.outputs[0], bb.inputs[0])
    tc = nt.nodes.new("ShaderNodeTexCoord"); nz = nt.nodes.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 60
    nt.links.new(tc.outputs["Object"], nz.inputs[0])
    st = nt.nodes.new("ShaderNodeMath"); st.operation = "MULTIPLY"; nt.links.new(heat.outputs[0], st.inputs[0])
    st2 = nt.nodes.new("ShaderNodeMapRange"); st2.inputs["To Min"].default_value = 2.0; st2.inputs["To Max"].default_value = 9.0
    nt.links.new(nz.outputs["Fac"], st2.inputs["Value"]); nt.links.new(st2.outputs[0], st.inputs[1])
    nt.links.new(bb.outputs[0], b.inputs["Emission Color"]); nt.links.new(st.outputs[0], b.inputs["Emission Strength"])
    return m


def set_heat(mat, frame, value):
    v = mat.node_tree.nodes["heat"].outputs[0]; v.default_value = value; v.keyframe_insert("default_value", frame=frame)


def emit_mat(name, color, strength=6.0):
    m, nt, b, out = _mat(name); nt.nodes.remove(b)
    em = nt.nodes.new("ShaderNodeEmission"); em.inputs["Color"].default_value = (*color[:3], 1); em.inputs["Strength"].default_value = strength
    nt.links.new(em.outputs[0], out.inputs[0])
    return m


# ------------------------------------------------------------------ geometry helpers
def _link(ob, coll):
    for c in list(ob.users_collection):
        c.objects.unlink(ob)
    coll.objects.link(ob)
    return ob


def mesh(name, verts, faces, coll, mat=None, smooth=True, bevel=0.0, segs=2, subsurf=0):
    me = bpy.data.meshes.new(name); me.from_pydata([tuple(v) for v in verts], [], [tuple(f) for f in faces]); me.update()
    me.validate()
    ob = bpy.data.objects.new(name, me); coll.objects.link(ob)
    if mat: me.materials.append(mat)
    if smooth:
        for p in me.polygons: p.use_smooth = True
    if bevel > 0:
        md = ob.modifiers.new("bevel", "BEVEL"); md.width = bevel; md.segments = segs; md.limit_method = "ANGLE"
        md.angle_limit = math.radians(35); md.harden_normals = False
    if subsurf:
        md = ob.modifiers.new("sub", "SUBSURF"); md.levels = subsurf; md.render_levels = subsurf
    if smooth:
        try: me.set_sharp_from_angle(angle=math.radians(40))
        except Exception: pass
    return ob


def box(name, size, coll, mat=None, loc=(0, 0, 0), bevel=0.004, rot=(0, 0, 0)):
    sx, sy, sz = (s / 2 for s in size)
    v = [(-sx, -sy, -sz), (sx, -sy, -sz), (sx, sy, -sz), (-sx, sy, -sz), (-sx, -sy, sz), (sx, -sy, sz), (sx, sy, sz), (-sx, sy, sz)]
    f = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    ob = mesh(name, v, f, coll, mat, smooth=True, bevel=bevel, segs=3)
    ob.location = loc; ob.rotation_euler = rot
    return ob


def cyl(name, r, h, coll, mat=None, n=48, loc=(0, 0, 0), rot=(0, 0, 0), r2=None, cap=True, bevel=0.0015):
    """Cylinder (or cone with r2) along local Z from 0..h."""
    r2 = r if r2 is None else r2
    v = [(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n), 0) for i in range(n)] + \
        [(r2 * math.cos(2 * math.pi * i / n), r2 * math.sin(2 * math.pi * i / n), h) for i in range(n)]
    f = [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
    if cap:
        f += [tuple(reversed(range(n))), tuple(range(n, 2 * n))]
    ob = mesh(name, v, f, coll, mat, smooth=True, bevel=bevel, segs=2)
    ob.location = loc; ob.rotation_euler = rot
    return ob


def lathe(name, prof, coll, mat=None, n=64, loc=(0, 0, 0), rot=(0, 0, 0)):
    """Revolve a (r, z) profile around Z."""
    v, f = [], []
    for i in range(n):
        a = 2 * math.pi * i / n
        for r, z in prof:
            v.append((r * math.cos(a), r * math.sin(a), z))
    m = len(prof)
    for i in range(n):
        for j in range(m - 1):
            a, b = i * m + j, ((i + 1) % n) * m + j
            f.append((a, b, b + 1, a + 1))
    ob = mesh(name, v, f, coll, mat, smooth=True)
    ob.location = loc; ob.rotation_euler = rot
    return ob


def tube(name, pts, r, coll, mat=None, res=12, grow=1.0):
    """Pipe along points (Bezier, auto handles). Animate growth with grow_keys(ob, [(f, 0..1)])."""
    cu = bpy.data.curves.new(name, "CURVE"); cu.dimensions = "3D"; cu.bevel_depth = r; cu.bevel_resolution = 4
    cu.resolution_u = res; cu.use_fill_caps = True
    sp = cu.splines.new("BEZIER"); sp.bezier_points.add(len(pts) - 1)
    for bp, p in zip(sp.bezier_points, pts):
        bp.co = p; bp.handle_left_type = bp.handle_right_type = "AUTO"
    cu.bevel_factor_end = grow
    ob = bpy.data.objects.new(name, cu); coll.objects.link(ob)
    if mat: cu.materials.append(mat)
    return ob


def grow_keys(ob, keys):
    for f, v in keys:
        ob.data.bevel_factor_end = v; ob.data.keyframe_insert("bevel_factor_end", frame=f)


# ------------------------------------------------------------------ turbo wheels
def blade_wheel(name, coll, mat, r_tip=0.045, r_hub=0.012, length=0.05, blades=6, splitters=True, kind="compressor",
                thick=0.0012, nu=14, nv=6):
    """Compressor (backswept, splitter blades) or turbine (radial) wheel along local +Z, inlet at +Z."""
    verts, faces = [], []
    def add_blade(a0, frac_start, wrap, lean):
        base = len(verts)
        for i in range(nu + 1):
            u = frac_start + (1 - frac_start) * i / nu          # 0 = inlet (top), 1 = exit (bottom/outer)
            z = length * (1 - u)
            for j in range(nv + 1):
                w = j / nv
                if kind == "compressor":
                    rh = r_hub + (r_tip * 0.55 - r_hub) * (1 - u) ** 1.5 * 0.0 + r_hub * (1 + 1.6 * u ** 2)
                    rt = r_tip * (0.68 + 0.32 * u ** 1.4)
                    zz = z * (1 - 0.65 * w * u)                   # meridional curve: axial in, radial out
                    rr = rh + (rt - rh) * w
                    ang = a0 + wrap * (1 - u) ** 2 * (0.4 + 0.6 * w) - lean * u ** 2
                else:
                    rh = r_hub * (1 + 1.2 * u); rt = r_tip * (0.55 + 0.45 * u)
                    zz = z; rr = rh + (rt - rh) * w
                    ang = a0 + wrap * (1 - u) ** 2 * w
                verts.append((rr * math.cos(ang), rr * math.sin(ang), zz))
        for i in range(nu):
            for j in range(nv):
                a = base + i * (nv + 1) + j
                faces.append((a, a + nv + 1, a + nv + 2, a + 1))
    for k in range(blades):
        a0 = 2 * math.pi * k / blades
        add_blade(a0, 0.0, 0.9 if kind == "compressor" else 0.5, 0.35 if kind == "compressor" else 0.0)
        if splitters and kind == "compressor":
            add_blade(a0 + math.pi / blades, 0.38, 0.9, 0.35)
    ob = mesh(name, verts, faces, coll, mat, smooth=True)
    so = ob.modifiers.new("solid", "SOLIDIFY"); so.thickness = thick; so.offset = 0.0
    # hub: a nose cone + back disc
    if kind == "compressor":
        prof = [(0.0, length * 1.12), (r_hub * 0.55, length * 1.1), (r_hub, length * 0.95), (r_hub * 1.25, length * 0.5),
                (r_hub * 2.2, length * 0.12), (r_tip * 0.98, 0.002), (r_tip * 0.98, -0.004), (r_hub * 1.2, -0.006), (0.0, -0.006)]
    else:
        prof = [(0.0, length * 1.05), (r_hub * 0.8, length), (r_hub * 1.3, length * 0.6), (r_hub * 2.0, length * 0.2),
                (r_tip * 1.0, 0.003), (r_tip * 1.0, -0.004), (0.0, -0.004)]
    hub = lathe(name + "_hub", prof, coll, mat, n=48)
    hub.parent = ob
    return ob


def blade_tip(kind, r_tip, length, n=12):
    """(r, z) of the blade tip line of blade_wheel() (for a shroud that clears the blades)."""
    out = []
    for i in range(n + 1):
        u = i / n
        if kind == "compressor":
            out.append((r_tip * (0.68 + 0.32 * u ** 1.4), length * (1 - u) * (1 - 0.65 * u)))
        else:
            out.append((r_tip * (0.55 + 0.45 * u), length * (1 - u)))
    return out


def volute(name, coll, mat, r_wheel=0.05, r0=0.012, r1=0.034, span=5.5, outlet_len=0.08, n=72, m=20, inlet_r=None,
           inlet_len=0.05, kind="compressor", back=True, z_shift=None, tip=None, hand=1, clear=0.0025, wall=0.004):
    """Snail housing around local Z: spiral tube whose section grows, straight tangential port, axial bore.
    tip: blade tip line [(r, z)] -> the shroud follows it with `clear` clearance (blades never touch the housing).
    hand=-1 mirrors the spiral (port on the other side). Stores 'outlet', 'outlet_dir', 'bore_top' on the object."""
    verts, faces = [], []
    secs = []
    for i in range(n + 1):
        t = i / n; phi = hand * span * t
        rho = r0 + (r1 - r0) * t
        R = r_wheel + rho * 0.9
        c = Vector((R * math.cos(phi), R * math.sin(phi), 0.0 if z_shift is None else rho + z_shift))
        tang = Vector((-math.sin(phi), math.cos(phi), 0.0)) * hand; rad = Vector((math.cos(phi), math.sin(phi), 0.0)); up = Vector((0, 0, 1))
        secs.append((c, rad, up, rho, tang))
    c_end, rad_e, up_e, rho_e, tang_e = secs[-1]
    for k in range(1, 9):
        c = c_end + tang_e * (outlet_len * k / 8)
        secs.append((c, rad_e, up_e, rho_e, tang_e))
    for c, rad, up, rho, tang in secs:
        for j in range(m):
            a = 2 * math.pi * j / m
            verts.append(c + (rad * math.cos(a) + up * math.sin(a)) * rho)
    for i in range(len(secs) - 1):
        for j in range(m):
            a, b = i * m + j, i * m + (j + 1) % m
            faces.append((a, b, b + m, a + m) if hand > 0 else (a, a + m, b + m, b))
    ob = mesh(name, verts, faces, coll, mat, smooth=True)
    if tip:
        tp = sorted(tip, key=lambda q: q[1])                       # z ascending
        ztop = tp[-1][1]; rtop = tp[-1][0] + clear
        inner = [(r + clear, z) for r, z in tp] + [(rtop, ztop + inlet_len)]
        outer = [(r + wall, z) for r, z in reversed(inner)]
        prof = [(r_wheel - 0.1 * r0, max(0.0, tp[0][1]))] + inner + outer + [(r_wheel + wall, max(0.0, tp[0][1]))]
        bore_top = ztop + inlet_len
    else:
        ir = inlet_r or r_wheel * 0.75
        prof = [(ir, inlet_len), (ir, 0.012), (r_wheel * 1.02, 0.006), (r_wheel + r1 * 0.9, 0.0), (r_wheel + r1 * 0.9, -0.004),
                (ir * 1.18, -0.004), (ir * 1.18, 0.006), (ir * 1.12, inlet_len), (ir, inlet_len)]
        bore_top = inlet_len
    sh = lathe(name + "_shroud", prof, coll, mat, n=56); sh.parent = ob
    rr = prof[-2][0] if tip else (inlet_r or r_wheel * 0.75) * 1.22
    lip = cyl(name + "_lip", rr + 0.003, 0.008, coll, mat, loc=(0, 0, bore_top - 0.008), r2=rr + 0.003, cap=False); lip.parent = ob
    if back:     # closed back plate (only when the housing is not fitted over a wheel)
        bk = cyl(name + "_back", r_wheel + r1 * 0.95, 0.006, coll, mat, loc=(0, 0, -0.012)); bk.parent = ob
    end = secs[-1][0]
    ob["outlet"] = tuple(end); ob["outlet_dir"] = tuple(tang_e); ob["bore_top"] = bore_top; ob["port_r"] = rho_e
    return ob


def intercooler(name, coll, M, w=0.62, h=0.26, d=0.07, necks="side"):
    """Bar-and-plate core with cast end tanks. necks='side' (outward) or 'back' (+Y, toward the engine)."""
    core_ = box(name + "_core", (w, d, h), coll, M["alu"], bevel=0.002)
    m, nt, b, _ = _mat("pt_ic_fins"); core.set_input(b, "Base Color", (0.42, 0.43, 0.44, 1)); core.set_input(b, "Metallic", 1.0)
    core.set_input(b, "Roughness", 0.38)
    tc = nt.nodes.new("ShaderNodeTexCoord"); wv = nt.nodes.new("ShaderNodeTexWave"); wv.wave_type = "BANDS"; wv.bands_direction = "Z"
    wv.inputs["Scale"].default_value = 160.0; nt.links.new(tc.outputs["Object"], wv.inputs[0])
    bp = nt.nodes.new("ShaderNodeBump"); bp.inputs["Strength"].default_value = 0.9; nt.links.new(wv.outputs["Fac"], bp.inputs["Height"])
    nt.links.new(bp.outputs[0], b.inputs["Normal"])
    core_.data.materials.clear(); core_.data.materials.append(m)
    for side in (-1, 1):
        t = box(f"{name}_tank_{'L' if side > 0 else 'R'}", (0.06, d * 1.25, h * 1.08), coll, M["alu"], loc=(side * (w / 2 + 0.03), 0, 0), bevel=0.012)
        t.parent = core_
        if necks == "side":
            c = cyl(f"{name}_neck_{'L' if side > 0 else 'R'}", 0.028, 0.05, coll, M["alu"], loc=(side * (w / 2 + 0.06), 0, -h * 0.2),
                    rot=(0, math.radians(90 * side), 0))
        else:
            c = cyl(f"{name}_neck_{'L' if side > 0 else 'R'}", 0.026, 0.045, coll, M["alu"], loc=(side * (w / 2 + 0.03), d * 0.625 - 0.002, -h * 0.15),
                    rot=(math.radians(-90), 0, 0))
        c.parent = core_
    return core_


def wastegate(name, coll, M):
    body = cyl(name, 0.026, 0.05, coll, M["iron"])
    can = cyl(name + "_actuator", 0.034, 0.03, coll, M["brand"], loc=(0, 0, 0.075)); can.parent = body
    rod = cyl(name + "_rod", 0.004, 0.03, coll, M["steel"], loc=(0, 0, 0.048)); rod.parent = body
    valve = cyl(name + "_valve", 0.018, 0.006, coll, M["steel"], loc=(0, 0, 0.0)); valve.parent = body
    body["valve"] = valve.name
    return body


# ------------------------------------------------------------------ V8
class V8:
    """Builds the engine into `coll`. Key points (engine space) are stored in self.P for piping/flows/cameras."""
    BANK = math.radians(45.0)

    def __init__(self, coll, M, bore=0.092, spacing=0.112, deck=0.215, stroke=0.092, rod=0.155):
        self.coll, self.M = coll, M; self.bore, self.spacing, self.deck, self.stroke, self.rod = bore, spacing, deck, stroke, rod
        self.parts = {}; self.P = {}; self.pistons = []; self.rods = []
        self.cranks = None
        self._block(); self._heads(); self._intake(); self._exhaust(); self._front(); self._internals()

    def bank_axis(self, side):           # side +1 = left bank (+X)
        return Vector((side * math.sin(self.BANK), 0.0, math.cos(self.BANK)))

    def cyl_y(self, i, side):
        return (-1.5 + i) * self.spacing + (0.012 if side > 0 else -0.012)

    def _block(self):
        c, M = self.coll, self.M; L = self.spacing * 4 + 0.05
        # crankcase section (X, Z) extruded along Y; the two banks rise at +-45 deg
        a = self.bank_axis(1); n = Vector((math.cos(self.BANK), 0, -math.sin(self.BANK)))
        top_out = a * self.deck + n * 0.085; top_in = a * self.deck - n * 0.085
        sec = [(-0.2, -0.085), (0.2, -0.085), (0.205, 0.02), (top_out.x, top_out.z), (top_in.x, top_in.z), (0.0, 0.07),
               (-top_in.x, top_in.z), (-top_out.x, top_out.z), (-0.205, 0.02)]
        v = [(x, y, z) for y in (-L / 2, L / 2) for x, z in sec]; k = len(sec)
        f = [tuple(range(k))[::-1], tuple(range(k, 2 * k))] + [(i, (i + 1) % k, k + (i + 1) % k, k + i) for i in range(k)]
        self.parts["block"] = mesh("v8_block", v, f, c, M["alu"], smooth=False, bevel=0.006, segs=2)
        self.parts["pan"] = box("v8_oilpan", (0.36, L * 0.92, 0.13), c, M["crinkle"], loc=(0, 0.01, -0.15), bevel=0.02)
        self.parts["bell"] = cyl("v8_bellhousing", 0.2, 0.05, c, M["alu"], loc=(0, L / 2, 0), rot=(math.radians(-90), 0, 0), r2=0.16)
        self.L = L

    def _heads(self):
        c, M = self.coll, self.M
        for side in (1, -1):
            a = self.bank_axis(side); rot = (0, side * self.BANK, 0)
            ctr = a * (self.deck + 0.055)
            h = box(f"v8_head_{'L' if side > 0 else 'R'}", (0.19, self.L * 0.98, 0.11), c, M["alu"], loc=ctr, rot=rot, bevel=0.008)
            cv = box(f"v8_cover_{'L' if side > 0 else 'R'}", (0.17, self.L * 0.9, 0.07), c, M["crinkle"], loc=a * (self.deck + 0.14), rot=rot, bevel=0.025)
            fins = []
            for i in range(6):
                fy = -self.L * 0.35 + i * self.L * 0.14
                fn = box(f"v8_fin_{'L' if side > 0 else 'R'}{i}", (0.15, 0.012, 0.012), c, M["machined"],
                         loc=a * (self.deck + 0.178) + Vector((0, fy, 0)), rot=rot, bevel=0.003)
                fins.append(fn)
            self.parts[f"head_{side}"] = h; self.parts[f"cover_{side}"] = cv; self.parts[f"fins_{side}"] = fins
            # exhaust ports on the outer face of each head
            out = Vector((side * math.cos(self.BANK), 0, -math.sin(self.BANK)))
            self.P[f"ports_{side}"] = [a * (self.deck + 0.05) + out * 0.1 + Vector((0, self.cyl_y(i, side), 0)) for i in range(4)]
            self.P[f"intake_ports_{side}"] = [a * (self.deck + 0.06) - out * 0.095 + Vector((0, self.cyl_y(i, side), 0)) for i in range(4)]

    def _intake(self):
        c, M = self.coll, self.M
        top = Vector((0, 0, self.deck * math.cos(self.BANK) + 0.17))
        self.parts["plenum"] = box("v8_plenum", (0.16, self.L * 0.82, 0.1), c, M["alu"], loc=top, bevel=0.03)
        runners = []
        for side in (1, -1):
            for i, p in enumerate(self.P[f"intake_ports_{side}"]):
                q = Vector((side * 0.05, p.y, top.z - 0.02))
                runners.append(tube(f"v8_runner_{side}_{i}", [q, (q + p) / 2 + Vector((0, 0, 0.03)), p], 0.016, c, M["alu"]))
        self.parts["runners"] = runners
        tb = cyl("v8_throttle", 0.042, 0.06, c, M["brand"], loc=top + Vector((0, -self.L * 0.41, 0)), rot=(math.radians(90), 0, 0))
        self.parts["throttle"] = tb
        self.P["throttle_in"] = top + Vector((0, -self.L * 0.41 - 0.065, 0))

    def _exhaust(self):
        c, M = self.coll, self.M
        for side in (1, -1):
            ports = self.P[f"ports_{side}"]
            coll_pt = Vector((side * 0.31, -self.L / 2 - 0.02, 0.02))          # collector: front-outer corner
            prim = []
            for i, p in enumerate(ports):
                out = Vector((side * 0.06, 0, -0.03))
                prim.append(tube(f"v8_primary_{side}_{i}", [p, p + out, coll_pt + Vector((0, 0.06 * (3 - i) * 0.25, 0.04)), coll_pt], 0.017, c, M["hot"]))
            col = cyl(f"v8_collector_{side}", 0.034, 0.06, c, M["hot"], loc=coll_pt, rot=(math.radians(90), 0, 0))
            self.parts[f"primaries_{side}"] = prim; self.parts[f"collector_{side}"] = col
            self.P[f"collector_{side}"] = coll_pt + Vector((0, -0.06, 0))      # outlet flange face (normal -Y)

    def _front(self):
        c, M = self.coll, self.M; y = -self.L / 2 - 0.01
        self.parts["timing"] = box("v8_timing_cover", (0.34, 0.03, 0.34), c, M["alu"], loc=(0, y - 0.015, 0.06), bevel=0.03)
        self.parts["crank_pulley"] = cyl("v8_crank_pulley", 0.085, 0.035, c, M["machined"], loc=(0, y - 0.03, 0), rot=(math.radians(90), 0, 0))
        self.parts["pump_pulley"] = cyl("v8_pump_pulley", 0.06, 0.03, c, M["machined"], loc=(0, y - 0.035, 0.17), rot=(math.radians(90), 0, 0))
        self.parts["alternator"] = cyl("v8_alternator", 0.065, 0.11, c, M["alu"], loc=(0.17, y - 0.02, 0.24), rot=(math.radians(90), 0, 0))
        belt_pts = [Vector((0, y - 0.05, -0.09)), Vector((0.1, y - 0.05, 0.05)), Vector((0.2, y - 0.05, 0.24)),
                    Vector((0.05, y - 0.05, 0.25)), Vector((-0.08, y - 0.05, 0.12)), Vector((-0.09, y - 0.05, -0.02))]
        b = tube("v8_belt", belt_pts, 0.006, c, M["rubber"]); b.data.splines[0].use_cyclic_u = True    # closed loop, no duplicate point
        self.parts["belt"] = b

    def _internals(self):
        c, M = self.coll, self.M
        crank = cyl("v8_crank", 0.026, self.L, c, M["steel"], loc=(0, -self.L / 2, 0), rot=(math.radians(-90), 0, 0))
        self.cranks = crank
        for side in (1, -1):
            for i in range(4):
                y = self.cyl_y(i, side)
                p = cyl(f"v8_piston_{side}_{i}", self.bore / 2 * 0.97, 0.06, c, M["machined"])
                rd = box(f"v8_rod_{side}_{i}", (0.022, 0.012, self.rod), c, M["steel"], bevel=0.003)
                self.pistons.append((p, side, i, y)); self.rods.append(rd)
        self.parts["internals"] = [crank] + [p for p, *_ in self.pistons] + self.rods

    # cross-plane crank: pins at 0, 90, 270, 180 deg; firing pulses for flows/glow
    PIN = (0.0, 90.0, 270.0, 180.0)

    def pose(self, theta, frame):
        """Key the pistons/rods for crank angle theta (rad)."""
        r = self.stroke / 2
        self.cranks.rotation_euler = (math.radians(-90), theta, 0); self.cranks.keyframe_insert("rotation_euler", frame=frame)
        for (p, side, i, y), rd in zip(self.pistons, self.rods):
            ang = theta + math.radians(self.PIN[i]) - side * self.BANK
            a = self.bank_axis(side)
            pin = Vector((r * math.sin(theta + math.radians(self.PIN[i])), y, r * math.cos(theta + math.radians(self.PIN[i]))))
            x = r * math.cos(ang) + math.sqrt(self.rod ** 2 - (r * math.sin(ang)) ** 2)
            top = a * x + Vector((0, y, 0))
            p.location = top + a * 0.005; p.rotation_euler = (0, side * self.BANK, 0)
            mid = (pin + top) / 2; d = top - pin
            rd.location = mid; rd.rotation_mode = "QUATERNION"; rd.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(d.normalized())
            p.keyframe_insert("location", frame=frame); p.keyframe_insert("rotation_euler", frame=frame)
            rd.keyframe_insert("location", frame=frame); rd.keyframe_insert("rotation_quaternion", frame=frame)


# ------------------------------------------------------------------ flows (Geometry Nodes)
def flow_group():
    ng = bpy.data.node_groups.get("S3D_Flow")
    if ng:
        return ng
    ng = bpy.data.node_groups.new("S3D_Flow", "GeometryNodeTree")
    I = ng.interface
    I.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    for nm, typ, dv in (("Count", "NodeSocketInt", 300), ("Phase", "NodeSocketFloat", 0.0), ("Density", "NodeSocketFloat", 1.0),
                        ("Radius", "NodeSocketFloat", 0.02), ("Size", "NodeSocketFloat", 0.006), ("Stretch", "NodeSocketFloat", 3.0),
                        ("Seed", "NodeSocketInt", 0), ("Jitter", "NodeSocketFloat", 0.02)):
        s = I.new_socket(nm, in_out="INPUT", socket_type=typ); s.default_value = dv
    I.new_socket("Material", in_out="INPUT", socket_type="NodeSocketMaterial")
    I.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    N, L = ng.nodes, ng.links
    gi, go = N.new("NodeGroupInput"), N.new("NodeGroupOutput")
    pts = N.new("GeometryNodePoints"); L.new(gi.outputs["Count"], pts.inputs["Count"])
    idx = N.new("GeometryNodeInputIndex")
    div = N.new("ShaderNodeMath"); div.operation = "DIVIDE"; L.new(idx.outputs[0], div.inputs[0]); L.new(gi.outputs["Count"], div.inputs[1])
    rj = N.new("FunctionNodeRandomValue"); rj.data_type = "FLOAT"; rj.inputs["Min"].default_value = 0.0; rj.inputs["Max"].default_value = 1.0
    L.new(gi.outputs["Seed"], rj.inputs["Seed"])
    jm = N.new("ShaderNodeMath"); jm.operation = "MULTIPLY"; L.new(rj.outputs[1], jm.inputs[0]); L.new(gi.outputs["Jitter"], jm.inputs[1])
    add = N.new("ShaderNodeMath"); add.operation = "ADD"; L.new(div.outputs[0], add.inputs[0]); L.new(gi.outputs["Phase"], add.inputs[1])
    add2 = N.new("ShaderNodeMath"); add2.operation = "ADD"; L.new(add.outputs[0], add2.inputs[0]); L.new(jm.outputs[0], add2.inputs[1])
    fr = N.new("ShaderNodeMath"); fr.operation = "FRACT"; L.new(add2.outputs[0], fr.inputs[0])
    sc = N.new("GeometryNodeSampleCurve"); sc.mode = "FACTOR"; sc.use_all_curves = True
    L.new(gi.outputs["Geometry"], sc.inputs["Curves"]); L.new(fr.outputs[0], sc.inputs["Factor"])
    # random offset inside the pipe section
    ra = N.new("FunctionNodeRandomValue"); ra.data_type = "FLOAT"; ra.inputs["Min"].default_value = 0.0; ra.inputs["Max"].default_value = 6.2832
    rr = N.new("FunctionNodeRandomValue"); rr.data_type = "FLOAT"; rr.inputs["Min"].default_value = 0.0; rr.inputs["Max"].default_value = 1.0
    sd2 = N.new("ShaderNodeMath"); sd2.operation = "ADD"; sd2.inputs[1].default_value = 7; L.new(gi.outputs["Seed"], sd2.inputs[0])
    L.new(sd2.outputs[0], ra.inputs["Seed"]); sd3 = N.new("ShaderNodeMath"); sd3.operation = "ADD"; sd3.inputs[1].default_value = 13
    L.new(gi.outputs["Seed"], sd3.inputs[0]); L.new(sd3.outputs[0], rr.inputs["Seed"])
    sq = N.new("ShaderNodeMath"); sq.operation = "SQRT"; L.new(rr.outputs[1], sq.inputs[0])
    rad = N.new("ShaderNodeMath"); rad.operation = "MULTIPLY"; L.new(sq.outputs[0], rad.inputs[0]); L.new(gi.outputs["Radius"], rad.inputs[1])
    cosn = N.new("ShaderNodeMath"); cosn.operation = "COSINE"; L.new(ra.outputs[1], cosn.inputs[0])
    sinn = N.new("ShaderNodeMath"); sinn.operation = "SINE"; L.new(ra.outputs[1], sinn.inputs[0])
    bin_ = N.new("ShaderNodeVectorMath"); bin_.operation = "CROSS_PRODUCT"; L.new(sc.outputs["Tangent"], bin_.inputs[0]); L.new(sc.outputs["Normal"], bin_.inputs[1])
    v1 = N.new("ShaderNodeVectorMath"); v1.operation = "SCALE"; L.new(sc.outputs["Normal"], v1.inputs[0]); L.new(cosn.outputs[0], v1.inputs["Scale"])
    v2 = N.new("ShaderNodeVectorMath"); v2.operation = "SCALE"; L.new(bin_.outputs[0], v2.inputs[0]); L.new(sinn.outputs[0], v2.inputs["Scale"])
    v3 = N.new("ShaderNodeVectorMath"); v3.operation = "ADD"; L.new(v1.outputs[0], v3.inputs[0]); L.new(v2.outputs[0], v3.inputs[1])
    v4 = N.new("ShaderNodeVectorMath"); v4.operation = "SCALE"; L.new(v3.outputs[0], v4.inputs[0]); L.new(rad.outputs[0], v4.inputs["Scale"])
    pos = N.new("ShaderNodeVectorMath"); pos.operation = "ADD"; L.new(sc.outputs["Position"], pos.inputs[0]); L.new(v4.outputs[0], pos.inputs[1])
    sp = N.new("GeometryNodeSetPosition"); L.new(pts.outputs[0], sp.inputs["Geometry"]); L.new(pos.outputs[0], sp.inputs["Position"])
    # density: delete a random subset
    rd = N.new("FunctionNodeRandomValue"); rd.data_type = "FLOAT"; sd4 = N.new("ShaderNodeMath"); sd4.operation = "ADD"; sd4.inputs[1].default_value = 29
    L.new(gi.outputs["Seed"], sd4.inputs[0]); L.new(sd4.outputs[0], rd.inputs["Seed"])
    gt = N.new("ShaderNodeMath"); gt.operation = "GREATER_THAN"; L.new(rd.outputs[1], gt.inputs[0]); L.new(gi.outputs["Density"], gt.inputs[1])
    dl = N.new("GeometryNodeDeleteGeometry"); dl.domain = "POINT"; L.new(sp.outputs[0], dl.inputs["Geometry"]); L.new(gt.outputs[0], dl.inputs["Selection"])
    # streak instances aligned to the tangent
    ico = N.new("GeometryNodeMeshIcoSphere"); ico.inputs["Radius"].default_value = 1.0; ico.inputs["Subdivisions"].default_value = 1
    al = N.new("FunctionNodeAlignEulerToVector"); al.axis = "X"; L.new(sc.outputs["Tangent"], al.inputs["Vector"])
    scl = N.new("ShaderNodeCombineXYZ"); mx = N.new("ShaderNodeMath"); mx.operation = "MULTIPLY"
    L.new(gi.outputs["Size"], mx.inputs[0]); L.new(gi.outputs["Stretch"], mx.inputs[1]); L.new(mx.outputs[0], scl.inputs[0])
    L.new(gi.outputs["Size"], scl.inputs[1]); L.new(gi.outputs["Size"], scl.inputs[2])
    iop = N.new("GeometryNodeInstanceOnPoints"); L.new(dl.outputs[0], iop.inputs["Points"]); L.new(ico.outputs[0], iop.inputs["Instance"])
    L.new(al.outputs[0], iop.inputs["Rotation"]); L.new(scl.outputs[0], iop.inputs["Scale"])
    sm = N.new("GeometryNodeSetMaterial"); L.new(iop.outputs[0], sm.inputs["Geometry"]); L.new(gi.outputs["Material"], sm.inputs["Material"])
    L.new(sm.outputs[0], go.inputs[0])
    return ng


def flow(name, pts, coll, mat, count=260, radius=0.018, size=0.005, stretch=3.5, seed=0, jitter=0.03):
    """Particle stream along a smooth curve through `pts`. Animate with flow_keys()."""
    cu = bpy.data.curves.new(name, "CURVE"); cu.dimensions = "3D"
    sp = cu.splines.new("BEZIER"); sp.bezier_points.add(len(pts) - 1)
    for bp, p in zip(sp.bezier_points, pts):
        bp.co = p; bp.handle_left_type = bp.handle_right_type = "AUTO"
    ob = bpy.data.objects.new(name, cu); coll.objects.link(ob)
    md = ob.modifiers.new("flow", "NODES"); md.node_group = flow_group()
    ids = {it.name: it.identifier for it in md.node_group.interface.items_tree if getattr(it, "in_out", "") == "INPUT"}
    md[ids["Count"]] = count; md[ids["Radius"]] = radius; md[ids["Size"]] = size; md[ids["Stretch"]] = stretch
    md[ids["Seed"]] = seed; md[ids["Jitter"]] = jitter; md[ids["Material"]] = mat
    ob["ids"] = ids
    L = 0.0; P = [Vector(p) for p in pts]
    for a, b in zip(P, P[1:]): L += (b - a).length
    ob["length"] = L * 1.08
    return ob


def flow_keys(ob, speed_fn, density_fn, f0, f1, fps=30):
    """Per-frame keys: phase integrates speed (m/s) so particles never jump when the speed changes."""
    md = ob.modifiers["flow"]; ids = ob["ids"]; ph = 0.0
    for f in range(f0, f1 + 1):
        md[ids["Phase"]] = ph; md[ids["Density"]] = density_fn(f)
        ob.keyframe_insert(f'modifiers["flow"]["{ids["Phase"]}"]', frame=f)
        ob.keyframe_insert(f'modifiers["flow"]["{ids["Density"]}"]', frame=f)
        ph += speed_fn(f) / fps / ob["length"]
