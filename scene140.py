#!/usr/bin/env python3
"""Scene "140" (issue #11) on the SPI display, standalone (not part of the
slideshow; stop the show first, e.g. with "make scene").

A tiny "140" (5 pt, Ubuntu Bold) appears at the clock's height, its left edge
at a third of the width. It rises in an arc (up first, then right) into the
middle, growing until its digits are half the screen high, its color warming
from white over yellow and orange to red. In the middle it bursts into
fragments that fly over the whole screen and fade out.

Behind it: the picture /mnt/stick/background/scene-background.jpg (black if
missing; --background FILE or none).

    python3 scene140.py                     # play once on /dev/fb1
    python3 scene140.py --repeat 3
    python3 scene140.py --repeat 0          # loop until stopped (Ctrl+C)
    python3 scene140.py --preview 140.gif   # render to a GIF instead (no display)

While the number is small only the rows it covers are written, so fbtft
sends less over SPI and the frame rate is higher.
"""
import argparse
import importlib.util
import itertools
import math
import os
import pathlib
import random
import signal
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = pathlib.Path(__file__).resolve().parent
FONT = HERE / "fonts" / "Ubuntu.ttf"
TEXT = "140"

if "fbs" in sys.modules:  # already loaded (by sequence.py): share it
    fbs = sys.modules["fbs"]
else:
    _spec = importlib.util.spec_from_file_location("fbs", HERE / "fb-slideshow.py")
    fbs = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(fbs)

START_PT = 5.0      # font size at the start
END_HEIGHT = 0.5    # digit height in the middle, as part of the screen height
DEFAULT_BACKGROUND = "/mnt/stick/background/scene-background.jpg"
FRAGMENT = 6        # fragment size in px (at the number's full size)

# warm color ramp over the journey: (position 0..1, RGB)
RAMP = [(0.0, (255, 255, 255)), (0.35, (255, 236, 170)),
        (0.7, (255, 150, 50)), (1.0, (225, 25, 15))]


def ramp_color(q):
    q = min(max(q, 0.0), 1.0)
    for (q0, c0), (q1, c1) in zip(RAMP, RAMP[1:]):
        if q <= q1:
            f = (q - q0) / (q1 - q0)
            return tuple(a + (b - a) * f for a, b in zip(c0, c1))
    return RAMP[-1][1]


def ease_in_out(p):
    return p * p * (3 - 2 * p)


def ease_out(p, k=2.5):
    return 1 - (1 - p) ** k


def bold_font(size, font=FONT):
    f = ImageFont.truetype(str(font), size)
    try:
        f.set_variation_by_name("Bold")
    except (OSError, ValueError):
        pass  # static font file: use it as it is
    return f


def digit_height_per_em(font=FONT, text=TEXT):
    _, y0, _, y1 = bold_font(1000, font).getbbox(text)
    return (y1 - y0) / 1000


def render_master(height, font=FONT, text=TEXT):
    """Alpha mask (float32 0..1) of the text, cropped to its ink, with the
    digits `height` px high."""
    f = bold_font(round(height / digit_height_per_em(font, text)), font)
    x0, y0, x1, y1 = f.getbbox(text)
    img = Image.new("L", (x1 - x0 + 8, y1 - y0 + 8), 0)
    ImageDraw.Draw(img).text((4 - x0, 4 - y0), text, font=f, fill=255)
    return np.asarray(img.crop(img.getbbox()), np.float32) / 255  # ink only


def blit(canvas, alpha, color, x, y, rim=None, shadow=0.5):
    """Blend an alpha mask in a color over the canvas at (x, y) (top left,
    may be partly off screen). rim: optional mask of the same size that
    darkens the picture around the ink (readability on bright pictures).
    Returns the rows touched (y0, y1) or None."""
    h, w = alpha.shape
    H, W, _ = canvas.shape
    cx0, cy0 = max(0, x), max(0, y)
    cx1, cy1 = min(W, x + w), min(H, y + h)
    if cx0 >= cx1 or cy0 >= cy1:
        return None
    a = alpha[cy0 - y:cy1 - y, cx0 - x:cx1 - x, None]
    region = canvas[cy0:cy1, cx0:cx1].astype(np.float32)
    if rim is not None:
        region *= 1 - shadow * rim[cy0 - y:cy1 - y, cx0 - x:cx1 - x, None]
    region += (np.asarray(color, np.float32) - region) * a
    canvas[cy0:cy1, cx0:cx1] = np.rint(region).astype(np.uint8)
    return cy0, cy1


def load_background(path, size):
    """The picture behind the scene, filling the screen (cropped), or black
    when path is None or not readable."""
    w, h = size
    if path:
        try:
            return fbs.load_image(path, size, fill=True)
        except (OSError, ValueError) as e:
            fbs.log(f"no background ({e}), using black")
    return np.zeros((h, w, 3), np.uint8)


class Scene:
    def __init__(self, size, travel=6.0, pause=0.6, explode=3.0, seed=None,
                 diagonal_in=fbs.DISPLAY_DIAGONAL_IN, font=FONT, background=None):
        self.size = W, H = size
        self.background = (np.zeros((H, W, 3), np.uint8) if background is None
                           else background)
        self.travel, self.pause, self.explode = travel, pause, explode
        self.total = travel + pause + explode
        px_cm = math.hypot(W, H) / (diagonal_in * 2.54)
        self.end_h = END_HEIGHT * H
        self.master = render_master(round(self.end_h), font)
        self.aspect = aspect = self.master.shape[1] / self.master.shape[0]
        # 5 pt font size -> digit height (digits are ~0.7 em in Ubuntu)
        em = START_PT / 72 * 2.54 * px_cm
        self.start_h = em * digit_height_per_em(font)
        # start: left edge at W/3, bottom at the clock's baseline
        bottom = H - round(fbs.CLOCK_MARGIN_CM * px_cm)
        w0 = self.start_h * aspect
        self.p0 = np.array([W / 3 + w0 / 2, bottom - self.start_h / 2])
        self.p2 = np.array([W / 2, H / 2])
        self.p1 = np.array([self.p0[0], self.p2[1]])  # up first, then right
        self._fragments(random.Random(seed))
        self._cache = {}
        self._big = self._padded_master()

    # ---------------------------------------------------------------- journey
    def position(self, q):
        """Center of the number at journey progress q (quadratic Bezier)."""
        return ((1 - q) ** 2 * self.p0 + 2 * (1 - q) * q * self.p1
                + q ** 2 * self.p2)

    def height(self, q):
        """Digit height: grows geometrically (evenly to the eye)."""
        return self.start_h * (self.end_h / self.start_h) ** q

    def _padded_master(self):
        """Full-size number and its soft dark rim, padded; computed once and
        scaled down per frame (the rim scales with the number)."""
        from PIL import ImageFilter
        r = max(1, round(self.end_h * 0.06))
        mh, mw = self.master.shape
        padded = Image.new("L", (mw + 4 * r, mh + 4 * r), 0)
        padded.paste(Image.fromarray((self.master * 255).astype(np.uint8)),
                     (2 * r, 2 * r))
        rim = padded.filter(ImageFilter.MaxFilter(2 * r + 1)).filter(
            ImageFilter.GaussianBlur(r / 2 + 0.5))
        return padded, rim

    def number(self, h):
        """(alpha, rim) of the number with digits h px high; both padded so
        the soft dark rim around the ink fits."""
        h = max(2, round(h))
        if h not in self._cache:
            padded, rim = self._big
            f = h / self.master.shape[0]
            size = (max(2, round(padded.width * f)), max(2, round(padded.height * f)))
            self._cache = {h: (np.asarray(padded.resize(size, Image.LANCZOS),
                                          np.float32) / 255,
                               np.asarray(rim.resize(size, Image.BILINEAR),
                                          np.float32) / 255)}
        return self._cache[h]

    # -------------------------------------------------------------- explosion
    def _fragments(self, rng):
        """Cut the full-size number into small pieces, each flying outward
        from the center to somewhere on (or just past) the screen."""
        W, H = self.size
        m = self.master
        mh, mw = m.shape
        ox = round(self.p2[0] - mw / 2)
        oy = round(self.p2[1] - mh / 2)
        c = self.p2
        frags = []
        for y in range(0, mh, FRAGMENT):
            for x in range(0, mw, FRAGMENT):
                patch = m[y:y + FRAGMENT, x:x + FRAGMENT]
                if patch.shape != (FRAGMENT, FRAGMENT) or patch.mean() < 0.15:
                    continue  # empty, or a sliver at the edge
                pos = np.array([ox + x + patch.shape[1] / 2,
                                oy + y + patch.shape[0] / 2])
                d = pos - c
                ang = math.atan2(d[1], d[0]) + rng.uniform(-0.5, 0.5)
                v = np.array([math.cos(ang), math.sin(ang)])
                # distance to the screen border in that direction
                tx = ((W if v[0] > 0 else 0) - pos[0]) / v[0] if v[0] else math.inf
                ty = ((H if v[1] > 0 else 0) - pos[1]) / v[1] if v[1] else math.inf
                # spread evenly from near the center to just past the
                # border, so the pieces end up all over the screen
                reach = min(tx, ty) * rng.uniform(0.0, 1.15)
                frags.append((pos, v * reach, patch, rng.uniform(0.8, 1.2)))
        # as arrays, so a frame draws all pieces at once (numpy, no loop)
        self.frag_pos = np.array([f[0] for f in frags], np.float32)
        self.frag_travel = np.array([f[1] for f in frags], np.float32)
        self.frag_alpha = np.array([f[2] for f in frags], np.float32)
        self.frag_bright = np.array([f[3] for f in frags], np.float32)

    # ----------------------------------------------------------------- frames
    def frame(self, t):
        """(RGB frame, rows (y0, y1) that differ from the background, or
        None = all)."""
        W, H = self.size
        canvas = self.background.copy()
        if t < self.travel + self.pause:
            q = ease_in_out(min(t / self.travel, 1.0)) if self.travel else 1.0
            alpha, rim = self.number(self.height(q))
            h, w = alpha.shape
            cx, cy = self.position(q)
            rows = blit(canvas, alpha, ramp_color(q),
                        round(cx - w / 2), round(cy - h / 2), rim)
            return canvas, rows
        p = min((t - self.travel - self.pause) / self.explode, 1.0)
        fade = (1 - p) ** 1.4
        red = np.array(RAMP[-1][1], np.float32)
        glow = np.array([255, 120, 30], np.float32)
        g = max(0.0, 1 - p / 0.15)  # short bright flash at the burst
        grow = 1 + int(2 * p)  # fragments get a bit bigger as they spread
        a = self.frag_alpha
        if grow > 1:
            a = a.repeat(grow, axis=1).repeat(grow, axis=2)
        n, k, _ = a.shape
        top_left = np.rint(self.frag_pos + self.frag_travel * ease_out(p)
                           - k / 2).astype(np.int32)
        r = np.arange(k)
        xx = np.broadcast_to(top_left[:, 0, None, None] + r[None, None, :], a.shape)
        yy = np.broadcast_to(top_left[:, 1, None, None] + r[None, :, None], a.shape)
        col = np.minimum((red * (1 - g) + glow * g)
                         * self.frag_bright[:, None], 255)
        m = (a > 0.02) & (xx >= 0) & (xx < W) & (yy >= 0) & (yy < H)
        # pieces fade out: their opacity goes to 0, the picture shows through
        op = (a * fade)[m][:, None]
        cols = np.broadcast_to(col[:, None, None, :], a.shape + (3,))[m]
        under = self.background[yy[m], xx[m]].astype(np.float32)
        canvas[yy[m], xx[m]] = np.rint(under + (cols - under) * op).astype(np.uint8)
        return canvas, None


def _rows4(rows, H):
    y0, y1 = rows
    return y0 // 4 * 4, min(H, -(-y1 // 4) * 4)


def play(display, scene, clock=time.monotonic, sleep=time.sleep, max_fps=25):
    """Time-driven like the transitions: slow frames are skipped, the scene
    keeps its duration. Returns the number of frames written."""
    W, H = scene.size
    display.show(scene.background)
    prev = None
    frames = 0
    t0 = clock()
    while True:
        start = clock()
        t = start - t0
        if t >= scene.total:
            break
        frame, rows = scene.frame(t)
        if rows is None or prev is None:
            display.show(frame)
        else:
            dirty = (min(rows[0], prev[0]), max(rows[1], prev[1]))
            display.show_rows(frame, *_rows4(dirty, H))
        prev = rows if rows is not None else (0, H)
        frames += 1
        left = 1 / max_fps - (clock() - start)
        if left > 0:
            sleep(left)
    display.show(scene.background)
    return frames


def preview(scene, path, fps=12):
    """Render the scene into an animated GIF (for looking at it on a PC)."""
    n = max(1, round(scene.total * fps))
    imgs = [Image.fromarray(scene.frame(i / fps)[0]) for i in range(n)]
    imgs.append(Image.fromarray(scene.background))
    imgs[0].save(path, save_all=True, append_images=imgs[1:],
                 duration=round(1000 / fps), loop=0)
    return len(imgs)


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fb", default=os.environ.get("FB_DEVICE", fbs.DEFAULT_FB))
    ap.add_argument("--travel", type=float, default=6.0,
                    help="seconds for the arc into the middle (default 6)")
    ap.add_argument("--pause", type=float, default=0.6,
                    help="seconds in the middle before the burst (default 0.6)")
    ap.add_argument("--explode", type=float, default=3.0,
                    help="seconds for the burst (default 3)")
    ap.add_argument("--repeat", type=int, default=1,
                    help="times to play; 0 = loop until stopped")
    ap.add_argument("--seed", type=int, help="random seed of the fragments")
    ap.add_argument("--background", default=os.environ.get("SCENE_BACKGROUND",
                                                            DEFAULT_BACKGROUND),
                    help="picture behind the scene, fills the screen; 'none' = "
                         "black (default: %s, black if missing)" % DEFAULT_BACKGROUND)
    ap.add_argument("--size", type=fbs.parse_size, help="override, e.g. 480x320")
    ap.add_argument("--invert", choices=["auto", "on", "off"], default="auto")
    ap.add_argument("--preview", metavar="GIF",
                    help="render to an animated GIF instead of the display")
    ap.add_argument("--fps", type=float, default=12, help="frame rate of --preview")
    a = ap.parse_args(argv)
    if a.background.lower() in ("", "none", "black"):
        a.background = None
    if min(a.travel, a.explode) <= 0 or a.pause < 0 or a.repeat < 0 or a.fps <= 0:
        ap.error("durations and --fps must be positive, --repeat not negative")
    return a


def main(argv=None):
    a = parse_args(sys.argv[1:] if argv is None else argv)
    if a.preview:
        size = a.size or (480, 320)
        scene = Scene(size, a.travel, a.pause, a.explode, a.seed,
                      background=load_background(a.background, size))
        n = preview(scene, a.preview, a.fps)
        print(f"{a.preview}: {n} frames, {scene.total:.1f} s")
        return 0
    if a.size:
        info = fbs.FbInfo(a.size[0], a.size[1], 16, a.size[0] * 2)
    else:
        try:
            info = fbs.read_fb_info(a.fb)
        except OSError as e:
            fbs.log(f"Cannot read framebuffer geometry of {a.fb} ({e})")
            return 2
    invert = fbs.detect_invert() if a.invert == "auto" else a.invert == "on"
    display = fbs.Display(a.fb, info, invert)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGHUP, lambda *_: sys.exit(0))  # ssh closed
    size = (info.width, info.height)
    scene = Scene(size, a.travel, a.pause, a.explode, a.seed,
                  background=load_background(a.background, size))
    try:
        for _ in itertools.count() if a.repeat == 0 else range(a.repeat):
            t0 = time.monotonic()
            n = play(display, scene)
            fbs.log(f"scene 140: {n} frames in {time.monotonic() - t0:.1f} s")
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        display.blank()
        display.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
