import importlib.util
import pathlib
import shutil

import numpy as np
import pytest
from PIL import Image

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("movie", HERE / "movie.py")
movie = importlib.util.module_from_spec(spec)
spec.loader.exec_module(movie)


def stick(tmp_path, n=3):
    pics = tmp_path / "bilder"
    pics.mkdir()
    for i in range(n):
        Image.new("RGB", (480, 320), (60 * i, 0, 0)).save(pics / f"{i}.jpg")
    return tmp_path


def test_default_args_from_the_service_file():
    assert movie.default_args() == ["--transitions", "stack", "--duration", "3",
                                    "--easing", "ease-out"]


def test_renders_the_sequence_with_its_durations(tmp_path):
    root = stick(tmp_path)
    frames = []
    n, seconds = movie.render("""
sequence:
  - scene: {travel: 1, pause: 0, explode: 1, background: /mnt/stick/none.jpg}
  - slideshow: {minutes: 0.1, hold: 2, transitions: cut}
""", frames.append, movie.default_args(), stick=root, fps=10, seed=1,
        log=lambda m: None)
    # scene 2 s + slideshow 6 s (+ the last picture's hold) + 1 s at the end
    assert 9 <= seconds <= 12
    assert n == len(frames) == pytest.approx(seconds * 10, abs=1)
    assert all(f.shape == (320, 480, 3) for f in frames)
    reds = {int(f[160, 240, 0]) for f in frames[30:]}
    assert len(reds) >= 2  # several pictures of the slideshow


def test_clock_in_the_slideshow_not_in_the_scene(tmp_path):
    root = stick(tmp_path)
    frames = []
    movie.render("""
sequence:
  - scene: {travel: 1, pause: 0, explode: 1, background: none}
  - slideshow: {minutes: 0.05, hold: 1, transitions: cut}
""", frames.append, stick=root, fps=10, seed=1, log=lambda m: None)
    corner = [f[300:315, 15:80].max() for f in frames]
    assert max(corner[:10]) < 200      # scene: no clock
    assert corner[-5] > 200            # slideshow: the white clock


def test_paths_on_the_stick_are_local(tmp_path):
    cfg = movie.seq.parse_config("""
sequence:
  - scene: {}
  - slideshow: {directory: /mnt/stick/other, minutes: 1}
  - slideshow: {directory: /home/x/pics}
""")
    cfg = movie.local_paths(cfg, tmp_path)
    assert cfg.steps[0].args.background == str(tmp_path / "background/scene-background.jpg")
    assert cfg.steps[1].args.directory == str(tmp_path / "other")
    assert cfg.steps[2].args.directory == "/home/x/pics"


def test_endless_steps_get_an_end():
    cfg = movie.seq.parse_config("""
sequence:
  - scene: {repeat: 2}
  - slideshow: {}
""")
    logs = []
    cfg = movie.finite(cfg, 30, logs.append)
    assert cfg.steps[1].seconds == 30 and len(logs) == 1
    cfg = movie.finite(movie.seq.parse_config("sequence: [{scene: {repeat: 0}}]"),
                       30, logs.append)
    assert cfg.steps[0].args.repeat == 1


def test_hydra_speed_or_smooth():
    clock = movie.SimClock(25, lambda: None)
    d = movie.MovieDisplay((480, 320), clock)
    frame = np.zeros((320, 480, 3), np.uint8)
    d.show(frame)
    assert clock.t == pytest.approx(movie.FULL_FRAME_S)
    d.show_rows(frame, 0, 16)
    assert clock.t - movie.FULL_FRAME_S < 0.1  # a small update is quicker
    smooth = movie.MovieDisplay((480, 320), movie.SimClock(25, lambda: None),
                                smooth=True)
    smooth.show(frame)
    assert smooth.clock.t == pytest.approx(1 / 25)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_main_writes_an_mp4(tmp_path):
    root = stick(tmp_path)
    cfg = tmp_path / "c.yaml"
    cfg.write_text("sequence:\n  - slideshow: {minutes: 0.05, hold: 1}\n")
    assert movie.main([str(cfg), "--stick", str(root), "--fps", "5"]) == 0
    out = tmp_path / "c.mp4"
    assert out.exists() and out.stat().st_size > 1000


def test_main_reports_a_broken_config(tmp_path, capsys):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("sequence:\n  - scene: {starting: -6}\n")
    assert movie.main([str(cfg), "-o", str(tmp_path / "x.mp4")]) == 2
    assert "--starting" in capsys.readouterr().err
