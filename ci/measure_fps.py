#!/usr/bin/env python3
"""Measure the frame rate of a transition on the real display (run on hydra).

Two numbers per transition:
  rendered: frames the program computes and writes into the framebuffer
  panel:    frames the fbtft driver actually sends to the panel over SPI,
            from the byte counter in /sys/bus/spi/devices/<dev>/statistics

The panel number is what you see. Stop the slideshow first (make measure-fps
does that).

usage: measure_fps.py [--transitions stack,cut] [--seconds 20] [--duration 2]
"""
import argparse
import glob
import importlib.util
import os
import random
import time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "fbs", os.path.join(HERE, "..", "fb-slideshow.py"))
fbs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fbs)


def spi_bytes(dev):
    with open(f"/sys/bus/spi/devices/{dev}/statistics/bytes_tx") as f:
        return int(f.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fb", default="/dev/fb1")
    ap.add_argument("--spi", default="spi0.0")
    ap.add_argument("--dir", default=fbs.DEFAULT_DIR)
    ap.add_argument("--transitions", default="stack")
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--duration", type=float, default=2.0)
    ap.add_argument("--easing", default="ease-in-out", choices=list(fbs.EASINGS))
    args = ap.parse_args()

    info = fbs.read_fb_info(args.fb)
    display = fbs.Display(args.fb, info, fbs.detect_invert())
    frame_bytes = info.width * info.height * info.bpp // 8
    files = fbs.list_images(args.dir)[:2] or sorted(glob.glob(os.path.join(HERE, "..", "test", "*.jpg")))[:2]
    pics = [fbs.load_image(p, display.size) for p in files]
    hz = None
    try:
        with open(f"/sys/bus/spi/devices/{args.spi}/of_node/spi-max-frequency", "rb") as f:
            hz = int.from_bytes(f.read(), "big")
    except OSError:
        pass
    if hz:
        print(f"SPI {hz / 1e6:.0f} MHz: theoretical maximum "
              f"{hz / (frame_bytes * 8):.1f} full frames/s")
    print(f"{info.width}x{info.height} {info.bpp}bpp, {frame_bytes} bytes per full frame, "
          f"duration {args.duration}s, easing {args.easing}")

    class Counting:
        size = display.size
        n = 0

        def show(self, frame):
            self.n += 1
            display.show(frame)

    try:
        display.show(pics[0])
        time.sleep(1)
        before = spi_bytes(args.spi)
        time.sleep(3)
        idle = (spi_bytes(args.spi) - before) / 3
        print(f"idle (static picture): {idle / frame_bytes:5.2f} panel frames/s")
        for name in args.transitions.split(","):
            counting = Counting()
            rng = random.Random(0)
            spent = moving = 0.0
            sent = 0
            i = 0
            while spent < args.seconds:
                a, b = pics[i % 2], pics[(i + 1) % 2]
                fn = fbs.make_transition(name, rng, display.size)
                before, t0 = spi_bytes(args.spi), time.monotonic()
                dur = 0 if name == "cut" else args.duration
                fbs.run_transition(counting, fn, a, b, dur, easing=fbs.EASINGS[args.easing])
                moving += time.monotonic() - t0
                time.sleep(0.3)  # let the driver flush the last frame
                spent += time.monotonic() - t0
                sent += spi_bytes(args.spi) - before
                i += 1
            per = sent / frame_bytes / i
            rate = (f"rendered {counting.n / moving:5.1f} fps, panel {per * i / moving:5.1f} fps"
                    if dur else "(no animation)")
            print(f"{name:8s} {i:3d} transitions: {rate}, "
                  f"{per:4.1f} full panel frames per transition")
    finally:
        display.blank()
        display.close()


if __name__ == "__main__":
    main()
