"""Turbocharger kit for powertrain.V8 as RIGID assemblies with exact mating faces, plus collision-free
choreography (every part arrives along a clear path and stops on its mate with a 1 mm gap; nothing interpenetrates -
verify with engine/collide.py).

Unit frame U of a turbo: origin on the shaft centre, +x from turbine to compressor, +z up. Compressor wheel at x=+0.10
(inducer faces +x), turbine wheel at x=-0.10 (exducer faces -x). The centre cartridge is three pieces so the story can
build it: compressor half (with backplate), middle coupling ("one shaft"), turbine half (with backplate).
Housings have OPEN backs and slide over their wheel along the axis (the way real housings are fitted).
"""
import bpy, math
from mathutils import Vector, Matrix, Quaternion, Euler
from . import core
from . import powertrain as PT

GAP = 0.001


def _root(name, coll, M=None):
    e = bpy.data.objects.new(name, None); e.empty_display_size = 0.05; coll.objects.link(e)
    if M is not None:
        e.matrix_world = M
    e["s3d_rigid"] = True
    return e


def _adopt(ob, parent, local=None):
    """Parent `ob` to `parent` keeping `local` (matrix in parent space) as its transform."""
    ob.parent = parent; ob.matrix_parent_inverse = Matrix.Identity(4)
    if local is not None:
        ob.matrix_basis = local
    return ob


def _group(root):
    out = [root]
    for c in root.children_recursive:
        out.append(c)
    return out


def _axis_basis(axis):
    """Columns for local X, Y, Z -> unit-frame vectors with local Z = axis (+x or -x)."""
    if axis > 0:
        return Matrix(((0, 0, 1), (1, 0, 0), (0, 1, 0)))     # X->(0,1,0) Y->(0,0,1) Z->(1,0,0)
    return Matrix(((0, 0, -1), (0, 1, 0), (1, 0, 0)))        # X->(0,0,1) Y->(0,1,0) Z->(-1,0,0)


class Turbo:
    """One turbocharger (all parts), built in its unit frame under `root` (place root with a matrix)."""
    def __init__(self, name, coll, M, scale=1.0):
        self.name, self.coll, self.M, self.s = name, coll, M, scale
        s = scale
        self.r_tip = 0.045 * s; self.r_wheel = 0.05 * s; self.r1 = 0.034 * s; self.half_r = 0.042 * s
        self.xc, self.xt = 0.10 * s, -0.10 * s
        self.parts = {}

    def build(self, port_c_dir=Vector((0, 0, 1)), port_t_dir=Vector((0, 1, 0))):
        c, M, s = self.coll, self.M, self.s
        P = {}
        # --- wheels (each its own rigid root so they can arrive separately); spin pivots are children
        for key, x, axis, kind, mat in (("comp_wheel", self.xc, +1, "compressor", M["machined"]), ("turb_wheel", self.xt, -1, "turbine", M["hot_inconel"])):
            r = _root(f"{self.name}_{key}", c)
            spin = bpy.data.objects.new(f"{self.name}_{key}_spin", None); c.objects.link(spin); _adopt(spin, r)
            B = _axis_basis(axis).to_4x4(); B.translation = Vector((x, 0, 0))
            spin.matrix_basis = B
            w = PT.blade_wheel(f"{self.name}_{key}_mesh", c, mat, r_tip=self.r_tip, r_hub=0.012 * s, length=0.05 * s,
                               blades=7 if kind == "compressor" else 11, splitters=(kind == "compressor"), kind=kind, thick=0.0012 * s)
            _adopt(w, spin, Matrix.Identity(4))
            P[key] = r; P[key + "_spin"] = spin
        # --- cartridge: halves with backplates + middle coupling
        hr = self.half_r
        for key, x0, x1, bp in (("comp_half", 0.030 * s, 0.084 * s, (0.084 * s, 0.090 * s)), ("turb_half", -0.084 * s, -0.030 * s, (-0.090 * s, -0.084 * s))):
            r = _root(f"{self.name}_{key}", c)
            body = PT.cyl(f"{self.name}_{key}_body", hr, x1 - x0, c, M["iron"], rot=(0, math.radians(90), 0), loc=(x0, 0, 0))
            _adopt(body, r, body.matrix_basis.copy())
            plate = PT.cyl(f"{self.name}_{key}_plate", 0.082 * s, bp[1] - bp[0], c, M["alu"] if key == "comp_half" else M["iron"],
                           rot=(0, math.radians(90), 0), loc=(bp[0], 0, 0))
            _adopt(plate, r, plate.matrix_basis.copy())
            fit = PT.cyl(f"{self.name}_{key}_oil", 0.007 * s, 0.02 * s, c, M["steel"], loc=((x0 + x1) / 2, 0, hr - 0.002 * s))
            _adopt(fit, r, fit.matrix_basis.copy())
            P[key] = r
        r = _root(f"{self.name}_mid", c)
        mid = PT.cyl(f"{self.name}_mid_body", 0.03 * s, 0.06 * s - 2 * GAP, c, M["steel"], rot=(0, math.radians(90), 0), loc=(-0.03 * s + GAP, 0, 0))
        _adopt(mid, r, mid.matrix_basis.copy())
        ring = PT.cyl(f"{self.name}_mid_ring", 0.036 * s, 0.012 * s, c, M["brand"], rot=(0, math.radians(90), 0), loc=(-0.006 * s, 0, 0))
        _adopt(ring, r, ring.matrix_basis.copy())
        P["mid"] = r
        # --- housings (open back) fitted over their wheel; spiral hand chosen so the port end lies on the wanted side
        for key, x, axis, mat, want, side_ok, kind, olen in (
                ("comp_housing", self.xc, +1, M["alu"], port_c_dir, lambda e: e.y < 0, "compressor", 0.085 * s),
                ("turb_housing", self.xt, -1, M["hot"], port_t_dir, lambda e: e.z > 0, "turbine", 0.05 * s)):
            B = _axis_basis(axis); best = None
            for hand in (1, -1):
                phi = hand * 5.5; rho_e = self.r1; R = self.r_wheel + 0.9 * rho_e
                c_end = Vector((R * math.cos(phi), R * math.sin(phi), rho_e - 0.006 * s))
                d_loc = Vector((-math.sin(phi), math.cos(phi), 0.0)) * hand; p_loc = c_end + d_loc * olen
                target_loc = B.inverted() @ want
                alpha = math.atan2(target_loc.y, target_loc.x) - math.atan2(d_loc.y, d_loc.x)
                Rm = B @ Matrix.Rotation(alpha, 3, "Z"); end = Rm @ p_loc
                if best is None or side_ok(end):
                    best = (hand, alpha, Rm)
                    if side_ok(end):
                        break
            hand, alpha, Rm = best
            r = _root(f"{self.name}_{key}", c)
            vo = PT.volute(f"{self.name}_{key}_mesh", c, mat, r_wheel=self.r_wheel, r0=0.012 * s, r1=self.r1, span=5.5,
                           outlet_len=olen, back=False, z_shift=-0.006 * s, hand=hand,
                           tip=PT.blade_tip(kind, self.r_tip, 0.05 * s), clear=0.0025 * s, wall=0.004 * s, inlet_len=0.05 * s)
            d_loc = Vector(vo["outlet_dir"]); p_loc = Vector(vo["outlet"])
            Mh = Rm.to_4x4(); Mh.translation = Vector((x, 0, 0))
            _adopt(vo, r, Mh)
            port = Mh @ p_loc; pdir = (Mh.to_3x3() @ d_loc).normalized()
            fl = PT.cyl(f"{self.name}_{key}_flange", vo["port_r"] + 0.004 * s, 0.006 * s, c, mat, cap=False)
            q = Vector((0, 0, 1)).rotation_difference(pdir).to_matrix().to_4x4(); q.translation = port - pdir * 0.006 * s
            _adopt(fl, r, q)
            P[key] = r; P[key + "_port"] = (port, pdir); P[key + "_bore"] = Mh @ Vector((0, 0, vo["bore_top"]))
            P[key + "_port_r"] = vo["port_r"]
        self.parts = P
        return P

    def world(self, local_pt):
        return self.M_world @ local_pt


# ------------------------------------------------------------------ kit around the engine
class Kit:
    """All add-on parts for the story, as rigid groups with final transforms in ENGINE space.
    self.final[name] = 4x4 matrix of the group's root when installed; animate with self.move()."""
    def __init__(self, v8, coll, M, brand_mat=None):
        self.v8, self.c, self.M = v8, coll, M; self.final = {}; self.roots = {}; self.allow = set(); self.notes = []
        L = v8.L
        self.T = Vector((0, -0.204 - 0.06, 0.322))                  # throttle flange face (normal -Y)
        self.C = {s: Vector((s * 0.31, -L / 2 - 0.02 - 0.06, 0.02)) for s in (1, -1)}   # collector faces (normal -Y)
        self.y_t, self.z_t = -0.51, -0.03
        self.xu, self.xu_twin = -0.21, 0.235           # unit-frame origins (turbine wheels in line with the collectors)

    # ---------------- helpers
    def _place(self, name, root, M):
        self.final[name] = M.copy(); self.roots[name] = root; root.matrix_world = M
        return root

    def flange(self, name, center, normal, r, parent, mat=None):
        """Flange disc whose mating face is at `center` (normal points away from the pipe, toward the mate)."""
        bpy.context.view_layer.update()
        f = PT.cyl(name, r, 0.006, self.c, mat or self.M["iron"])
        n = normal.normalized()
        q = Vector((0, 0, 1)).rotation_difference(n).to_matrix().to_4x4(); q.translation = center - n * 0.006
        _adopt(f, parent, parent.matrix_world.inverted() @ q)
        return f

    def pipe(self, name, pts, r, parent, mat, start=None, end=None, lead=0.045, fl_start=True, fl_end=True):
        """Rigid pipe. start/end = (face point, mating normal pointing OUT of the pipe toward its mate): the pipe
        runs straight along the normal for `lead` so its end cap sits flat on the mate (no corner digs in)."""
        bpy.context.view_layer.update()
        P = [Vector(p) for p in pts]
        if start and end:
            lead = min(lead, (Vector(end[0]) - Vector(start[0])).length / 3.2)
        if start:
            p0, n0 = Vector(start[0]), Vector(start[1]).normalized()
            P = [p0, p0 - n0 * lead] + P
            if fl_start:
                self.flange(name + "_fs", p0, n0, r + 0.009, parent, mat)
        if end:
            pe, ne = Vector(end[0]), Vector(end[1]).normalized()
            P = P + [pe - ne * lead, pe]
            if fl_end:
                self.flange(name + "_fe", pe, ne, r + 0.009, parent, mat)
        t = PT.tube(name, P, r, self.c, mat)
        _adopt(t, parent, parent.matrix_world.inverted())
        return t

    # ---------------- stock (naturally aspirated) parts
    def stock(self):
        M, c = self.M, self.c; Y = Vector((0, 1, 0))
        r = _root("filter", c, Matrix.Identity(4))
        prof = [(0.0, 0.0), (0.046, 0.0), (0.046, 0.012), (0.07, 0.03), (0.07, 0.13), (0.05, 0.16), (0.0, 0.16)]
        f = PT.lathe("filter_cone", prof, c, M["crinkle"], n=48)
        q = Vector((0, 0, 1)).rotation_difference(Vector((0, -1, 0))).to_matrix().to_4x4(); q.translation = self.T - Y * GAP
        _adopt(f, r, q)
        self._place("filter", r, Matrix.Identity(4))
        for s in (1, -1):        # stock downpipes: collector -> down -> back under the engine (exhaust leaves at the back)
            r = _root(f"downpipe_stock_{s}", c, Matrix.Identity(4))
            C = self.C[s] - Y * GAP
            # 90 deg elbow right at the collector (bend radius 1.5 x pipe radius: no folded inner wall), then down and back
            x = C.x; Rb = 0.045; y0 = C.y - 0.01; zc = C.z - Rb
            arc = [Vector((x, y0 - Rb * math.sin(math.radians(a)), zc + Rb * math.cos(math.radians(a)))) for a in (30, 60, 90)]
            self.pipe(f"downpipe_stock_{s}_tube", arc + [Vector((x, y0 - Rb, zc - 0.04)), Vector((x - s * 0.01, -0.36, -0.10)),
                                                         Vector((s * 0.29, -0.24, -0.12)), Vector((s * 0.27, -0.05, -0.15)),
                                                         Vector((s * 0.26, 0.10, -0.21)), Vector((s * 0.26, 0.25, -0.22))],
                      0.03, r, M["hot"], start=(C, Y), lead=0.01)
            self._place(f"downpipe_stock_{s}", r, Matrix.Identity(4))
        r = _root("stand", c, Matrix.Identity(4)); y = self.y_t; xu = self.xu
        beam = PT.box("stand_beam", (0.36, 0.03, 0.024), c, M["crinkle"], loc=(xu, y, -0.15), bevel=0.003); _adopt(beam, r, beam.matrix_basis.copy())
        top = self.z_t - 0.042 - GAP
        for i, xp in enumerate((xu + 0.057, xu - 0.057)):
            post = PT.box(f"stand_post{i}", (0.02, 0.02, top + 0.138), c, M["crinkle"], loc=(xp, y, (top - 0.138) / 2), bevel=0.002)
            _adopt(post, r, post.matrix_basis.copy())
        pan_front = -0.219
        arm = PT.box("stand_arm", (0.024, abs(pan_front - y) - 0.015 - GAP, 0.024), c, M["crinkle"],
                     loc=(-0.06, (pan_front - GAP + y + 0.015) / 2, -0.15), bevel=0.003)
        _adopt(arm, r, arm.matrix_basis.copy())
        self._place("stand", r, Matrix.Identity(4))

    # ---------------- single big turbo, built in pieces
    def single(self):
        X, Y, Z = Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))
        U = Matrix.Translation((self.xu, self.y_t, self.z_t))
        tb = Turbo("T1", self.c, self.M, 1.0); tb.M_world = U
        P = tb.build(port_c_dir=Z, port_t_dir=Y)
        for k in ("comp_wheel", "turb_wheel", "comp_half", "turb_half", "mid", "comp_housing", "turb_housing"):
            self._place(f"T1_{k}", P[k], U)
        self.t1 = tb; self.t1P = P
        pc, pcd = P["comp_housing_port"]; pt, ptd = P["turb_housing_port"]
        pc, pt = U @ pc, U @ pt; self.pt1 = pt
        self.Fc = pc + pcd * GAP                                     # charge-pipe flange face above the compressor port
        th = P["turb_housing"]; C = self.C[-1] - Y * GAP
        self.pipe("T1_adapter", [], 0.032, th, self.M["hot"], start=(pt, -ptd), end=(C, Y), fl_start=False)
        CL = self.C[1] - Y * GAP
        sock_top = Vector((C.x, C.y - 0.035, C.z - 0.01)); sock = Vector((C.x, C.y - 0.035, -0.19))
        stub = PT.tube("T1_socket", [sock_top, (sock_top + sock) / 2, sock], 0.026, self.c, self.M["hot"])
        bpy.context.view_layer.update(); _adopt(stub, th, th.matrix_world.inverted())
        self.flange("T1_socket_fl", sock, -Z, 0.035, th, self.M["hot"])
        self.sock = sock - Z * GAP
        r = _root("T1_crossover", self.c, Matrix.Identity(4))
        self.pipe("T1_crossover_tube", [Vector((0.30, -0.385, -0.12)), Vector((0.20, -0.39, -0.235)), Vector((-0.10, -0.385, -0.25)),
                                        Vector((-0.26, -0.37, -0.25))], 0.026, r, self.M["hot"],
                  start=(CL, Y), end=(self.sock, Z), lead=0.03)
        self._place("T1_crossover", r, Matrix.Identity(4))
        bore = U @ P["turb_housing_bore"]
        r = _root("T1_downpipe", self.c, Matrix.Identity(4))
        self.pipe("T1_downpipe_tube", [Vector((-0.47, -0.47, -0.07)), Vector((-0.42, -0.30, -0.10)), Vector((-0.36, -0.10, -0.12)),
                                       Vector((-0.31, 0.06, -0.19)), Vector((-0.30, 0.25, -0.22))], 0.034, r, self.M["hot"],
                  start=(bore - X * GAP, X))
        self._place("T1_downpipe", r, Matrix.Identity(4))
        # wastegate pad welded on the adapter (flat top), the wastegate itself bolts onto it
        lead = min(0.045, (C - pt).length / 3.2); pad_c = pt + ptd * lead * 0.45
        pad = PT.cyl("T1_wg_pad", 0.019, 0.026, self.c, self.M["hot"])
        bpy.context.view_layer.update(); _adopt(pad, th, th.matrix_world.inverted() @ Matrix.Translation(pad_c + Z * 0.02))
        r = _root("T1_wastegate", self.c, Matrix.Identity(4))
        wg = PT.wastegate("T1_wastegate_body", self.c, self.M)
        _adopt(wg, r, Matrix.Translation(pad_c + Z * (0.046 + GAP)) @ Matrix.Diagonal((0.62, 0.62, 0.8, 1)))
        # dump tube: the bled exhaust leaves the valve body on its outboard side and rejoins the downpipe AFTER the turbine
        # (how an external wastegate is plumbed). Part of the wastegate unit; it seats on a flange on the downpipe (GAP).
        o = pad_c + Z * (0.046 + GAP); sx = -1.0 if o.x < 0 else 1.0; rb = 0.026 * 0.62
        p0 = Vector((o.x + sx * (rb + GAP), o.y, o.z + 0.018))
        c_dp = Vector((sx * 0.42, -0.30, -0.10))                                 # a downpipe control point (on its centreline)
        t_dp = (Vector((sx * 0.36, -0.10, -0.12)) - Vector((sx * 0.47, -0.47, -0.07))).normalized()
        n_dp = Vector((sx * 0.45, 0.0, 0.89)); n_dp = (n_dp - n_dp.dot(t_dp) * t_dp).normalized()
        pe = c_dp + n_dp * (0.034 + GAP)
        mids = [Vector((sx * 0.462, o.y + 0.03, o.z - 0.05)), Vector((sx * 0.47, -0.37, -0.03))]
        self.pipe("T1_wastegate_dump", mids, 0.0095, r, self.M["hot"], start=(p0, Vector((-sx, 0, 0))), end=(pe, -n_dp),
                  lead=0.04, fl_start=False, fl_end=True)
        self.wg_path = [pad_c, o + Z * 0.018, p0, p0 + Vector((sx * 0.04, 0, 0))] + mids + [pe + n_dp * 0.04, pe, c_dp]
        self._place("T1_wastegate", r, Matrix.Identity(4))
        r = _root("charge_pipe", self.c, Matrix.Identity(4))
        T = self.T - Y * GAP
        self.pipe("charge_pipe_tube", [Vector((self.Fc.x * 0.7, self.Fc.y - 0.02, 0.20)), Vector((-0.02, -0.45, 0.33))], 0.034, r,
                  self.M["brand"], start=(self.Fc, -Z), end=(T, Y))
        self._place("charge_pipe", r, Matrix.Identity(4))

    # ---------------- intercooler (front, low) + its pipes
    def intercooler(self):
        X, Y, Z = Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))
        r = _root("ic", self.c, Matrix.Identity(4))
        # mounted high in front of the turbos (fits the car's bay above the bumper beam); necks point back
        icc = Vector((0, -0.70, 0.20)); w, h, d = 0.50, 0.17, 0.06
        ic = PT.intercooler("ic_core", self.c, self.M, w=w, h=h, d=d, necks="back")
        _adopt(ic, r, Matrix.Translation(icc))
        self._place("ic", r, Matrix.Identity(4))
        ny = icc.y + d * 0.625 - 0.002 + 0.045 + GAP
        self.neck = {s_: Vector((s_ * (w / 2 + 0.03), ny, icc.z - h * 0.15)) for s_ in (-1, 1)}
        T = self.T - Y * GAP
        r = _root("ic_pipeB", self.c, Matrix.Identity(4))
        self.pipe("ic_pipeB_tube", [Vector((0.27, -0.55, 0.20)), Vector((0.18, -0.46, 0.31)), Vector((0.04, -0.39, 0.345))],
                  0.03, r, self.M["brand"], start=(self.neck[1], -Y), end=(T, Y))
        self._place("ic_pipeB", r, Matrix.Identity(4))
        r = _root("ic_pipeA", self.c, Matrix.Identity(4))
        self.pipe("ic_pipeA_tube", [Vector((self.Fc.x - 0.07, self.neck[-1].y + 0.05, 0.135))],
                  0.03, r, self.M["brand"], start=(self.Fc, -Z), end=(self.neck[-1], -Y))
        self._place("ic_pipeA", r, Matrix.Identity(4))

    # ---------------- twin turbos (complete units) + their pipes
    def twins(self, scale=0.78):
        X, Y, Z = Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))
        self.tw = {}
        for s in (-1, 1):
            U = Matrix.Translation((s * self.xu_twin, self.y_t, self.z_t))
            if s > 0:
                U = U @ Matrix.Diagonal((-1, 1, 1, 1))                      # left unit: mirror image of the right
            tb = Turbo(f"TW{'L' if s > 0 else 'R'}", self.c, self.M, scale); tb.M_world = U
            P = tb.build(port_c_dir=Z, port_t_dir=Y)
            r = _root(f"twin_{s}", self.c, Matrix.Identity(4))
            for k in ("comp_wheel", "turb_wheel", "comp_half", "turb_half", "mid", "comp_housing", "turb_housing"):
                P[k].parent = r; P[k].matrix_parent_inverse = Matrix.Identity(4); P[k].matrix_basis = U
            pc, pcd = P["comp_housing_port"]; pt, ptd = P["turb_housing_port"]
            pc, pt = U @ pc, U @ pt
            C = self.C[s] - Y * GAP
            self.pipe(f"twin_{s}_adapter", [], 0.027, r, self.M["hot"], start=(pt, -ptd), end=(C, Y), fl_start=False)
            lead = min(0.045, (C - pt).length / 3.2); pad_c = pt + ptd * lead * 0.45
            pad = PT.cyl(f"twin_{s}_wg_pad", 0.016, 0.022, self.c, self.M["hot"])
            _adopt(pad, r, Matrix.Translation(pad_c + Z * 0.017))
            wg = PT.wastegate(f"twin_{s}_wastegate", self.c, self.M)
            _adopt(wg, r, Matrix.Translation(pad_c + Z * (0.039 + GAP)) @ Matrix.Diagonal((0.5, 0.5, 0.65, 1)))
            self._place(f"twin_{s}", r, Matrix.Identity(4))
            bore = U @ P["turb_housing_bore"]; d = Vector((s, 0, 0))
            rr = _root(f"twin_{s}_downpipe", self.c, Matrix.Identity(4))
            self.pipe(f"twin_{s}_downpipe_tube", [Vector((s * 0.44, -0.47, -0.07)), Vector((s * 0.40, -0.30, -0.10)),
                                                 Vector((s * 0.35, -0.10, -0.12)), Vector((s * 0.31, 0.06, -0.19)), Vector((s * 0.30, 0.25, -0.22))],
                      0.03, rr, self.M["hot"], start=(bore + d * GAP, -d))
            self._place(f"twin_{s}_downpipe", rr, Matrix.Identity(4))
            self.tw[s] = (tb, pc + pcd * GAP, P)
        r = _root("twin_ypipe", self.c, Matrix.Identity(4))
        nR = self.neck[-1]; merge = Vector((nR.x + 0.06, nR.y + 0.07, 0.115))
        for s in (-1, 1):
            F = self.tw[s][1]
            self.pipe(f"twin_ypipe_{s}", [Vector((F.x, F.y + 0.005, 0.10)), merge + Vector((s * 0.035 + 0.035, 0.0, 0.0))], 0.026, r,
                      self.M["brand"], start=(F, -Z), lead=0.03)
        self.pipe("twin_ypipe_main", [merge + Vector((0.035, 0, 0)), merge, Vector((nR.x + 0.01, nR.y + 0.05, nR.z - 0.005))],
                  0.03, r, self.M["brand"], end=(nR, -Y), lead=0.03)
        self._place("twin_ypipe", r, Matrix.Identity(4))

    # ---------------- motion
    def move(self, name, keys):
        """keys: [(frame, offset Vector or Matrix relative to the final pose, ease)] -> keyframes on the group root.
        Offsets are in engine space; Bezier easing (no overshoot: parts stop exactly on their mates)."""
        r = self.roots[name]; F = self.final[name]
        for f, off, *_ in keys:
            if isinstance(off, Matrix):
                r.matrix_world = off @ F
            else:
                r.matrix_world = Matrix.Translation(off) @ F
            r.keyframe_insert("location", frame=f); r.keyframe_insert("rotation_euler", frame=f); r.keyframe_insert("scale", frame=f)

    def show(self, name, f_on=None, f_off=None):
        """Render-visible from frame f_on (inclusive) until f_off (exclusive); None = from the start / forever."""
        keys = [(0, f_on is not None)]
        if f_on is not None:
            keys.append((f_on, False))
        if f_off is not None:
            keys.append((f_off, True))
        for o in _group(self.roots[name]):
            for f, hidden in keys:
                o.hide_render = hidden; o.hide_viewport = hidden
                o.keyframe_insert("hide_render", frame=f); o.keyframe_insert("hide_viewport", frame=f)

    def groups(self, names=None, engine_objs=()):
        g = {n: _group(r) for n, r in self.roots.items() if names is None or n in names}
        if engine_objs:
            g["engine"] = list(engine_objs)
        return g
