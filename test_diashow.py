"""ci/diashow-sync.sh (issue #25) with a stand-in for rclone that "fetches"
from a local directory."""
import os
import pathlib
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).with_name("ci") / "diashow-sync.sh"

FAKE_RCLONE = r"""#!/bin/sh
# stand-in: lsf <src> -> files of the source dir; sync <src> <dest> -> mirror
cmd=$1; shift
src=$1
[ -f "$OFFLINE" ] && { echo "offline" >&2; exit 1; }
case "$cmd" in
  lsf) ls "$src" | grep -i -E '\.jpe?g$' ;;
  sync) dest=$2
        for f in "$dest"/*; do
          b=$(basename "$f")
          case "$b" in *.jpg|*.jpeg|*.JPG|*.JPEG) [ -e "$src/$b" ] || rm "$f" ;; esac
        done
        for f in "$src"/*; do
          case "$f" in *.jpg|*.jpeg|*.JPG|*.JPEG) cp "$f" "$dest/" ;; esac
        done ;;
esac
"""


@pytest.fixture
def env(tmp_path):
    fake = tmp_path / "rclone"
    fake.write_text(FAKE_RCLONE)
    fake.chmod(0o755)
    src, dest = tmp_path / "cloud", tmp_path / "stick"
    src.mkdir()
    dest.mkdir()
    e = dict(os.environ, RCLONE=str(fake), DIASHOW_SRC=str(src),
             DIASHOW_DEST=str(dest), OFFLINE=str(tmp_path / "offline"))
    return e, src, dest, tmp_path


def run(e):
    return subprocess.run(["sh", str(SCRIPT)], env=e, capture_output=True, text=True)


def pics(d, names):
    for n in names:
        (d / n).write_bytes(b"jpg")


def names(d):
    return sorted(p.name for p in d.iterdir())


def test_new_and_deleted_pictures(env):
    e, src, dest, _ = env
    pics(src, ["a.jpg", "b.JPG", "c.jpeg"])
    pics(dest, ["a.jpg", "old.jpg", "notes.txt"])
    r = run(e)
    assert r.returncode == 0, r.stderr
    assert names(dest) == ["a.jpg", "b.JPG", "c.jpeg", "notes.txt"]
    assert "sync ok: 3 pictures (2 new, 1 deleted)" in r.stdout


def test_guard_aborts_before_changing_anything(env):
    e, src, dest, _ = env
    pics(src, ["new.jpg"])
    pics(dest, [f"{i:02d}.jpg" for i in range(21)])
    before = names(dest)
    r = run(e)
    assert r.returncode == 1
    assert "would delete 21 of 21 pictures" in r.stderr and "unchanged" in r.stderr
    assert names(dest) == before  # not even the new one fetched


def test_twenty_deletions_are_still_allowed(env):
    e, src, dest, _ = env
    pics(dest, [f"{i:02d}.jpg" for i in range(20)])
    pics(src, ["keep.jpg"])
    assert run(e).returncode == 0
    assert names(dest) == ["keep.jpg"]


def test_offline_leaves_the_stick_unchanged(env):
    e, src, dest, tmp = env
    pics(src, ["a.jpg"])
    pics(dest, ["b.jpg"])
    (tmp / "offline").write_text("")
    r = run(e)
    assert r.returncode == 1 and "stick unchanged" in r.stderr
    assert names(dest) == ["b.jpg"]


def test_units():
    ci = SCRIPT.parent
    service = (ci / "diashow-sync.service").read_text()
    timer = (ci / "diashow-sync.timer").read_text()
    assert "ConditionPathIsMountPoint=/mnt/stick" in service
    assert "ExecStart=%h/fb-slideshow/ci/diashow-sync.sh" in service
    assert "OnBootSec=2min" in timer and "OnUnitActiveSec=1h" in timer
    assert "--max-delete" in SCRIPT.read_text()
