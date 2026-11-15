import importlib.util
import pathlib

import numpy as np
import pytest
from PIL import Image

spec = importlib.util.spec_from_file_location(
    "scene140", pathlib.Path(__file__).with_name("scene140.py"))
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)
fbs = sc.fbs

W, H = 480, 320


@pytest.fixture(scope="module")
def scene():
    return sc.Scene((W, H), seed=1)


def ink(frame, thresh=40):
    ys, xs = np.nonzero(frame.max(axis=2) > thresh)
    return xs, ys


def test_starts_tiny_at_a_third_of_the_width_at_clock_height(scene):
    frame, rows = scene.frame(0)
    xs, ys = ink(frame)
    px_cm = np.hypot(W, H) / (3.5 * 2.54)
    assert abs(xs.min() - W / 3) <= 2                   # left edge at 1/3
    assert abs((H - ys.max()) - 0.25 * px_cm) <= 3      # clock's baseline
    assert ys.max() - ys.min() + 1 <= 12                # 5 pt: tiny
    assert rows[1] - rows[0] <= 16


def test_starts_white_ends_red_warm_on_the_way(scene):
    assert sc.ramp_color(0) == (255, 255, 255)
    r, g, b = sc.ramp_color(1)
    assert r > 200 and g < 60 and b < 60
    for q in np.linspace(0, 1, 21):
        r, g, b = sc.ramp_color(q)
        assert r >= g >= b  # warm all the way


def test_ends_in_the_middle_half_the_screen_high(scene):
    frame, _ = scene.frame(scene.travel + 0.1)
    xs, ys = ink(frame)
    assert abs((ys.max() - ys.min() + 1) - H / 2) <= 3
    assert abs((xs.min() + xs.max()) / 2 - W / 2) <= 3
    assert abs((ys.min() + ys.max()) / 2 - H / 2) <= 3
    r, g, b = frame[ys, xs].max(axis=0)
    assert r > 200 and g < 60


def test_arc_goes_up_first_then_right(scene):
    pts = [scene.position(q) for q in np.linspace(0, 1, 11)]
    dx0, dy0 = pts[1] - pts[0]
    dx1, dy1 = pts[-1] - pts[-2]
    assert -dy0 > 3 * abs(dx0)   # first mostly upward
    assert dx1 > 3 * abs(dy1)    # finally mostly to the right
    hs = [scene.height(q) for q in np.linspace(0, 1, 11)]
    assert hs == sorted(hs)      # only grows


def test_burst_fills_the_screen_and_fades_out(scene):
    t_burst = scene.travel + scene.pause
    frame, rows = scene.frame(t_burst + 0.5 * scene.explode)
    assert rows is None
    xs, ys = ink(frame, 10)
    # pieces in every ninth of the screen
    cells = {(x * 3 // W, y * 3 // H) for x, y in zip(xs, ys)}
    assert len(cells) == 9
    early = scene.frame(t_burst + 0.1 * scene.explode)[0].astype(int).sum()
    late = scene.frame(t_burst + 0.9 * scene.explode)[0].astype(int).sum()
    assert late < early / 5
    assert scene.frame(scene.total - 1e-6)[0].max() < 8  # all but gone


def test_same_seed_same_burst():
    a = sc.Scene((W, H), seed=7).frame(8.0)[0]
    b = sc.Scene((W, H), seed=7).frame(8.0)[0]
    c = sc.Scene((W, H), seed=8).frame(8.0)[0]
    assert np.array_equal(a, b) and not np.array_equal(a, c)


class Clock:
    def __init__(self, step):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


def test_play_writes_only_the_rows_of_the_small_number(tmp_path):
    info = fbs.FbInfo(W, H, 16, W * 2)
    fb = tmp_path / "fb"
    fb.write_bytes(b"")
    d = fbs.Display(str(fb), info, False)
    writes = []
    real_rows, real_show = d.show_rows, d.show
    d.show_rows = lambda f, y0, y1: (writes.append((y0, y1)), real_rows(f, y0, y1))
    d.show = lambda f: (writes.append((0, H)), real_show(f))
    scene = sc.Scene((W, H), travel=2.0, pause=0.2, explode=1.0, seed=1)
    n = sc.play(d, scene, clock=Clock(0.05), sleep=lambda s: None)
    assert n > 10
    first = writes[2:5]  # after the initial black and the first full frame
    assert all(y1 - y0 <= 24 and y0 % 4 == 0 for y0, y1 in first)
    assert writes[-1] == (0, H)
    d._m.flush()
    assert fb.read_bytes() == b"\x00" * (W * H * 2)  # ends black
    d.close()


def test_preview_gif(tmp_path):
    out = tmp_path / "140.gif"
    assert sc.main(["--preview", str(out), "--fps", "4", "--travel", "1",
                    "--explode", "1", "--seed", "1"]) == 0
    im = Image.open(out)
    assert im.size == (W, H) and im.n_frames > 3


def test_main_on_a_fake_framebuffer(tmp_path):
    fb = tmp_path / "fb"
    fb.write_bytes(b"")
    assert sc.main(["--fb", str(fb), "--size", f"{W}x{H}", "--invert", "off",
                    "--travel", "0.3", "--pause", "0", "--explode", "0.3"]) == 0
    assert fb.read_bytes() == b"\x00" * (W * H * 2)


@pytest.mark.parametrize("bad", [["--travel", "0"], ["--repeat", "-1"],
                                 ["--pause", "-1"], ["--fps", "0"]])
def test_bad_options(bad):
    with pytest.raises(SystemExit):
        sc.parse_args(bad)


def test_repeat_0_loops_until_stopped(tmp_path, monkeypatch):
    fb = tmp_path / "fb"
    fb.write_bytes(b"")
    rounds = []

    def fake_play(display, scene, **kw):
        rounds.append(1)
        if len(rounds) == 5:
            raise KeyboardInterrupt  # Ctrl+C
        return 1

    monkeypatch.setattr(sc, "play", fake_play)
    assert sc.parse_args(["--repeat", "0"]).repeat == 0
    assert sc.main(["--fb", str(fb), "--size", f"{W}x{H}", "--invert", "off",
                    "--repeat", "0"]) == 0
    assert len(rounds) == 5
    assert fb.read_bytes() == b"\x00" * (W * H * 2)  # black after Ctrl+C
