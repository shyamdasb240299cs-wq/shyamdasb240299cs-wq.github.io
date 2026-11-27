"""Quasi-static lap simulation on a 3D centreline (wet 1993 F1 car): speed limited by lateral grip in corners,
traction/power on exits, braking on entries, aero drag. Gives s(t), v(t) so cars brake and accelerate like the real
thing - the pit-lane route and the Goddards route come out ~3-4 s apart over the final sector."""
import numpy as np

G = 9.81


class Route:
    def __init__(self, P, v_cap=None):
        """P: (N,3) points ~1 m apart. v_cap: optional (N,) speed caps (m/s) e.g. pit lane."""
        self.P = np.asarray(P, np.float64)
        d = np.linalg.norm(np.diff(self.P[:, :2], axis=0), axis=1)
        self.s = np.concatenate([[0], np.cumsum(d)]); self.L = self.s[-1]
        t = np.gradient(self.P[:, :2], axis=0); a = np.unwrap(np.arctan2(t[:, 1], t[:, 0]))
        k = np.gradient(a, self.s + np.arange(len(self.s)) * 1e-9)
        r = 6; ker = np.ones(2 * r + 1) / (2 * r + 1)
        self.k = np.convolve(np.pad(k, r, mode="edge"), ker, "valid")
        self.v_cap = np.full(len(self.s), 1e9) if v_cap is None else np.asarray(v_cap, float)

    def profile(self, a_lat=1.55 * G, a_brake=2.1 * G, power=520e3, mass=620.0, mu_trac=0.95 * G, cda=1.7, v_max=78.0):
        n = len(self.s); vlim = np.minimum(np.sqrt(a_lat / np.maximum(np.abs(self.k), 1e-6)), v_max)
        vlim = np.minimum(vlim, self.v_cap)
        v = vlim.copy()
        drag = lambda u: 0.5 * 1.2 * cda * u * u / mass
        for i in range(1, n):                      # forward: traction/power limited acceleration
            ds = self.s[i] - self.s[i - 1]; u = v[i - 1]
            acc = min(mu_trac, power / (mass * max(u, 5.0))) - drag(u)
            v[i] = min(v[i], np.sqrt(max(u * u + 2 * acc * ds, 0.0)))
        for i in range(n - 2, -1, -1):             # backward: braking (aero drag helps)
            ds = self.s[i + 1] - self.s[i]; u = v[i + 1]
            dec = a_brake + drag(u)
            v[i] = min(v[i], np.sqrt(u * u + 2 * dec * ds))
        self.v = v
        dt = np.diff(self.s) / np.maximum((v[1:] + v[:-1]) / 2, 0.5)
        self.t = np.concatenate([[0], np.cumsum(dt)])
        return self

    def s_at(self, t):
        return np.interp(t, self.t, self.s, left=None, right=None) if np.ndim(t) else float(np.interp(t, self.t, self.s))

    def v_at_s(self, s):
        return float(np.interp(s, self.s, self.v))

    def t_at_s(self, s):
        return float(np.interp(s, self.s, self.t))


def extend(route_s, t, s0_t0, v0):
    """s for times outside the simulated range (constant speed extrapolation)."""
    return s0_t0 + v0 * t
