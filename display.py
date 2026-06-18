#!/usr/bin/env python3
"""
Ambient display for the Pi 7" (800x480): a cycling collection of scenes.
Scenes: flow field, matrix rain, aquarium, plasma, fractal zoom, word clock,
flip clock, world clock + ISS tracker, weather radar. Auto-rotates every ~75s
with a fade transition. pygame/SDL KMSDRM fullscreen. Writes screen.png.
"""
import glob
import io
import json
import math
import os
import random
import threading
import time
import urllib.request
from datetime import datetime, timezone

os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import numpy as np
import pygame

W, H = 800, 480
HERE = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(HERE, "screen.png")
FB = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FM = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
VLG = "/usr/share/fonts/truetype/vlgothic/VL-Gothic-Regular.ttf"  # Japanese katakana


def _find(*paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return paths[-1]


# Matrix Code NFI glyphs: prefer system install, fall back to the copy bundled in the repo
MTX = _find("/usr/share/fonts/truetype/matrix/matrix-code-nfi.ttf",
            os.path.join(HERE, "fonts", "matrix-code-nfi.ttf"))
SCENE_SECS = 75

# auto-detect the display backlight (official 7" DSI = 10-0045); None if there is none
_bl = sorted(glob.glob("/sys/class/backlight/*/brightness"))
BL = _bl[0] if _bl else None
BL_LEVELS = [13, 25, 76, 102]                     # 5% / 10% / 30% / 40% (default 10%; tap cycles)


def set_brightness(v):
    if not BL:
        return
    try:
        with open(BL, "w") as f:
            f.write(str(v))
    except Exception:
        pass

PAL = []
for h in range(360):
    c = pygame.Color(0); c.hsva = (h, 80, 100, 100); PAL.append((c.r, c.g, c.b))


def font(path, sz):
    return pygame.font.Font(path, sz)


def floating_clock(screen, big, small, alpha=70, color=(255, 255, 255)):
    now = datetime.now()
    cs = big.render(now.strftime("%H:%M"), True, color); cs.set_alpha(alpha)
    screen.blit(cs, (W // 2 - cs.get_width() // 2, 150))
    ds = small.render(now.strftime("%a  %d %b"), True, color); ds.set_alpha(alpha - 10)
    screen.blit(ds, (W // 2 - ds.get_width() // 2, 312))


# ---------------- shared net state (ISS, radar) ----------------
net = {"iss": None, "radar_frames": [], "radar_imgs": {}}
netlock = threading.Lock()


def get_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "pi-display"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def get_bytes(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "pi-display"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def net_worker():
    while True:
        try:
            d = get_json("https://api.wheretheiss.at/v1/satellites/25544")
            with netlock:
                net["iss"] = (float(d["latitude"]), float(d["longitude"]))
        except Exception as e:
            print("iss err:", e, flush=True)
        time.sleep(5)


def _tilexy(lat, lon, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    latr = math.radians(lat)
    y = (1.0 - math.log(math.tan(latr) + 1.0 / math.cos(latr)) / math.pi) / 2.0 * n
    return x, y


def radar_worker(lat=1.3521, lon=103.8198, z=7, tw=3, th=2):
    """Composite RainViewer radar frames over a dark base map around `lat,lon`."""
    cxf, cyf = _tilexy(lat, lon, z)
    tx0, ty0 = int(cxf) - tw // 2, int(cyf) - th // 2
    # marker (you-are-here) pixel in the scaled WxH frame
    mx = (cxf - tx0) / tw * W
    my = (cyf - ty0) / th * H
    base = None
    while True:
        try:
            if base is None:                      # base map: fetch once, reuse
                b = pygame.Surface((tw * 256, th * 256))
                for i in range(tw):
                    for j in range(th):
                        url = f"https://a.basemaps.cartocdn.com/dark_nolabels/{z}/{tx0+i}/{ty0+j}.png"
                        t = pygame.image.load(io.BytesIO(get_bytes(url)))
                        b.blit(t, (i * 256, j * 256))
                base = b
            meta = get_json("https://api.rainviewer.com/public/weather-maps.json")
            host = meta["host"]
            frames = meta["radar"]["past"][-6:] + meta["radar"].get("nowcast", [])[:2]
            built = []
            for fr in frames:
                comp = base.copy()
                for i in range(tw):
                    for j in range(th):
                        url = f"{host}{fr['path']}/256/{z}/{tx0+i}/{ty0+j}/4/1_1.png"
                        try:
                            t = pygame.image.load(io.BytesIO(get_bytes(url)))
                            comp.blit(t, (i * 256, j * 256))
                        except Exception:
                            pass
                built.append((pygame.transform.smoothscale(comp, (W, H)), fr["time"]))
            with netlock:
                net["radar"] = {"surfs": built, "marker": (mx, my)}
            print(f"radar updated: {len(built)} frames", flush=True)
        except Exception as e:
            print("radar err:", e, flush=True)
        time.sleep(300)                            # refresh every 5 min


# ====================== SCENES ======================
class FlowField:
    name = "flow"
    clock = True

    def __init__(self):
        self.N = 1300
        self.px = [random.uniform(0, W) for _ in range(self.N)]
        self.py = [random.uniform(0, H) for _ in range(self.N)]
        self.fade = pygame.Surface((W, H)); self.fade.set_alpha(16); self.fade.fill((7, 8, 13))
        self.fb = font(FB, 150); self.fr = font(FR, 30)

    def enter(self, screen):
        screen.fill((7, 8, 13))

    def draw(self, screen, t):
        hue0 = (time.time() * 10) % 360
        screen.blit(self.fade, (0, 0))
        ft = t * 0.05
        cos, sin, line = math.cos, math.sin, pygame.draw.line
        for i in range(self.N):
            x = self.px[i]; y = self.py[i]
            a = math.pi * (sin(x * 0.0072 + ft * 0.21) + sin(y * 0.009 - ft * 0.17)
                           + 0.6 * sin((x + y) * 0.005 + ft * 0.25))
            nx = x + cos(a) * 1.9; ny = y + sin(a) * 1.9
            line(screen, PAL[int(hue0 + a * 57.3 + x * 0.08) % 360], (x, y), (nx, ny))
            if nx < 0 or nx >= W or ny < 0 or ny >= H or random.random() < 0.0025:
                nx = random.uniform(0, W); ny = random.uniform(0, H)
            self.px[i] = nx; self.py[i] = ny
        floating_clock(screen, self.fb, self.fr, 30)


class MatrixRain:
    name = "matrix"
    clock = True

    B = 22           # brightness buckets

    # depth layers, drawn back -> front:
    # (font px, blurred, ndrops/col, bf_lo, bf_hi, speed_lo, speed_hi, len_lo_frac, len_hi_frac)
    LAYERS = [
        (13, True,  0.30, 0.30, 0.55, 0.02, 0.06, 0.20, 0.50),    # FAR haze: small, blurred, dim, slow
        (17, True,  0.45, 0.45, 0.78, 0.10, 0.35, 0.35, 0.90),    # BLUR: prominent out-of-focus streams
        (15, False, 1.00, 0.60, 0.92, 0.12, 0.50, 0.40, 1.00),    # MID: crisp bulk, normal/fast
        (23, False, 0.30, 0.92, 1.0,  0.28, 0.55, 0.30, 0.80),    # NEAR: large, sharp, bright, fast
    ]

    def __init__(self):
        self.chars = list("0123456789abcdefghijklmnopqrstuvwxyz")
        self._fonts = {}
        self.cache = {}                       # (char, bucket, head, fs, blur) -> surface
        self.spcache = {}                     # spark glyphs: (char, fs, kind, bri) -> surface
        self.layers = [self._make_layer(*spec) for spec in self.LAYERS]
        # rare slow lone glyphs that fall on their own, no tail (least common element)
        self.sparks = [self._new_spark() for _ in range(8)]
        # faded BLURRED dots with a short trail, drifting down + flickering (video background)
        self.mote_lv = [self._make_mote(0.18 + 0.16 * i) for i in range(6)]
        self.motes = [self._new_mote(True) for _ in range(48)]
        self.fb = font(FB, 150); self.fr = font(FR, 30)

    def _font_at(self, sz):
        f = self._fonts.get(sz)
        if f is None:
            f = font(MTX, sz); self._fonts[sz] = f
        return f

    def _make_layer(self, fs, blur, nf, blo, bhi, slo, shi, llo, lhi):
        cols = W // fs; rows = H // fs + 1
        lyr = {"fs": fs, "blur": blur, "cols": cols, "rows": rows,
               "cells": [[random.choice(self.chars) for _ in range(rows)] for _ in range(cols)],
               "blo": blo, "bhi": bhi, "slo": slo, "shi": shi, "llo": llo, "lhi": lhi}
        lyr["drops"] = [self._new_drop(lyr, True) for _ in range(max(2, int(cols * nf)))]
        return lyr

    def _new_drop(self, lyr, anywhere=False):
        rows = lyr["rows"]
        bf = random.uniform(lyr["blo"], lyr["bhi"])
        top = -rows if anywhere else random.uniform(-rows * 0.5, -1)
        length = random.randint(max(3, int(rows * lyr["llo"])), max(4, int(rows * lyr["lhi"])))
        # crisp streams pick a style: 0 normal, 1 dot-matrix, 2 gradient-faded (only some)
        style = 0
        if not lyr["blur"]:
            r = random.random()
            if r < 0.22:
                style = 1                     # LED dot-matrix
            elif r < 0.42:
                style = 2                     # gradient: each glyph fades along its height
        return [random.randint(0, lyr["cols"] - 1), random.uniform(top, -1),
                random.uniform(lyr["slo"], lyr["shi"]), length, bf, style]

    def _new_spark(self):                     # lone glyph: varied size / brightness / focus, falls on its own
        fs = random.choice([9, 12, 16, 20, 24])
        r = random.random()
        kind = "blur" if r < 0.22 else ("grad" if r < 0.58 else "plain")
        br = random.uniform(0.35, 1.0)
        if random.random() < 0.30:            # some are very bright (near-white green)
            br = random.uniform(0.85, 1.0)
        return [random.randint(0, W - fs), random.uniform(-40, -fs),
                0.4 + 1.6 * random.random(), br, random.choice(self.chars), fs,
                random.random() < 0.4, kind]
        # x, y, speed, br, char, fs, flicker, kind

    def _spark_col(self, br):                 # faded green -> bright green -> near-white green (never pure white)
        br = max(0.0, min(1.0, br))
        lift = max(0.0, (br - 0.7) / 0.3)     # white lift only kicks in when very bright
        return (min(255, int(205 * lift)),
                min(255, int(70 + 185 * br)),
                min(255, int(35 + 30 * br + 170 * lift)))

    def _two_tone(self, base_white, top, bot):  # fill glyph shape with vertical gradient between TWO bright tones
        bw, bh = base_white.get_size()
        grad = pygame.Surface((bw, bh), pygame.SRCALPHA)
        for y in range(bh):
            f = y / (bh - 1) if bh > 1 else 0
            grad.fill((int(top[0] + (bot[0] - top[0]) * f),
                       int(top[1] + (bot[1] - top[1]) * f),
                       int(top[2] + (bot[2] - top[2]) * f), 255), (0, y, bw, 1))
        grad.blit(base_white, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)  # mask by glyph shape/alpha
        return grad

    def _spark_glyph(self, ch, fs, kind, bri):
        key = (ch, fs, kind, bri)
        s = self.spcache.get(key)
        if s is None:
            br = bri / 9.0
            ft = self._font_at(fs + 3)
            if kind == "grad":                # both ends bright: near-white-green <-> bright-green
                base_w = ft.render(ch, True, (255, 255, 255))
                s = self._two_tone(base_w, self._spark_col(min(1.0, br + 0.25)),
                                    self._spark_col(max(0.5, br - 0.05))).convert_alpha()
            else:
                base = ft.render(ch, True, self._spark_col(br))
                if kind == "blur":
                    bw, bh = base.get_size()
                    sm = pygame.transform.smoothscale(base, (max(1, bw // 4), max(1, bh // 4)))
                    s = pygame.transform.smoothscale(sm, (bw, bh)).convert_alpha()
                else:
                    s = base.convert_alpha()
            self.spcache[key] = s
        return s

    def _new_mote(self, spread=False):         # background dust: blurred trailing dot OR tiny faded text, falling + flickering
        y = random.uniform(0, H) if spread else random.uniform(-20, -2)
        x = random.randint(0, W - 12)
        speed = 0.5 + 2.2 * random.random()
        lvl = random.randint(1, 4)
        if random.random() < 0.4:              # tiny faded flickering text
            return [x, y, speed, lvl, 1, random.choice(self.chars), random.choice([8, 9, 10, 11])]
        return [x, y, speed, lvl, 0, "", 0]    # blurred trailing dot

    def _make_mote(self, bright):              # soft BLURRED dot with a short upward trail
        w, h = 6, 16
        s = pygame.Surface((w, h), pygame.SRCALPHA)
        for y in range(h):
            f = y / (h - 1)                    # 0 = top (trail tip) .. 1 = bottom (head)
            a = max(0, min(255, int(235 * bright * (0.12 + 0.88 * f * f))))
            g = max(0, min(255, int((55 + 150 * f) * bright)))
            s.fill((int(g * 0.1), g, int(g * 0.35), a), (0, y, w, 1))
        sm = pygame.transform.smoothscale(s, (2, 6))     # blur: shrink then upscale
        return pygame.transform.smoothscale(sm, (w, h)).convert_alpha()

    def _glyph(self, ch, b, head, fs, blur, style=0):
        key = (ch, b, head, fs, blur, style)
        s = self.cache.get(key)
        if s is None:
            f = b / (self.B - 1)
            if head:                           # leading char: BRIGHT GREEN (slight lift, not white)
                col = (int(40 + 100 * f), int(110 + 145 * f), int(55 + 95 * f))
            else:                              # green tail, scaled by depth*fade
                col = (int(14 * f), int(22 + 225 * f), int(48 * f))
            base = self._font_at(fs + 3).render(ch, True, col)
            if blur:                           # out-of-focus: shrink hard then upscale -> soft smear
                bw, bh = base.get_size()
                sm = pygame.transform.smoothscale(base, (max(1, bw // 4), max(1, bh // 4)))
                s = pygame.transform.smoothscale(sm, (bw, bh)).convert_alpha()
            elif style == 1:                   # LED dot-matrix: sample onto a FINE dot grid
                s = self._dotify(base, col, max(2, (fs + 3) // 6)).convert_alpha()
            elif style == 2:                   # gradient: each glyph fades along its height
                s = self._gradient(base).convert_alpha()
            else:
                s = base.convert_alpha()
            self.cache[key] = s
        return s

    def _gradient(self, base):                 # multiply glyph alpha by a vertical gradient (faint top -> solid bottom)
        bw, bh = base.get_size()
        grad = pygame.Surface((bw, bh), pygame.SRCALPHA)
        for y in range(bh):
            a = 70 + int(185 * y / max(1, bh - 1))
            grad.fill((255, 255, 255, a), (0, y, bw, 1))
        out = base.copy()
        out.blit(grad, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        return out

    def _dotify(self, base, col, step):
        bw, bh = base.get_size()
        out = pygame.Surface((bw, bh), pygame.SRCALPHA)
        alpha = pygame.surfarray.array_alpha(base)   # (bw, bh)
        rad = max(1, step // 2); half = step // 2; draw = pygame.draw.circle
        for gx in range(0, bw, step):
            sx = min(bw - 1, gx + half); colm = alpha[sx]
            for gy in range(0, bh, step):
                sy = min(bh - 1, gy + half)
                if colm[sy] > 90:                    # glyph lit here -> place a dot
                    draw(out, col, (sx, sy), rad)
        return out

    def enter(self, screen):
        screen.fill((0, 0, 0))

    def draw(self, screen, t):
        screen.fill((0, 0, 0))
        B = self.B; blit = screen.blit
        rnd = random.random; rint = random.randint; choice = random.choice
        for m in self.motes:                   # blurred trailing dots + tiny faded text, falling + flickering
            m[1] += m[2]
            if m[1] > H:
                m[:] = self._new_mote(); continue
            li = m[3] + rint(-1, 1)            # flicker brightness level
            li = 0 if li < 0 else (5 if li > 5 else li)
            if m[4] == 0:                      # blurred trailing dot
                blit(self.mote_lv[li], (m[0], int(m[1])))
            else:                              # tiny faded flickering glyph
                if rnd() < 0.25:
                    m[5] = choice(self.chars)
                blit(self._glyph(m[5], 1 + li, False, m[6], False, 2), (m[0], int(m[1])))
        for lyr in self.layers:                # FAR (back) -> NEAR (front)
            fs = lyr["fs"]; rows = lyr["rows"]; blur = lyr["blur"]; cells = lyr["cells"]
            for dp in lyr["drops"]:
                col, head, speed, L, bf, style = dp
                prev = int(head); head += speed; hr = int(head); dp[1] = head
                x = col * fs; cc = cells[col]
                if hr != prev and 0 <= hr < rows:
                    cc[hr] = choice(self.chars)
                headb = int(bf * (B - 1))
                for d in range(L):
                    r = hr - d
                    if 0 <= r < rows:
                        if d == 0:
                            blit(self._glyph(cc[r], headb, True, fs, blur, style), (x, r * fs))
                        else:
                            bb = bf * (1 - d / L) ** 2   # steep falloff: most of the trail stays dark/faded
                            if rnd() < 0.02:    # FLASH: a glyph flares bright as the stream falls
                                bb = min(1.0, bb + 0.55)
                            b = int(bb * (B - 1))
                            if b:
                                blit(self._glyph(cc[r], b, False, fs, blur, style), (x, r * fs))
                if hr - L > rows:
                    dp[:] = self._new_drop(lyr)
            for _ in range(lyr["cols"] // 3):  # subtle in-place shimmer
                cells[rint(0, lyr["cols"] - 1)][rint(0, rows - 1)] = choice(self.chars)
        # rare slow lone falling glyphs, each its own size (crisp, no trail)
        for sp in self.sparks:
            sp[1] += sp[2]; y = sp[1]
            if y > H:
                sp[:] = self._new_spark(); continue
            if y < -sp[5]:
                continue
            if rnd() < 0.16:                   # ALL individuals change glyph as they fall (none static)
                sp[4] = choice(self.chars)
            br = sp[3]
            if sp[6]:                          # only SOME also flicker in brightness
                br = max(0.2, min(1.0, br * (0.7 + 0.3 * rnd())))
            bri = max(0, min(9, int(br * 9)))
            blit(self._spark_glyph(sp[4], sp[5], sp[7], bri), (sp[0], int(y)))
        floating_clock(screen, self.fb, self.fr, 50, (150, 255, 175))


class Aquarium:
    name = "aquarium"
    clock = True

    def __init__(self):
        self.bg = (8, 30, 55)
        self.fish = []
        for _ in range(12):
            self.fish.append({
                "x": random.uniform(0, W), "y": random.uniform(40, H - 20),
                "v": random.choice([-1, 1]) * random.uniform(0.6, 1.8),
                "s": random.uniform(10, 22),
                "c": random.choice([(255, 140, 60), (255, 210, 70), (120, 200, 255),
                                    (255, 110, 140), (160, 255, 180)])})
        self.bubbles = [{"x": random.uniform(0, W), "y": random.uniform(0, H),
                         "r": random.uniform(2, 5), "v": random.uniform(0.5, 1.4)} for _ in range(40)]
        self.fb = font(FB, 130); self.fr = font(FR, 28)

    def enter(self, screen):
        pass

    def draw(self, screen, t):
        for y in range(0, H, 4):  # gradient water
            c = (8, 24 + y // 16, 45 + y // 8)
            pygame.draw.rect(screen, c, (0, y, W, 4))
        for b in self.bubbles:
            b["y"] -= b["v"]
            if b["y"] < 0:
                b["y"] = H; b["x"] = random.uniform(0, W)
            pygame.draw.circle(screen, (120, 190, 230), (int(b["x"]), int(b["y"])), int(b["r"]), 1)
        for f in self.fish:
            f["x"] += f["v"]
            if f["x"] < -30: f["x"] = W + 30
            if f["x"] > W + 30: f["x"] = -30
            x, y, s = int(f["x"]), int(f["y"] + math.sin(t * 2 + f["x"] * 0.05) * 4), f["s"]
            pygame.draw.ellipse(screen, f["c"], (x - s, y - s // 2, 2 * s, s))
            tx = s if f["v"] < 0 else -s
            pygame.draw.polygon(screen, f["c"], [(x + tx, y), (x + int(tx * 1.7), y - s // 2), (x + int(tx * 1.7), y + s // 2)])
            eye = x - tx // 2
            pygame.draw.circle(screen, (255, 255, 255), (eye, y - 2), 2)
        floating_clock(screen, self.fb, self.fr, 30)


class Plasma:
    name = "plasma"
    clock = True

    def __init__(self):
        self.w, self.h = 200, 120
        xs = np.linspace(0, math.pi * 4, self.w)
        ys = np.linspace(0, math.pi * 4, self.h)
        self.gx, self.gy = np.meshgrid(xs, ys)
        self.fb = font(FB, 150); self.fr = font(FR, 30)

    def enter(self, screen):
        pass

    def draw(self, screen, t):
        v = (np.sin(self.gx + t) + np.sin(self.gy + t * 0.8)
             + np.sin((self.gx + self.gy + t) * 0.7)
             + np.sin(np.sqrt(self.gx ** 2 + self.gy ** 2) + t))
        hue = ((v + 4) / 8 * 255).astype(np.uint8)
        rgb = np.zeros((self.h, self.w, 3), np.uint8)
        rgb[..., 0] = (128 + 127 * np.sin(v + 0)).astype(np.uint8)
        rgb[..., 1] = (128 + 127 * np.sin(v + 2)).astype(np.uint8)
        rgb[..., 2] = (128 + 127 * np.sin(v + 4)).astype(np.uint8)
        surf = pygame.image.frombuffer(rgb.tobytes(), (self.w, self.h), "RGB")
        screen.blit(pygame.transform.smoothscale(surf, (W, H)), (0, 0))
        floating_clock(screen, self.fb, self.fr, 30)


class Fractal:
    name = "fractal"
    clock = False

    def __init__(self):
        self.w, self.h = 240, 144
        self.cx, self.cy = -0.743643887, 0.13182590  # seahorse valley
        self.zoom = 3.0

    def enter(self, screen):
        self.zoom = 3.0

    def draw(self, screen, t):
        self.zoom *= 0.97
        if self.zoom < 0.0008:
            self.zoom = 3.0
        w, h = self.w, self.h
        x = np.linspace(self.cx - self.zoom, self.cx + self.zoom, w)
        y = np.linspace(self.cy - self.zoom * h / w, self.cy + self.zoom * h / w, h)
        cx, cy = np.meshgrid(x, y)
        c = cx + 1j * cy
        z = np.zeros_like(c); div = np.zeros(c.shape, np.int32)
        for i in range(60):
            z = z * z + c
            m = (np.abs(z) > 2) & (div == 0)
            div[m] = i
        div[div == 0] = 60
        hue = (div * 4 + t * 20).astype(np.uint8)
        rgb = np.zeros((h, w, 3), np.uint8)
        rgb[..., 0] = (128 + 127 * np.sin(div * 0.3)).astype(np.uint8)
        rgb[..., 1] = (128 + 127 * np.sin(div * 0.3 + 2)).astype(np.uint8)
        rgb[..., 2] = (128 + 127 * np.sin(div * 0.3 + 4)).astype(np.uint8)
        surf = pygame.image.frombuffer(rgb.tobytes(), (w, h), "RGB")
        screen.blit(pygame.transform.smoothscale(surf, (W, H)), (0, 0))


class WordClock:
    name = "wordclock"
    clock = False
    GRID = ["ITLISHALFTEN", "QUARTERTWENTY", "FIVEMINUTESTO", "PASTONETWOTHREE",
            "FOURFIVESIXSEVEN", "EIGHTNINETENELEVEN", "TWELVEOCLOCK"]

    def __init__(self):
        self.font = font(FB, 30)

    def enter(self, screen):
        pass

    # word -> (row, col_start, col_end) in GRID
    HOURS = {0: (6, 0, 5), 1: (3, 4, 6), 2: (3, 7, 9), 3: (3, 10, 14), 4: (4, 0, 3),
             5: (4, 4, 7), 6: (4, 8, 10), 7: (4, 11, 15), 8: (5, 0, 4), 9: (5, 5, 8),
             10: (5, 9, 11), 11: (5, 12, 17)}
    MINS = {5: [(2, 0, 3), (3, 0, 3)], 10: [(0, 9, 11), (3, 0, 3)], 15: [(1, 0, 6), (3, 0, 3)],
            20: [(1, 7, 12), (3, 0, 3)], 25: [(1, 7, 12), (2, 0, 3), (3, 0, 3)],
            30: [(0, 5, 8), (3, 0, 3)], 35: [(1, 7, 12), (2, 0, 3), (2, 11, 12)],
            40: [(1, 7, 12), (2, 11, 12)], 45: [(1, 0, 6), (2, 11, 12)],
            50: [(0, 9, 11), (2, 11, 12)], 55: [(2, 0, 3), (2, 11, 12)]}

    def _on_cells(self):
        now = datetime.now(); h = now.hour % 12; m = now.minute
        mm = (m // 5) * 5
        spans = [(0, 0, 1), (0, 3, 4)]            # IT IS
        spans += self.MINS.get(mm, [])
        dh = (h + 1 if mm > 30 else h) % 12
        spans.append(self.HOURS[dh])
        if mm == 0:
            spans.append((6, 6, 11))              # O'CLOCK
        on = set()
        for r, c0, c1 in spans:
            for c in range(c0, c1 + 1):
                on.add((r, c))
        return on

    def draw(self, screen, t):
        screen.fill((10, 12, 20))
        on = self._on_cells()
        y = 40
        for r, row in enumerate(self.GRID):
            x = 80
            for c, ch in enumerate(row):
                g = self.font.render(ch, True, (255, 220, 120) if (r, c) in on else (48, 53, 66))
                screen.blit(g, (x, y)); x += 30
            y += 58
        now = datetime.now()
        ds = font(FR, 26).render(now.strftime("%H:%M  ·  %a %d %b"), True, (120, 130, 150))
        screen.blit(ds, (W // 2 - ds.get_width() // 2, H - 36))


class FlipClock:
    name = "flipclock"
    clock = False

    def __init__(self):
        self.big = font(FB, 150); self.sm = font(FR, 30)

    def enter(self, screen):
        pass

    def _card(self, screen, s, x, y, w, h):
        pygame.draw.rect(screen, (28, 30, 40), (x, y, w, h), border_radius=14)
        pygame.draw.line(screen, (12, 13, 18), (x, y + h // 2), (x + w, y + h // 2), 3)
        g = self.big.render(s, True, (240, 244, 255))
        screen.blit(g, (x + w // 2 - g.get_width() // 2, y + h // 2 - g.get_height() // 2))

    def draw(self, screen, t):
        screen.fill((14, 15, 22))
        now = datetime.now(); hh = now.strftime("%H"); mm = now.strftime("%M")
        cw, ch, gap = 150, 210, 24
        total = cw * 2 + gap
        x0 = W // 2 - total - 20
        self._card(screen, hh, x0, 80, cw, ch)
        self._card(screen, mm, x0 + total + 40, 80, cw, ch)
        col = self.big.render(":", True, (240, 244, 255))
        screen.blit(col, (W // 2 - col.get_width() // 2, 110))
        ds = self.sm.render(now.strftime("%A  %d %B %Y"), True, (120, 130, 150))
        screen.blit(ds, (W // 2 - ds.get_width() // 2, H - 46))


class WorldISS:
    name = "world-iss"
    clock = False
    CITIES = [("SGP", 1.35, 103.8, "Asia/Singapore"), ("NYC", 40.7, -74.0, "America/New_York"),
              ("LON", 51.5, -0.1, "Europe/London"), ("TOK", 35.7, 139.7, "Asia/Tokyo")]

    def __init__(self):
        self.sm = font(FB, 22); self.tiny = font(FR, 18); self.big = font(FB, 40)
        self.trail = []

    def enter(self, screen):
        pass

    def _proj(self, lat, lon):
        return int((lon + 180) / 360 * W), int((90 - lat) / 180 * (H - 120) + 20)

    def draw(self, screen, t):
        screen.fill((6, 12, 24))
        # lat/lon grid
        for lon in range(-180, 181, 30):
            x = int((lon + 180) / 360 * W); pygame.draw.line(screen, (20, 34, 58), (x, 20), (x, H - 100), 1)
        for lat in range(-90, 91, 30):
            y = int((90 - lat) / 180 * (H - 120) + 20); pygame.draw.line(screen, (20, 34, 58), (0, y), (W, y), 1)
        with netlock:
            iss = net["iss"]
        if iss:
            x, y = self._proj(*iss)
            self.trail.append((x, y)); self.trail = self.trail[-60:]
            if len(self.trail) > 1:
                pygame.draw.lines(screen, (90, 170, 255), False, self.trail, 2)
            pygame.draw.circle(screen, (255, 80, 80), (x, y), 6)
            pygame.draw.circle(screen, (255, 80, 80), (x, y), 12, 1)
            lab = self.tiny.render(f"ISS  {iss[0]:.1f}, {iss[1]:.1f}", True, (255, 140, 140))
            screen.blit(lab, (min(x + 10, W - lab.get_width()), y - 8))
        # city clocks
        try:
            from zoneinfo import ZoneInfo
            bx = 10
            for name, lat, lon, tz in self.CITIES:
                tm = datetime.now(ZoneInfo(tz)).strftime("%H:%M")
                g = self.sm.render(f"{name} {tm}", True, (210, 220, 240))
                screen.blit(g, (bx, H - 90)); bx += g.get_width() + 24
        except Exception:
            pass
        title = self.tiny.render("LIVE ISS POSITION", True, (90, 170, 255))
        screen.blit(title, (10, H - 60))


class WeatherRadar:
    name = "radar"
    clock = False

    def __init__(self):
        self.idx = 0; self.acc = 0
        self.f = font(FB, 22); self.fs = font(FR, 18)

    def enter(self, screen):
        self.idx = 0; self.acc = 0

    def draw(self, screen, t):
        screen.fill((6, 10, 18))
        with netlock:
            rd = net.get("radar")
        if not rd or not rd["surfs"]:
            msg = self.f.render("Loading weather radar…", True, (150, 160, 180))
            screen.blit(msg, (W // 2 - msg.get_width() // 2, H // 2 - 12))
            return
        surfs = rd["surfs"]
        self.acc += 1
        if self.acc >= 6:                          # ~5 frames/sec animation
            self.idx = (self.idx + 1) % len(surfs); self.acc = 0
        surf, ts = surfs[self.idx]
        screen.blit(surf, (0, 0))
        # you-are-here marker
        mx, my = rd["marker"]
        pygame.draw.circle(screen, (255, 255, 255), (int(mx), int(my)), 5)
        pygame.draw.circle(screen, (255, 255, 255), (int(mx), int(my)), 11, 1)
        # bottom strip: label + frame progress
        pygame.draw.rect(screen, (0, 0, 0), (0, H - 34, W, 34))
        when = datetime.fromtimestamp(ts).strftime("%H:%M")
        lab = self.fs.render(f"Rain radar · Singapore · {when}", True, (220, 230, 245))
        screen.blit(lab, (10, H - 28))
        n = len(surfs)
        for i in range(n):
            c = (90, 170, 255) if i == self.idx else (60, 66, 82)
            pygame.draw.circle(screen, c, (W - 12 - (n - 1 - i) * 16, H - 17), 4)


SCENES = [FlowField, MatrixRain, Aquarium, Fractal, WordClock, FlipClock, WorldISS, WeatherRadar]


def main():
    pygame.init(); pygame.font.init(); pygame.mouse.set_visible(False)
    screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN)
    clk = pygame.time.Clock()
    threading.Thread(target=net_worker, daemon=True).start()
    threading.Thread(target=radar_worker, daemon=True).start()

    # SCENE env var pins the display to one scene (no cycling); else cycle all
    pin = os.environ.get("SCENE", "").strip().lower()
    names = [s.name for s in SCENES]
    order = [names.index(pin)] if pin in names else list(range(len(SCENES)))
    idx = 0
    scene = SCENES[order[0]]()
    scene.enter(screen)
    scene_start = time.time()
    t0 = time.time()
    frame = 0
    fade = pygame.Surface((W, H)); fade.fill((0, 0, 0))
    transition = 0  # 0..1 fade

    bl_idx = BL_LEVELS.index(25)    # start at 10%
    set_brightness(BL_LEVELS[bl_idx])
    start_t = time.time()
    last_tap = 0.0
    pygame.event.clear()            # drop spurious startup touch/mouse events

    while True:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return
            if ev.type in (pygame.FINGERDOWN, pygame.MOUSEBUTTONDOWN):
                now_t = time.time()
                if now_t - start_t > 1.5 and now_t - last_tap > 0.3:   # skip startup events; debounce taps
                    last_tap = now_t
                    bl_idx = (bl_idx + 1) % len(BL_LEVELS)
                    set_brightness(BL_LEVELS[bl_idx])   # tap cycles 10% -> 30% -> 40% -> 5% -> back

        t = time.time() - t0
        try:
            scene.draw(screen, t)
        except Exception as e:
            if frame % 120 == 0:
                print("scene err", scene.name, e, flush=True)

        # scene name tag (bottom-right, subtle)
        if time.time() - scene_start < 4:
            tag = font(FR, 18).render(scene.name, True, (90, 95, 115))
            screen.blit(tag, (W - tag.get_width() - 8, H - 24))

        pygame.display.flip()
        frame += 1
        if frame % 30 == 0:
            try: pygame.image.save(screen, SHOT)
            except Exception: pass

        if len(order) > 1 and time.time() - scene_start > SCENE_SECS:
            idx = (idx + 1) % len(order)
            scene = SCENES[order[idx]]()
            scene.enter(screen)
            scene_start = time.time()

        clk.tick(30)


if __name__ == "__main__":
    main()
