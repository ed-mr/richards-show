import importlib.util
import os
import pathlib
import random
import sys

import numpy as np
import pytest
from PIL import Image

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("sequence", HERE / "sequence.py")
seq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seq)
fbs, scene140 = seq.fbs, seq.scene140

W, H = 480, 320

EXAMPLE = (HERE / "config.example.yaml").read_text()


# ------------------------------------------------------------ parsing
def test_example_config_is_valid():
    cfg = seq.parse_config(EXAMPLE)
    assert cfg.loop
    assert [s.kind for s in cfg.steps] == ["scene", "slideshow"]
    scene, show = cfg.steps
    assert scene.args.repeat == 3 and scene.args.travel == 8
    assert show.seconds == 600 and show.args.shuffle and show.args.hold == 5
    assert show.args.transitions == ["stack"]


def test_step_options_override_the_command_line_for_that_step_only():
    base = ["--transitions", "stack", "--duration", "3", "--hold", "20"]
    cfg = seq.parse_config("""
sequence:
  - slideshow: {minutes: 1, hold: 5}
  - slideshow: {minutes: 1, transitions: [cut, fade], clock: false}
""", base)
    a, b = cfg.steps
    assert a.args.hold == 5 and a.args.transitions == ["stack"] and a.args.duration == 3
    assert b.args.hold == 20 and b.args.transitions == ["cut", "fade"] and not b.args.clock


def test_to_argv():
    assert seq.to_argv({"hold": 5, "shuffle": True, "fill": False, "clock": False,
                        "transitions": ["a", "b"], "clock_hours": 2}) == [
        "--hold", "5", "--shuffle", "--no-clock", "--transitions", "a,b",
        "--clock-hours", "2"]


def test_defaults_loop_and_plain_step_names():
    cfg = seq.parse_config("sequence:\n  - scene\n  - slideshow\n")
    assert cfg.loop and cfg.steps[0].args.repeat == 1
    assert cfg.steps[1].seconds is None


def test_directory_per_step():
    cfg = seq.parse_config("sequence:\n  - slideshow: {directory: /tmp/x}\n")
    assert cfg.steps[0].args.directory == "/tmp/x"


@pytest.mark.parametrize("text, msg", [
    ("sequence: [", "not valid YAML"),
    ("- slideshow", "expected 'sequence:'"),
    ("sequenz:\n  - slideshow\n", "unknown setting: sequenz"),
    ("sequence: []\n", "at least one step"),
    ("loop: maybe\nsequence:\n  - slideshow\n", "loop: must be true or false"),
    ("sequence:\n  - movie: {}\n", "unknown step 'movie'"),
    ("sequence:\n  - slideshow: {hold: five}\n", "step 1 (slideshow): argument --hold"),
    ("sequence:\n  - slideshow: {transitions: wobble}\n", "Unknown transition: wobble"),
    ("sequence:\n  - slideshow: {minutes: 0}\n", "minutes must be a positive number"),
    ("sequence:\n  - slideshow: {gamma: 2}\n", "'gamma' can't be set per step"),
    ("sequence:\n  - scene: {travel: 0}\n", "step 1 (scene)"),
    ("sequence:\n  - scene: {colour: red}\n", "unrecognized arguments"),
    ("sequence:\n  - slideshow\n  - scene\n", "never ends"),
    ("sequence:\n  - scene: {repeat: 0}\n  - slideshow\n", "never ends"),
    ("sequence:\n  - slideshow: {hold: }\n", "'hold' has no value"),
])
def test_bad_configs_have_readable_errors(text, msg):
    with pytest.raises(seq.ConfigError) as e:
        seq.parse_config(text)
    assert msg in str(e.value)


def test_issue_example_style_is_rejected_clearly():
    # the sketch in issue #14 (make commands) is not a list of steps
    with pytest.raises(seq.ConfigError) as e:
        seq.parse_config('sequenz:\n  make scene SCENE_ARGS="--repeat 3"\n  make start\n')
    assert "unknown setting" in str(e.value)


# ------------------------------------------------------------ config file
def test_config_file_reload_and_keeping_the_last_good_one(tmp_path):
    f = tmp_path / "config.yaml"
    logs = []
    cf = seq.ConfigFile(str(f), [], log=logs.append)
    assert [s.text for s in cf.get().steps] == ["slideshow forever"]  # no file
    f.write_text("sequence:\n  - scene\n  - slideshow: {minutes: 2}\n")
    assert cf.changed()
    assert [s.kind for s in cf.get().steps] == ["scene", "slideshow"]
    assert not cf.changed()
    f.write_text("sequence: [\n")  # broken: keep the previous
    os.utime(f, (1, 1))
    assert [s.kind for s in cf.get().steps] == ["scene", "slideshow"]
    assert "keeping the previous sequence" in logs[-1]
    f.unlink()
    assert [s.text for s in cf.get().steps] == ["slideshow forever"]


# ------------------------------------------------------------ running
class FakeDisplay:
    size = (W, H)

    def __init__(self):
        self.frames = []
        self.overlay = "clock"

    def show(self, frame):
        self.frames.append(("show", self.overlay, frame))

    def show_rows(self, frame, y0, y1):
        self.frames.append(("rows", self.overlay, frame))

    def refresh(self):
        pass


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 0.05
        return self.t

    def sleep(self, s):
        self.t += s


def pictures(tmp_path, n):
    d = tmp_path / "pics"
    d.mkdir()
    for i in range(n):
        Image.new("RGB", (W, H), (20 * i, 0, 0)).save(d / f"{i:02d}.jpg", quality=95)
    return d


class StaticConfig:
    def __init__(self, cfg):
        self.cfg = cfg

    def get(self):
        return self.cfg

    def changed(self):
        return False


def test_run_plays_steps_in_order_and_continues_the_pictures(tmp_path, monkeypatch):
    d = pictures(tmp_path, 6)
    cfg = seq.parse_config(f"""
sequence:
  - slideshow: {{directory: {d}, minutes: 0.05, hold: 1, transitions: cut}}
  - scene: {{repeat: 1, travel: 0.2, pause: 0, explode: 0.2, background: none}}
""")
    shown = []
    real_load = fbs.load_image
    monkeypatch.setattr(fbs, "load_image",
                        lambda p, *a, **k: (shown.append(pathlib.Path(p).name),
                                            real_load(p, *a, **k))[1])
    scenes = []
    real_play = scene140.play
    monkeypatch.setattr(scene140, "play",
                        lambda disp, sc, **k: (scenes.append(disp.overlay),
                                               real_play(disp, sc, **k))[1])
    c = Clock()
    disp = FakeDisplay()
    seq.run(disp, StaticConfig(cfg), "clock", random.Random(0), clock=c,
            sleep=c.sleep, rounds=2)
    assert scenes == [None, None]  # no clock during the scene
    # 3 s per round of slideshow at hold 1: pictures go on where they stopped,
    # the one preloaded at the end of a step is not skipped
    order = list(dict.fromkeys(shown))  # a preloaded picture is loaded again
    assert len(order) >= 4
    assert order == [f"{i:02d}.jpg" for i in range(len(order))]  # none skipped


def test_run_once_config_stops_after_one_round(tmp_path):
    d = pictures(tmp_path, 2)
    cfg = seq.parse_config(f"""
loop: false
sequence:
  - slideshow: {{directory: {d}, minutes: 0.02, hold: 1, transitions: cut}}
""")
    c = Clock()
    seq.run(FakeDisplay(), StaticConfig(cfg), None, random.Random(0), clock=c,
            sleep=c.sleep)  # returns: loop is false
    assert c.t < 10


def test_run_restarts_with_the_new_config_when_the_file_changes(tmp_path):
    d = pictures(tmp_path, 3)
    f = tmp_path / "config.yaml"
    cf = seq.ConfigFile(str(f), [str(d), "--hold", "1", "--transitions", "cut"],
                        log=lambda m: None)
    c = Clock()
    calls = []

    def sleep(s):
        c.sleep(s)
        if len(calls) == 0 and c.t > 3:  # the user saves a config meanwhile
            f.write_text("loop: false\nsequence:\n  - scene: {travel: 0.1, "
                         "pause: 0, explode: 0.1, background: none}\n")
            calls.append(1)

    disp = FakeDisplay()
    seq.run(disp, cf, None, random.Random(0), clock=c, sleep=sleep)
    assert calls  # the plain slideshow ended and the new sequence played once


def test_main_check_validates_the_config(tmp_path, capsys):
    good = tmp_path / "good.yaml"
    good.write_text(EXAMPLE)
    bad = tmp_path / "bad.yaml"
    bad.write_text("sequence:\n  - slideshow: {hold: five}\n")
    assert seq.main(["--config", str(good), "--check"]) == 0
    out = capsys.readouterr().out
    assert "ok: scene x3, slideshow 10 min (loop)" in out
    assert seq.main(["--config", str(bad), "--check"]) == 2
    assert "argument --hold" in capsys.readouterr().err
    assert seq.main(["--config", str(tmp_path / "none.yaml"), "--check"]) == 0


def test_main_end_to_end_once_on_a_fake_framebuffer(tmp_path):
    d = pictures(tmp_path, 2)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("sequence:\n  - scene: {travel: 0.2, pause: 0, explode: 0.2, "
                   "background: none}\n  - slideshow: {hold: 0}\n")
    fb = tmp_path / "fb"
    fb.write_bytes(b"")
    assert seq.main(["--config", str(cfg), str(d), "--fb", str(fb), "--size",
                     f"{W}x{H}", "--invert", "off", "--once", "--no-clock"]) == 0
    assert fb.read_bytes() == b"\x00" * (W * H * 2)


def test_service_runs_the_sequence():
    unit = (HERE / "ci" / "fb-slideshow.service").read_text()
    assert "/fb-slideshow/sequence.py $DEFAULT_ARGS $SHOW_ARGS" in unit
