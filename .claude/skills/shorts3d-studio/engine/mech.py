"""Procedural mechanism / science props for "how it works" explainers (no downloads needed).
Use in specs:  src: proc:gear  params: {teeth: 24, r: 0.05}
All parts are single meshes with good default materials; sizes in metres."""
import bpy, bmesh, math, random
from mathutils import Vector, Matrix
from . import core, props as P

M = lambda n, c, r=0.4, metal=0.0, **k: P.mat(n, core.hexcol(c) if isinstance(c, str) else c, r, metal=metal, **k)
CPK = {"H": ("#F2F2F2", 0.012), "C": ("#3A3A3A", 0.017), "O": ("#E8382F", 0.016), "N": ("#3050F8", 0.016),
       "Na": ("#AB5CF2", 0.02), "Cl": ("#1FF01F", 0.019), "S": ("#FFD123", 0.019), "P": ("#FF8000", 0.019)}


def _ob(name, bm, mat, coll, smooth=True):
    return P._obj_from_bm(name, bm, mat, coll, smooth)


def gear(name, coll=None, teeth=20, r=0.05, t=0.012, hole=0.012, color="#B8BEC6"):
    bm = bmesh.new(); pts = []
    for i in range(teeth):
        a0 = 2 * math.pi * i / teeth; da = 2 * math.pi / teeth
        for f, rr in ((0.0, r * 0.86), (0.18, r * 0.86), (0.3, r), (0.55, r), (0.67, r * 0.86)):
            pts.append((math.cos(a0 + f * da) * rr, math.sin(a0 + f * da) * rr))
    lo = [bm.verts.new((x, y, 0)) for x, y in pts]; hi = [bm.verts.new((x, y, t)) for x, y in pts]
    bm.faces.new(lo[::-1]); bm.faces.new(hi)
    for i in range(len(pts)):
        j = (i + 1) % len(pts); bm.faces.new((lo[i], lo[j], hi[j], hi[i]))
    ob = _ob(name, bm, M("steel", color, 0.3, 1.0), coll, smooth=False)
    if hole:
        cut = P.proc_cyl(name + "_hole", hole, t * 3, M("x", "#000000"), coll); cut.location.z = -t
        m = ob.modifiers.new("hole", "BOOLEAN"); m.object = cut; m.operation = "DIFFERENCE"
        bpy.context.view_layer.update(); P._apply_mods(ob); bpy.data.objects.remove(cut)
    P._bevel(ob, 0.0008, 2); P._apply_mods(ob)
    return ob


def spring(name, coll=None, r=0.015, length=0.06, turns=8, wire=0.0018, color="#C9CED6"):
    pts = [(math.cos(2 * math.pi * turns * u) * r, math.sin(2 * math.pi * turns * u) * r, length * u)
           for u in [i / (turns * 10) for i in range(turns * 10 + 1)]]
    return P.curve_tube(name, pts, wire, coll, M("spring_steel", color, 0.25, 1.0), res=2)


def coil(name, coll=None, r=0.012, length=0.05, turns=14, wire=0.0012):
    ob = spring(name, coll, r, length, turns, wire)
    ob.data.materials.clear(); ob.data.materials.append(M("copper", "#C8743A", 0.25, 1.0))
    return ob


def pipe(name, coll=None, points=((0, 0, 0), (0.2, 0, 0)), r=0.012, glass=False, color="#9AA3AD"):
    m = P.mat("glass_pipe", (0.9, 0.97, 1, 1), 0.02, transmission=1.0) if glass else M("pipe", color, 0.35, 0.8)
    return P.curve_tube(name, [Vector(p) for p in points], r, coll, m)


def wire(name, coll=None, points=((0, 0, 0), (0.1, 0.05, 0), (0.2, 0, 0)), r=0.0015, color="#D23B2E"):
    return P.curve_tube(name, [Vector(p) for p in points], r, coll, M("wire_" + color, color, 0.45))


def piston(name, coll=None, r=0.03, h=0.03, rod=0.08):
    head = P.proc_cyl(name, r, h, M("alu", "#C9CCD1", 0.3, 1.0), coll)
    rd = P.proc_cyl(name + "_rod", r * 0.22, rod, M("alu", "#C9CCD1", 0.3, 1.0), coll); rd.location.z = -rod; P._apply_loc(rd)
    P._join(head, rd)
    return head


def battery(name, coll=None, r=0.007, h=0.05):
    b = P.proc_cyl(name, r, h * 0.92, M("battery_body", "#1E1E1E", 0.35), coll)
    cap = P.proc_cyl(name + "_cap", r * 0.35, h * 0.06, M("battery_cap", "#C8C8C8", 0.2, 1.0), coll); cap.location.z = h * 0.92; P._apply_loc(cap)
    band = P.proc_cyl(name + "_band", r * 1.01, h * 0.25, M("battery_band", "#E3B23C", 0.3, 0.6), coll); band.location.z = h * 0.67; P._apply_loc(band)
    P._join(b, cap); P._join(b, band)
    return b


def chip(name, coll=None, w=0.02, pins=8):
    body = P.proc_box(name, w, w * 0.55, 0.004, M("ic", "#1B1B1D", 0.5), coll, 0.0005)
    for i in range(pins):
        for s in (-1, 1):
            p = P.proc_box(name + "_pin", 0.0012, 0.004, 0.0008, M("pin", "#C0C4C9", 0.25, 1.0), coll, 0)
            p.location = (-w / 2 + w * (i + 0.5) / pins, s * w * 0.32, 0.0012); P._apply_loc(p); P._join(body, p)
    return body


def led(name, coll=None, color="#FF3B3B"):
    dome = P.proc_lathe(name, [(0.0, 0.0), (0.0025, 0.0), (0.0025, 0.004), (0.0018, 0.0062), (0.0, 0.0068)],
                        P.mat("led_" + color, core.hexcol(color), 0.1, emit=core.hexcol(color), transmission=0.4), coll)
    for dx in (-0.001, 0.001):
        leg = P.proc_box(name + "_leg", 0.0004, 0.0004, 0.012, M("pin", "#C0C4C9", 0.25, 1.0), coll, 0)
        leg.location = (dx, 0, -0.012); P._apply_loc(leg); P._join(dome, leg)
    return dome


def outlet(name, coll=None, style="us"):
    """Wall outlet face plate with slots (US/EU), mounted on the wall's surface facing -Y."""
    plate = P.proc_box(name, 0.07, 0.006, 0.115, M("plate", "#F4F4F2", 0.35), coll, 0.002)
    cuts = []
    if style == "us":
        for z in (0.035, 0.08):
            for dx, hh in ((-0.0065, 0.0085), (0.0065, 0.0065)):
                c = P.proc_box("c", 0.0025, 0.03, hh, M("x", "#000000"), coll, 0); c.location = (dx, 0, z - hh / 2); cuts.append(c)
            g = P.proc_cyl("g", 0.0026, 0.03, M("x", "#000000"), coll); g.rotation_euler.x = math.pi / 2; g.location = (0, 0.015, z - 0.013); cuts.append(g)
    else:
        for dx in (-0.01, 0.01):
            c = P.proc_cyl("c", 0.0024, 0.03, M("x", "#000000"), coll); c.rotation_euler.x = math.pi / 2; c.location = (dx, 0.015, 0.0575); cuts.append(c)
    for c in cuts:
        m = plate.modifiers.new("cut", "BOOLEAN"); m.object = c; m.operation = "DIFFERENCE"
    bpy.context.view_layer.update(); P._apply_mods(plate)
    for c in cuts: bpy.data.objects.remove(c)
    return plate


def plug(name, coll=None):
    body = P.proc_box(name, 0.03, 0.03, 0.025, M("charger", "#F7F7F7", 0.3), coll, 0.004)
    for dx in (-0.0065, 0.0065):
        p = P.proc_box(name + "_prong", 0.0016, 0.006, 0.016, M("brass", "#D4AF37", 0.25, 1.0), coll, 0.0003)
        p.location = (dx, 0, 0.025); P._apply_loc(p); P._join(body, p)
    return body


def screw(name, coll=None, r=0.003, length=0.02):
    sh = spring(name, coll, r * 0.85, length, max(4, int(length / 0.002)), r * 0.35)
    core_ = P.proc_cyl(name + "_core", r * 0.75, length, M("steel", "#B8BEC6", 0.3, 1.0), coll); P._join(sh, core_)
    head = P.proc_cyl(name + "_head", r * 1.9, r * 0.8, M("steel", "#B8BEC6", 0.3, 1.0), coll); head.location.z = length; P._apply_loc(head)
    P._join(sh, head)
    return sh


def molecule(name, coll=None, kind="water", scale=1.0):
    presets = {"water": ([("O", (0, 0, 0)), ("H", (0.024, 0, 0.016)), ("H", (-0.024, 0, 0.016))], [(0, 1), (0, 2)]),
               "co2": ([("C", (0, 0, 0)), ("O", (0.03, 0, 0)), ("O", (-0.03, 0, 0))], [(0, 1), (0, 2)]),
               "o2": ([("O", (0.015, 0, 0)), ("O", (-0.015, 0, 0))], [(0, 1)]),
               "salt": ([("Na", (0.02, 0, 0)), ("Cl", (-0.02, 0, 0))], [(0, 1)])}
    atoms, bonds = presets.get(kind, presets["water"])
    bm = bmesh.new(); first = None
    root = None
    for i, (el, p) in enumerate(atoms):
        col, r = CPK[el]
        s = bpy.data.objects.new(f"{name}_{i}", bpy.data.meshes.new(f"{name}_{i}"))
        b2 = bmesh.new(); bmesh.ops.create_uvsphere(b2, u_segments=24, v_segments=16, radius=r * scale); b2.to_mesh(s.data); b2.free()
        s.data.polygons.foreach_set("use_smooth", [True] * len(s.data.polygons))
        s.data.materials.append(M("atom_" + el, col, 0.25, coat=0.5)); (coll or bpy.context.scene.collection).objects.link(s)
        s.location = Vector(p) * scale; P._apply_loc(s)
        if root is None: root = s
        else: P._join(root, s)
    for i, j in bonds:
        a, b = Vector(atoms[i][1]) * scale, Vector(atoms[j][1]) * scale
        st = P.curve_tube(f"{name}_bond", [a, b], 0.0035 * scale, coll, M("bond", "#DADADA", 0.3)); P._join(root, st)
    root.name = name
    return root


def cell(name, coll=None, r=0.05, color="#F28AA0"):
    shell = P.proc_lathe(name, [(math.sin(math.pi * i / 24) * r, -math.cos(math.pi * i / 24) * r + r) for i in range(25)],
                         P.mat("membrane_" + color, core.hexcol(color), 0.2, sss=0.3, transmission=0.6), coll)
    nuc = bpy.data.objects.new(name + "_nuc", bpy.data.meshes.new(name + "_nuc"))
    b2 = bmesh.new(); bmesh.ops.create_icosphere(b2, subdivisions=3, radius=r * 0.33); b2.to_mesh(nuc.data); b2.free()
    nuc.data.materials.append(M("nucleus", "#7A3FA0", 0.4, sss=0.4)); (coll or bpy.context.scene.collection).objects.link(nuc)
    nuc.location = (r * 0.1, 0, r * 1.05); P._apply_loc(nuc); P._join(shell, nuc)
    return shell


def wave(name, coll=None, length=0.3, amp=0.02, cycles=4, r=0.002, color="#3EC6E0"):
    pts = [(length * u - length / 2, 0, amp * math.sin(2 * math.pi * cycles * u)) for u in [i / (cycles * 12) for i in range(cycles * 12 + 1)]]
    return P.curve_tube(name, pts, r, coll, P.mat("wave_" + color, core.hexcol(color), 0.3, emit=core.hexcol(color)))


def lattice(name, coll=None, n=4, spacing=0.012, r=0.003, color="#BFE7FF"):
    root = None
    for i in range(n):
        for j in range(n):
            for k in range(n):
                s = bpy.data.objects.new(f"{name}_a", bpy.data.meshes.new(f"{name}_a"))
                b2 = bmesh.new(); bmesh.ops.create_icosphere(b2, subdivisions=2, radius=r); b2.to_mesh(s.data); b2.free()
                s.data.materials.append(P.mat("lattice_" + color, core.hexcol(color), 0.15, coat=0.6, emit=core.hexcol(color)))
                (coll or bpy.context.scene.collection).objects.link(s)
                s.location = (Vector((i, j, k)) - Vector((n - 1, n - 1, n - 1)) / 2) * spacing; P._apply_loc(s)
                if root is None: root = s
                else: P._join(root, s)
    root.name = name
    return root


def strands(name, coll=None, count=12, length=0.06, r=0.0012, color="#F5E6C8", seed=2):
    rng = random.Random(seed); root = None
    for k in range(count):
        p0 = Vector((rng.uniform(-.02, .02), rng.uniform(-.02, .02), 0))
        pts = [p0 + Vector((math.sin(u * 6 + k) * 0.004, math.cos(u * 5 + k) * 0.004, u * length)) for u in [i / 10 for i in range(11)]]
        s = P.curve_tube(f"{name}_{k}", pts, r, coll, P.mat("strand_" + color, core.hexcol(color), 0.5, sss=0.3))
        if root is None: root = s
        else: P._join(root, s)
    root.name = name
    return root


def droplet(name, coll=None, r=0.01):
    prof = [(math.sin(math.pi * i / 20) * r * (1 - 0.6 * max(0, (i - 12) / 8)), -math.cos(math.pi * i / 20) * r + r + max(0, (i - 12) / 8) * r * 0.9) for i in range(21)]
    prof[-1] = (0.0, prof[-1][1] + r * 0.4)
    return P.proc_lathe(name, prof, P.mat("water", (0.8, 0.92, 1.0, 1), 0.02, transmission=1.0), coll)


BUILDERS = {"gear": gear, "spring": spring, "coil": coil, "pipe": pipe, "wire": wire, "piston": piston,
            "battery": battery, "chip": chip, "led": led, "outlet": outlet, "plug": plug, "screw": screw,
            "molecule": molecule, "cell": cell, "wave": wave, "lattice": lattice, "strands": strands, "droplet": droplet}
