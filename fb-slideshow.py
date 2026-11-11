#!/usr/bin/env python3
"""Slideshow for the small SPI TFT (fbtft) on a Raspberry Pi.

Shows the JPEGs from a directory (default: /mnt/stick/bilder) in order on
the framebuffer, with changing, randomly chosen transitions.

Unlike fbi, this script writes directly into the framebuffer (mmap), because
that is the only way to render transition frames. As with fb-image-watcher.sh:
 - the framebuffer number may vary (FB_DEVICE, default /dev/fb1)
 - the waveshare35a overlay drives the panel with inverted colors
   -> detected from the device tree (or forced with --invert on/off)

Pause/resume:      kill -USR1 <pid>   (or pkill -USR1 -f fb-slideshow.py)
Color diagnostics: --testcard [bars|ramps] [--gamma 2.0 | R,G,B]

Dependencies:  sudo apt install python3-pil python3-numpy
"""
import argparse
import math
import mmap
import os
import random
import re
import signal
import stat
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple

import numpy as np
from PIL import Image, ImageOps

DEFAULT_DIR = "/mnt/stick/bilder"
DEFAULT_FB = "/dev/fb1"
DT_SPI = "/proc/device-tree/soc/spi@7e204000"
SYSFS_GRAPHICS = "/sys/class/graphics"
IMAGE_EXT = (".jpg", ".jpeg")


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Framebuffer
# --------------------------------------------------------------------------
class FbInfo(NamedTuple):
    width: int
    height: int
    bpp: int
    stride: int  # bytes per row


def read_fb_info(dev, sysfs=SYSFS_GRAPHICS):
    """Geometry from sysfs (no fbset needed)."""
    base = os.path.join(sysfs, os.path.basename(dev))

    def rd(name):
        with open(os.path.join(base, name)) as f:
            return f.read().strip()

    w, h = (int(x) for x in rd("virtual_size").split(","))
    bpp = int(rd("bits_per_pixel"))
    try:
        stride = int(rd("stride"))
    except OSError:
        stride = w * bpp // 8
    return FbInfo(w, h, bpp, stride)


def detect_invert(dt_dir=DT_SPI):
    """waveshare35a drives the panel inverted, tft35a does not."""
    try:
        names = os.listdir(dt_dir)
    except OSError:
        return False
    return any(n.lower().startswith("waveshare35a@") for n in names)


def _triple(x):
    return (float(x),) * 3 if isinstance(x, (int, float)) else tuple(float(v) for v in x)


def apply_gamma(frame, gamma, limit=255.0, black=0.0):
    """Per-channel tone curve: out = black + (limit - black) * (in/255)**gamma.

    gamma: number or (R,G,B); >1 darkens the midtones.
    limit: highest raw value used (number or (R,G,B), 1..255). For panels
    with a folded response curve, use only the first monotonic range.
    black: raw value for input 0 (number or (R,G,B)); compensates a color
    cast in the blacks by lifting the weaker channels."""
    g, lim, bl = _triple(gamma), _triple(limit), _triple(black)
    if g == (1.0,) * 3 and lim == (255.0,) * 3 and bl == (0.0,) * 3:
        return frame
    x = np.arange(256) / 255.0
    lut = np.stack([np.round(b + (li - b) * x ** gi)
                    for gi, li, b in zip(g, lim, bl)], axis=1)
    return lut.astype(np.uint8)[frame, np.arange(3)]


_BAYER4 = (np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]],
                    np.float32) + 0.5) / 16


def dither565(frame):
    """Ordered 4x4 dithering before reduction to RGB565 (R/B 5 bits, G 6 bits)."""
    h, w, _ = frame.shape
    t = np.tile(_BAYER4, (-(-h // 4), -(-w // 4)))[:h, :w]
    out = frame.astype(np.float32) + t[..., None] * np.array([8, 4, 8], np.float32)
    return np.minimum(out, 255).astype(np.uint8)


def parse_gamma(s):
    try:
        vals = [float(v) for v in s.split(",")]
    except ValueError:
        vals = []
    if len(vals) == 1:
        vals *= 3
    if len(vals) != 3 or any(v <= 0 for v in vals):
        raise argparse.ArgumentTypeError(
            "format: G or R,G,B with values > 0, e.g. 2.2 or 2.4,1.8,2.4")
    return tuple(vals)


def parse_black(s):
    try:
        vals = [float(v) for v in s.split(",")]
    except ValueError:
        vals = []
    if len(vals) == 1:
        vals *= 3
    if len(vals) != 3 or any(not 0 <= v <= 254 for v in vals):
        raise argparse.ArgumentTypeError(
            "format: N or R,G,B with 0 <= N <= 254, e.g. 22,16,0")
    return tuple(vals)


def parse_limit(s):
    try:
        vals = [float(v) for v in s.split(",")]
    except ValueError:
        vals = []
    if len(vals) == 1:
        vals *= 3
    if len(vals) != 3 or any(not 1 <= v <= 255 for v in vals):
        raise argparse.ArgumentTypeError(
            "format: N or R,G,B with 1 <= N <= 255, e.g. 85")
    return tuple(vals)


def encode_frame(frame, info, invert, dither=False):
    """RGB frame (HxWx3 uint8) -> raw bytes for the framebuffer."""
    if invert:
        frame = 255 - frame
    if dither and info.bpp == 16:
        frame = dither565(frame)
    h, w, _ = frame.shape
    r, g, b = frame[..., 0], frame[..., 1], frame[..., 2]
    if info.bpp == 16:  # RGB565, little endian (fbtft)
        px = (
            ((r.astype(np.uint16) >> 3) << 11)
            | ((g.astype(np.uint16) >> 2) << 5)
            | (b.astype(np.uint16) >> 3)
        )
        row = px.astype("<u2").view(np.uint8).reshape(h, w * 2)
    elif info.bpp == 32:  # BGRA (e.g. HDMI framebuffer)
        px = np.empty((h, w, 4), np.uint8)
        px[..., 0], px[..., 1], px[..., 2], px[..., 3] = b, g, r, 255
        row = px.reshape(h, w * 4)
    else:
        raise ValueError(f"bpp {info.bpp} not supported (only 16/32)")
    if info.stride == row.shape[1]:
        return row.tobytes()
    out = np.zeros((h, info.stride), np.uint8)
    out[:, : row.shape[1]] = row
    return out.tobytes()


class Display:
    def __init__(self, dev, info, invert, gamma=1.0, limit=255.0, dither=False,
                 black=0.0):
        self.info = info
        self.invert = invert
        self.gamma = gamma
        self.limit = limit
        self.dither = dither
        self.black = black
        self.size = (info.width, info.height)
        length = info.stride * info.height
        self._f = open(dev, "r+b")
        if stat.S_ISREG(os.fstat(self._f.fileno()).st_mode):
            self._f.truncate(length)  # tests: regular file as fake framebuffer
        self._m = mmap.mmap(self._f.fileno(), length)

    def show(self, frame):
        frame = apply_gamma(frame, self.gamma, self.limit, self.black)
        data = encode_frame(frame, self.info, self.invert, self.dither)
        self._m[: len(data)] = data

    def blank(self):
        """Deepest black of the panel: raw value 0, without gamma/limit/black/dither."""
        w, h = self.size
        data = encode_frame(np.zeros((h, w, 3), np.uint8), self.info, self.invert)
        self._m[: len(data)] = data

    def close(self):
        self._m.close()
        self._f.close()


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------
def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def list_images(directory):
    """JPEGs, naturally sorted (img_2 before img_10); empty if the stick is missing."""
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    good = [
        n for n in names
        if n.lower().endswith(IMAGE_EXT) and not n.startswith(".")
    ]
    return [os.path.join(directory, n) for n in sorted(good, key=natural_key)]


def playlist(directory, shuffle, rng, loop=True, empty_wait=5.0,
             sleep=time.sleep, log=log):
    """Endless generator; rescans the directory every round."""
    while True:
        files = list_images(directory)
        if not files:
            if not loop:
                return
            log(f"No images in {directory} (stick not mounted?) - waiting")
            sleep(empty_wait)
            continue
        if shuffle:
            rng.shuffle(files)
        yield from files
        if not loop:
            return


def load_image(path, size, fill=False):
    """Load an image and fit it to the display size -> HxWx3 uint8."""
    w, h = size
    m = max(w, h)
    with Image.open(path) as im:
        im.draft("RGB", (m, m))  # fast downscaling inside the JPEG decoder
        im = ImageOps.exif_transpose(im).convert("RGB")
    if fill:
        im = ImageOps.fit(im, (w, h), Image.LANCZOS)
    else:
        im = ImageOps.contain(im, (w, h), Image.LANCZOS)
        canvas = Image.new("RGB", (w, h))
        canvas.paste(im, ((w - im.width) // 2, (h - im.height) // 2))
        im = canvas
    return np.array(im, dtype=np.uint8)


# --------------------------------------------------------------------------
# Transitions: make_xxx(rng, w, h) -> f(a, b, p), p in (0,1) -> frame.
# Random parameters (direction, center, block size ...) are rolled on each
# call of the factory, so that no two transitions are ever identical.
# --------------------------------------------------------------------------
def _smooth(p):
    return p * p * (3 - 2 * p)


def _mix(a, b, p):
    k = int(p * 256)
    return (
        (a.astype(np.uint16) * (256 - k) + b.astype(np.uint16) * k) >> 8
    ).astype(np.uint8)


def _blend(a, b, mask):
    """mask: float 0..1, (H,W) or broadcastable; 1 = b."""
    af = a.astype(np.float32)
    out = af + (b.astype(np.float32) - af) * mask[..., None]
    return (out + 0.5).astype(np.uint8)


def _grid(w, h):
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    return xs, ys


def make_fade(rng, w, h):
    return _mix


def make_wipe(rng, w, h):
    """Soft edge that sweeps across the image in one of 8 directions."""
    ang = math.radians(rng.choice([0, 45, 90, 135, 180, 225, 270, 315]))
    xs, ys = _grid(w, h)
    c = xs * math.cos(ang) + ys * math.sin(ang)
    c = (c - c.min()) / (c.max() - c.min())
    f = 0.2

    def run(a, b, p):
        return _blend(a, b, np.clip((p * (1 + f) - c) / f, 0, 1))
    return run


def make_push(rng, w, h):
    """The new image pushes the old one out of the frame."""
    d = rng.choice(["left", "right", "up", "down"])

    def run(a, b, p):
        if d in ("left", "right"):
            off = int(round(w * p))
            if d == "left":
                return np.concatenate((a[:, off:], b[:, :off]), axis=1)
            return np.concatenate((b[:, w - off:], a[:, : w - off]), axis=1)
        off = int(round(h * p))
        if d == "up":
            return np.concatenate((a[off:], b[:off]), axis=0)
        return np.concatenate((b[h - off:], a[: h - off]), axis=0)
    return run


def make_blinds(rng, w, h):
    """Venetian blinds: n stripes open simultaneously."""
    vertical = rng.random() < 0.5
    n = rng.choice([6, 8, 10, 12, 16])
    size = w if vertical else h
    local = (np.arange(size, dtype=np.float32) * n / size) % 1.0

    def run(a, b, p):
        m = local < p
        m = m[None, :, None] if vertical else m[:, None, None]
        return np.where(m, b, a)
    return run


def make_dissolve(rng, w, h):
    """Random blocks (mosaic), rows (TV scanlines) or columns."""
    bh, bw = rng.choice([(8, 8), (16, 16), (32, 32), (2, w), (h, 6)])
    ny, nx = -(-h // bh), -(-w // bw)
    gen = np.random.default_rng(rng.getrandbits(32))
    rank = gen.permutation(ny * nx).reshape(ny, nx) / (ny * nx)
    full = np.repeat(np.repeat(rank, bh, 0), bw, 1)[:h, :w]

    def run(a, b, p):
        return np.where((full < p)[..., None], b, a)
    return run


def make_iris(rng, w, h):
    """A circle grows from a random point (or shrinks to reveal the new image)."""
    cx, cy = w * rng.uniform(0.25, 0.75), h * rng.uniform(0.25, 0.75)
    reverse = rng.random() < 0.5
    xs, ys = _grid(w, h)
    dist = np.hypot(xs - cx, ys - cy)
    rmax = float(dist.max())
    fe = 0.06 * max(w, h)

    def run(a, b, p):
        if not reverse:
            r = p * (rmax + fe)
            return _blend(a, b, np.clip((r - dist) / fe, 0, 1))
        r = (1 - p) * (rmax + fe) - fe
        return _blend(a, b, np.clip((dist - r) / fe, 0, 1))
    return run


def make_zoom(rng, w, h):
    """Old image zooms away / new image zooms in, cross-fading meanwhile."""
    zoom_out_old = rng.random() < 0.5
    amount = 0.5

    def zoomed(img, s):
        cw, ch = w / s, h / s
        box = ((w - cw) / 2, (h - ch) / 2, (w + cw) / 2, (h + ch) / 2)
        return np.asarray(
            Image.fromarray(img).resize((w, h), Image.BILINEAR, box=box)
        )

    def run(a, b, p):
        if zoom_out_old:
            return _mix(zoomed(a, 1 + amount * p), b, p)
        return _mix(a, zoomed(b, 1 + amount * (1 - p)), p)
    return run


def make_split(rng, w, h):
    """Curtain: opens from the center outwards (or from the edges to the center)."""
    vertical = rng.random() < 0.5
    edges_first = rng.random() < 0.5
    size = w if vertical else h
    d = np.abs(np.arange(size, dtype=np.float32) - (size - 1) / 2) / (size / 2)
    d = np.clip(d, 0, 1)
    c = 1 - d if edges_first else d
    c = c[None, :] if vertical else c[:, None]
    f = 0.15

    def run(a, b, p):
        return _blend(a, b, np.broadcast_to(
            np.clip((p * (1 + f) - c) / f, 0, 1), (h, w)))
    return run


TRANSITIONS = {
    "fade": make_fade,
    "wipe": make_wipe,
    "push": make_push,
    "blinds": make_blinds,
    "dissolve": make_dissolve,
    "iris": make_iris,
    "zoom": make_zoom,
    "split": make_split,
}


def make_transition(name, rng, size):
    """Factory + guarantee: p<=0 returns exactly a, p>=1 exactly b."""
    if name not in TRANSITIONS:
        raise ValueError(f"Unknown transition: {name}")
    fn = TRANSITIONS[name](rng, size[0], size[1])

    def run(a, b, p):
        if p <= 0:
            return a
        if p >= 1:
            return b
        return fn(a, b, p)
    return run


def run_transition(display, fn, a, b, duration, clock=time.monotonic):
    """Time-driven: the SPI bandwidth decides how many frames there will be."""
    if duration <= 0:
        display.show(b)
        return
    t0 = clock()
    while True:
        p = (clock() - t0) / duration
        if p >= 1:
            break
        display.show(fn(a, b, _smooth(max(p, 0.0))))
    display.show(b)


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------
def make_testcard(w, h, kind="bars"):
    """Diagnostic test card.

    bars : color bars (top) + 16 gray levels (bottom)
    ramps: 16 steps each for gray, red, green, blue (rows from top to bottom)
    """
    img = np.zeros((h, w, 3), np.uint8)
    steps = ((np.arange(w) * 16 // w) * 17).astype(np.uint8)
    if kind == "ramps":
        for i, ch in enumerate((None, 0, 1, 2)):
            rows = slice(i * h // 4, (i + 1) * h // 4)
            if ch is None:
                img[rows] = steps[None, :, None]
            else:
                img[rows, :, ch] = steps[None, :]
        return img
    bars = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0),
            (255, 0, 255), (255, 0, 0), (0, 0, 255), (0, 0, 0)]
    top = h // 2
    for i, c in enumerate(bars):
        img[:top, i * w // 8:(i + 1) * w // 8] = c
    img[top:] = steps[None, :, None]
    return img


def slideshow(display, paths, hold, duration, fill, transitions, rng,
              sleep=time.sleep, clock=time.monotonic, paused=lambda: False):
    size = display.size
    w, h = size
    paths = iter(paths)
    loader = ThreadPoolExecutor(max_workers=1)

    def load(path):
        try:
            return path, load_image(path, size, fill)
        except Exception as e:  # broken / partially copied file
            log(f"Skipping {path}: {e}")
            return path, None

    def submit():
        p = next(paths, None)
        return None if p is None else loader.submit(load, p)

    prev = np.zeros((h, w, 3), np.uint8)  # start from black
    prev_path = None
    last = None
    fut = submit()
    try:
        while fut is not None:
            path, img = fut.result()
            fut = submit()  # load the next image during the hold time
            if img is None:
                continue
            if path != prev_path:
                choices = [t for t in transitions if t != last] or transitions
                last = rng.choice(choices)
                fn = make_transition(last, rng, size)
                run_transition(display, fn, prev, img, duration, clock)
                prev, prev_path = img, path
            sleep(hold)
            while paused():  # image stays until the next SIGUSR1 arrives
                sleep(0.2)
    finally:
        loader.shutdown(wait=False, cancel_futures=True)


def parse_size(s):
    m = re.fullmatch(r"(\d+)x(\d+)", s)
    if not m:
        raise argparse.ArgumentTypeError("format: WIDTHxHEIGHT, e.g. 480x320")
    return int(m.group(1)), int(m.group(2))


def parse_args(argv, env=os.environ):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("directory", nargs="?",
                    default=env.get("IMAGE_DIR", env.get("BILDER_DIR", DEFAULT_DIR)))
    ap.add_argument("--fb", default=env.get("FB_DEVICE", DEFAULT_FB))
    ap.add_argument("--hold", type=float, default=float(env.get("HOLD", 10)),
                    help="seconds per image (default 10)")
    ap.add_argument("--duration", type=float,
                    default=float(env.get("DURATION", 1.2)),
                    help="transition duration in seconds (default 1.2)")
    ap.add_argument("--shuffle", action="store_true", help="random order")
    ap.add_argument("--fill", action="store_true",
                    help="crop the image instead of black borders")
    ap.add_argument("--transitions", default=",".join(TRANSITIONS),
                    help="comma-separated list, default: all (%s)" % ",".join(TRANSITIONS))
    ap.add_argument("--invert", choices=["auto", "on", "off"], default="auto")
    ap.add_argument("--size", type=parse_size, help="override, e.g. 480x320")
    ap.add_argument("--bpp", type=int, help="override, e.g. 16")
    ap.add_argument("--gamma", type=parse_gamma, default=env.get("GAMMA", "1.0"),
                    help="tone curve in**gamma: one value or R,G,B "
                         "(e.g. 2.4,1.8,2.4); >1 = darker midtones")
    ap.add_argument("--limit", type=parse_limit, default=env.get("LIMIT", "255"),
                    help="highest raw value (1..255), one value or R,G,B; "
                         "e.g. 85 for a folded panel response curve")
    ap.add_argument("--black", type=parse_black, default=env.get("BLACK", "0"),
                    help="raw value for black, one value or R,G,B (e.g. 22,16,0): "
                         "compensates purple-tinted black")
    ap.add_argument("--dither", action="store_true",
                    help="ordered dithering before RGB565 (against banding)")
    ap.add_argument("--testcard", nargs="?", const="bars", default=None,
                    choices=["bars", "ramps"],
                    help="only show a test card (bars or ramps)")
    ap.add_argument("--once", action="store_true", help="only one round (testing)")
    args = ap.parse_args(argv)
    names = [t.strip() for t in args.transitions.split(",") if t.strip()]
    bad = [t for t in names if t not in TRANSITIONS]
    if bad or not names:
        ap.error("Unknown transition: %s" % ",".join(bad or ["(empty)"]))
    args.transitions = names
    if any(b >= li for b, li in zip(args.black, args.limit)):
        ap.error("--black must be less than --limit")
    return args


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.size:
        bpp = args.bpp or 16
        info = FbInfo(args.size[0], args.size[1], bpp, args.size[0] * bpp // 8)
    else:
        try:
            info = read_fb_info(args.fb)
        except OSError as e:
            log(f"Cannot read framebuffer geometry of {args.fb} ({e}); "
                f"pass --size/--bpp. Hint: dmesg | grep -i fb_ili9486")
            return 2
        if args.bpp:
            info = info._replace(bpp=args.bpp, stride=info.width * args.bpp // 8)
    invert = detect_invert() if args.invert == "auto" else args.invert == "on"

    display = Display(args.fb, info, invert, args.gamma, args.limit, args.dither,
                      args.black)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    paused = threading.Event()

    def toggle(*_):
        paused.clear() if paused.is_set() else paused.set()
        log("PAUSED" if paused.is_set() else "RESUMED")

    signal.signal(signal.SIGUSR1, toggle)
    rng = random.Random()
    log(f"Slideshow {args.directory} -> {args.fb} {info.width}x{info.height} "
        f"{info.bpp}bpp invert={invert} hold={args.hold}s")
    try:
        if args.testcard:
            display.show(make_testcard(*display.size, args.testcard))
            while True:
                time.sleep(3600)
        slideshow(
            display,
            playlist(args.directory, args.shuffle, rng, loop=not args.once),
            args.hold, args.duration, args.fill, args.transitions, rng,
            paused=paused.is_set,
        )
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        display.blank()
        display.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
