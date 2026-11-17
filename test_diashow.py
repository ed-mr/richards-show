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
             DIASHOW_DEST=str(dest), OFFLINE=str(tmp_path / "offline"),
             DIASHOW_STICK="")
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


def test_no_stick_mounted_nothing_done(env):
    e, src, dest, tmp = env
    pics(src, ["a.jpg"])
    e["DIASHOW_STICK"] = str(tmp)  # a plain directory, not a mount point
    r = run(e)
    assert r.returncode == 1 and "no stick mounted" in r.stderr
    assert names(dest) == []


# ci/diashow-setup.sh: the Nextcloud address is not in the repo; it comes from
# the environment or the existing remote, and a new password keeps it.
SETUP = pathlib.Path(__file__).with_name("ci") / "diashow-setup.sh"

FAKE_RCLONE_CONFIG = r"""#!/bin/sh
conf="$RCLONE_CONF"
case "$1 $2" in
  "config file") echo "Configuration file is stored at:"; echo "$conf" ;;
  "config show") [ -f "$conf" ] && sed -n '/^\[nc\]/,/^$/p' "$conf" | grep . || exit 1 ;;
  "config delete") [ -f "$conf" ] && sed -i '/^\[nc\]/,/^$/d' "$conf"; true ;;
  "obscure -") printf 'obscured-'; cat; echo ;;
  "lsd nc:") exit 0 ;;
  *) exit 2 ;;
esac
"""


@pytest.fixture
def setup_env(tmp_path):
    fake = tmp_path / "rclone"
    fake.write_text(FAKE_RCLONE_CONFIG)
    fake.chmod(0o755)
    conf = tmp_path / "rclone.conf"
    env = {k: v for k, v in os.environ.items() if not k.startswith("DIASHOW_")}
    env.update(RCLONE=str(fake), RCLONE_CONF=str(conf), SYSTEMCTL="true")
    return env, conf


def setup(env, password="pw1"):
    return subprocess.run(["sh", str(SETUP)], env=env, input=password + "\n",
                          capture_output=True, text=True)


def test_setup_has_no_address_in_the_repo(setup_env):
    env, conf = setup_env
    r = setup(env)
    assert r.returncode == 1 and "set DIASHOW_USER" in r.stderr
    r = setup(dict(env, DIASHOW_USER="show"))
    assert r.returncode == 1 and "set DIASHOW_URL" in r.stderr
    assert not conf.exists()
    assert "DIASHOW_URL:-http" not in SETUP.read_text()  # no built-in address


def test_setup_keeps_address_for_a_new_password(setup_env):
    env, conf = setup_env
    url = "https://cloud.example.org/remote.php/dav/files/show/"
    r = setup(dict(env, DIASHOW_URL=url, DIASHOW_USER="show"))
    assert r.returncode == 0, r.stderr
    assert f"url = {url}" in conf.read_text()
    assert "pass = obscured-pw1" in conf.read_text()
    r = setup(env, "pw2")  # address and user from the existing remote
    assert r.returncode == 0, r.stderr
    text = conf.read_text()
    assert text.count("[nc]") == 1
    assert f"url = {url}" in text and "user = show" in text
    assert "pass = obscured-pw2" in text and "pw1" not in text
    assert oct(conf.stat().st_mode & 0o777) == "0o600"
