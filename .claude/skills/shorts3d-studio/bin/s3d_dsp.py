"""DSP kit for sound design (numpy only): wav io, circular FFT filters, varispeed, STFT tone extraction (turbo whistle), synth foley."""
import wave, math
import numpy as np

SR = 48000


def rd(path, mono=True):
    w = wave.open(path); n = w.getnframes(); ch = w.getnchannels(); sw = w.getsampwidth(); sr = w.getframerate()
    raw = w.readframes(n)
    x = (np.frombuffer(raw, "<i2").astype(np.float64) / 32768.0) if sw == 2 else (np.frombuffer(raw, "<i4").astype(np.float64) / 2 ** 31)
    x = x.reshape(-1, ch)
    if sr != SR:
        t = np.arange(int(len(x) * SR / sr)) * sr / SR
        x = np.stack([np.interp(t, np.arange(len(x)), x[:, c]) for c in range(ch)], 1)
    return x.mean(1) if mono else (x if ch == 2 else np.repeat(x, 2, 1))


def wr(path, x, sr=SR):
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1: x = x[:, None]
    y = np.clip(x, -1, 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(y.shape[1]); w.setsampwidth(2); w.setframerate(sr); w.writeframes((y * 32767).astype("<i2").tobytes())


def db(g): return 10 ** (g / 20.0)


def seg(x, t0, t1):
    return x[max(0, int(t0 * SR)):int(t1 * SR)].copy()


def filt(x, hp=None, lp=None, order=2, shelves=(), circular=False):
    """Zero-phase magnitude filter by FFT. shelves: [(f_lo, f_hi, gain_db)] smooth bands. circular=True keeps a looping
    signal looping (no padding); otherwise padded to avoid wrap-around."""
    x = np.asarray(x, dtype=np.float64); n = len(x)
    N = n if circular else 1 << int(math.ceil(math.log2(n + SR // 2)))
    X = np.fft.rfft(x, N, axis=0); f = np.fft.rfftfreq(N, 1 / SR)
    H = np.ones_like(f)
    if hp: H *= 1 / np.sqrt(1 + (hp / np.maximum(f, 1e-3)) ** (2 * order))
    if lp: H *= 1 / np.sqrt(1 + (f / lp) ** (2 * order))
    for lo, hi, g in shelves:
        w = 1 / (1 + np.exp(-(np.log(np.maximum(f, 1)) - np.log(lo)) * 6)) * (1 / (1 + np.exp((np.log(np.maximum(f, 1)) - np.log(hi)) * 6)))
        H *= 1 + (db(g) - 1) * w
    if x.ndim == 2: H = H[:, None]
    return np.fft.irfft(X * H, N, axis=0)[:n]


def fades(x, fi=0.0, fo=0.0):
    x = x.copy(); n = len(x)
    if fi > 0:
        k = min(n, int(fi * SR)); r = np.sin(np.linspace(0, np.pi / 2, k)) ** 2
        x[:k] *= r if x.ndim == 1 else r[:, None]
    if fo > 0:
        k = min(n, int(fo * SR)); r = np.cos(np.linspace(0, np.pi / 2, k)) ** 2
        x[n - k:] *= r if x.ndim == 1 else r[:, None]
    return x


def env_pts(n, pts, mode="db"):
    """Piecewise-linear envelope over n samples from [(t_s, value)] (value in dB if mode == 'db')."""
    t = np.arange(n) / SR; ts = [p[0] for p in pts]; vs = [p[1] for p in pts]
    v = np.interp(t, ts, vs)
    return db(v) if mode == "db" else v


def varispeed(x, rate, start=0.0):
    """Tape-style playback: rate[i] = source samples advanced per output sample (pitch and speed together)."""
    pos = start * SR + np.concatenate([[0.0], np.cumsum(rate[:-1])])
    pos = np.clip(pos, 0, len(x) - 2)
    i = pos.astype(np.int64); f = pos - i
    return x[i] * (1 - f) + x[i + 1] * f


def stft(x, n=4096, hop=512):
    w = np.hanning(n); pad = np.pad(x, (n // 2, n // 2))
    frames = np.lib.stride_tricks.sliding_window_view(pad, n)[::hop] * w
    return np.fft.rfft(frames, axis=1), w


def istft(S, w, n_out, n=4096, hop=512):
    frames = np.fft.irfft(S, n, axis=1) * w
    out = np.zeros(n_out + n); norm = np.zeros(n_out + n)
    for k, fr in enumerate(frames):
        a = k * hop; out[a:a + n] += fr; norm[a:a + n] += w * w
    out = out[n // 2:n // 2 + n_out] / np.maximum(norm[n // 2:n // 2 + n_out], 1e-6)
    return out


def extract_tone(x, guide, bw=110.0, harm=0.45, n=4096, hop=512, search=900.0):
    """Keep only a gliding tone (e.g. a turbo whistle): per STFT frame, find the spectral peak near guide(t) Hz and
    keep a gaussian band around it (+ its 2nd harmonic). guide: function of seconds -> Hz."""
    S, w = stft(x, n, hop); f = np.fft.rfftfreq(n, 1 / SR); mag = np.abs(S)
    track = []
    for k in range(S.shape[0]):
        g = guide(k * hop / SR); m = (f > g - search) & (f < g + search)
        track.append(f[m][np.argmax(mag[k, m])] if m.any() else g)
    track = np.array(track)
    k = 5; tr = np.array([np.median(track[max(0, i - k):i + k + 1]) for i in range(len(track))])
    M = np.exp(-0.5 * ((f[None, :] - tr[:, None]) / bw) ** 2) + harm * np.exp(-0.5 * ((f[None, :] - 2 * tr[:, None]) / (bw * 1.6)) ** 2)
    return istft(S * M, w, len(x), n, hop), tr


# ---------------------------------------------------------------- synths (original, CC0 by construction)
_rng = np.random.default_rng(29)


def noise(sec, seed=None):
    r = np.random.default_rng(seed) if seed is not None else _rng
    return r.standard_normal(int(sec * SR))


def modal(freqs, decays, amps, sec, click=0.4, seed=1, detune=0.004):
    """Struck metal: damped partials + a short contact click."""
    r = np.random.default_rng(seed); t = np.arange(int(sec * SR)) / SR; y = np.zeros_like(t)
    for fr, d, a in zip(freqs, decays, amps):
        fr *= 1 + r.uniform(-detune, detune)
        y += a * np.sin(2 * np.pi * fr * t + r.uniform(0, 6.28)) * np.exp(-t / d)
    c = filt(r.standard_normal(int(0.004 * SR)), hp=1800, lp=9000) * np.exp(-np.arange(int(0.004 * SR)) / (0.0008 * SR))
    y[:len(c)] += click * c / (np.max(np.abs(c)) + 1e-9)
    a = np.minimum(1, np.arange(len(y)) / (0.0006 * SR))
    return y * a / (np.max(np.abs(y)) + 1e-9)


def whoosh(sec, f0=400, f1=2500, seed=3, shape=2.0):
    """Air swish: band-passed noise sweeping f0->f1 with a swell that peaks near the end (things arriving)."""
    r = np.random.default_rng(seed); n = int(sec * SR); x = r.standard_normal(n)
    k = 24; parts = np.array_split(np.arange(n), k); y = np.zeros(n)
    for i, idx in enumerate(parts):
        fc = f0 * (f1 / f0) ** (i / (k - 1)); lo, hi = fc / 1.8, fc * 1.8
        blk = filt(x[max(0, idx[0] - 2048):idx[-1] + 2048], hp=lo, lp=hi, order=2)
        off = idx[0] - max(0, idx[0] - 2048); y[idx] = blk[off:off + len(idx)]
    t = np.linspace(0, 1, n); e = (t ** shape) * (1 - t) ** 0.35
    y *= e / (e.max() + 1e-9)
    return y / (np.max(np.abs(y)) + 1e-9)


def thud(sec=0.6, f=55, seed=5):
    t = np.arange(int(sec * SR)) / SR; fr = f * (1 + 0.6 * np.exp(-t / 0.03))
    y = np.sin(2 * np.pi * np.cumsum(fr) / SR) * np.exp(-t / 0.16)
    y += 0.5 * filt(noise(sec, seed), lp=350) * np.exp(-t / 0.05)
    return y / np.max(np.abs(y))


def rattle(sec=0.5, rate=26, seed=7, f=2600):
    """Loose-metal chatter: a train of tiny modal ticks."""
    r = np.random.default_rng(seed); y = np.zeros(int(sec * SR)); t = 0.0
    while t < sec - 0.02:
        tk = modal([f * r.uniform(0.8, 1.25), f * 2.3 * r.uniform(0.9, 1.1)], [0.012, 0.008], [1, 0.5], 0.03, click=0.6, seed=int(t * 1000) + seed)
        a = int(t * SR); y[a:a + len(tk)] += tk[:len(y) - a] * r.uniform(0.4, 1.0) * (1 - t / sec) ** 0.5
        t += r.exponential(1 / rate)
    return y / (np.max(np.abs(y)) + 1e-9)


def hiss(sec, lo=1500, hi=9000, attack=0.01, decay=0.4, flutter=0.0, seed=9):
    """Gas release: band noise, fast attack, exponential decay; optional flutter (compressor surge 'tu-tu-tu')."""
    t = np.arange(int(sec * SR)) / SR; y = filt(noise(sec, seed), hp=lo, lp=hi, order=2)
    e = np.minimum(1, t / attack) * np.exp(-t / decay)
    if flutter:
        e *= 1 - 0.6 * (0.5 + 0.5 * np.sin(2 * np.pi * flutter * t)) * np.clip((t - 0.12) / 0.1, 0, 1)
    y *= e
    return y / (np.max(np.abs(y)) + 1e-9)


def beep(sec=0.12, f=1650):
    t = np.arange(int(sec * SR)) / SR
    y = (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * 2 * f * t)) * np.minimum(1, t / 0.004) * np.minimum(1, (sec - t) / 0.02)
    return y / np.max(np.abs(y))


def tick(f=3200, sec=0.03, seed=11):
    return modal([f, f * 1.7], [0.006, 0.004], [1, 0.4], sec, click=0.8, seed=seed)


def pop(sec=0.09, f0=900, f1=420):
    t = np.arange(int(sec * SR)) / SR; fr = f1 + (f0 - f1) * np.exp(-t / 0.02)
    y = np.sin(2 * np.pi * np.cumsum(fr) / SR) * np.minimum(1, t / 0.002) * np.exp(-t / 0.03)
    return y / np.max(np.abs(y))


def sizzle(sec=1.2, seed=13):
    r = np.random.default_rng(seed); y = filt(noise(sec, seed), hp=3500, lp=11000) * 0.35
    n = len(y)
    for _ in range(int(sec * 70)):
        a = r.integers(0, n - 400); y[a:a + 300] += filt(r.standard_normal(300), hp=4000, lp=12000) * r.uniform(0.3, 1.0) * np.exp(-np.arange(300) / 60)
    t = np.arange(n) / SR; y *= np.minimum(1, t / 0.08) * np.minimum(1, (sec - t) / 0.3)
    return y / np.max(np.abs(y))


def chill(sec=1.4, seed=15):
    """Icy shimmer: glassy partials gliding down + airy noise."""
    t = np.arange(int(sec * SR)) / SR; y = np.zeros_like(t); r = np.random.default_rng(seed)
    for k in range(7):
        f0 = r.uniform(2600, 5200); fr = f0 * (1 - 0.35 * t / sec)
        y += np.sin(2 * np.pi * np.cumsum(fr) / SR + r.uniform(0, 6)) * np.exp(-t / r.uniform(0.3, 0.8)) * r.uniform(0.3, 1)
    y += 0.6 * filt(noise(sec, seed), hp=5000, lp=14000) * np.exp(-t / 0.5)
    y *= np.minimum(1, t / 0.02)
    return y / np.max(np.abs(y))


def starter(sec=0.45, seed=17):
    """Starter motor cranking: whine + compression pulses (~11 Hz)."""
    t = np.arange(int(sec * SR)) / SR
    whine = np.sin(2 * np.pi * np.cumsum(260 + 60 * t / sec) / SR) * 0.4
    pul = 0.5 + 0.5 * np.sin(2 * np.pi * 11 * t) ** 3
    y = (whine + filt(noise(sec, seed), hp=200, lp=2500) * 0.6) * pul
    return y / np.max(np.abs(y))
