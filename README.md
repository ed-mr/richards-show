# fb-slideshow

Small slideshow for the 480x320 SPI display (fbtft, `/dev/fb1`) of a
Raspberry Pi. Shows the `*.jpg` / `*.jpeg` files of a directory in natural
order, with a hard cut or a transition. Writes directly into the framebuffer
(mmap), without X11 and without fbi.

## Requirements

- Raspberry Pi with SPI TFT, framebuffer `/dev/fb1`
- Display overlay matching the panel. Tested: Waveshare 3.5" IPS (B)
  with `dtoverlay=waveshare35b-v2:bgr=1,rotate=270` in `/boot/firmware/config.txt`
- Python 3, `pip install -r requirements.txt` (numpy, Pillow, pytest)
- Access to `/dev/fb1` (group `video`), no `sudo` needed

## Usage

```bash
python3 fb-slideshow.py                       # /mnt/stick/bilder, 10 s per image
python3 fb-slideshow.py ~/pictures --hold 20 --shuffle
```

Geometry, color depth and stride are read from sysfs
(`/sys/class/graphics/fbN/`), inversion from the device tree
(`--invert auto`). Supported: RGB565 (16 bpp) and BGRA (32 bpp).

## Options

| Option | Default | Meaning |
|---|---|---|
| `directory` | `/mnt/stick/bilder` | image directory (env `IMAGE_DIR`, legacy `BILDER_DIR`) |
| `--fb` | `/dev/fb1` | framebuffer device (env `FB_DEVICE`) |
| `--hold S` | 10 | seconds per image (env `HOLD`) |
| `--transitions LIST` | `cut` | comma-separated, see [Transitions](#transitions) (env `TRANSITIONS`) |
| `--duration S` | 2.0 | transition duration (env `DURATION`) |
| `--easing E` | `ease-in-out` | speed curve: `ease-in-out`, `ease-out` or `linear` (env `EASING`) |
| `--seed N` | new each start | random seed for `--shuffle` and transition variations; logged at start so a run can be repeated (env `SEED`) |
| `--shuffle` | off | random order |
| `--fill` | off | image fills the display (cropped) instead of being fitted |
| `--invert auto\|on\|off` | auto | panel inversion |
| `--size WxH`, `--bpp N` | from sysfs | manual override of the framebuffer values |
| `--gamma G` | 1.0 | gamma correction (env `GAMMA`) |
| `--limit N[,N,N]` | 255 | upper brightness limit per channel (env `LIMIT`) |
| `--black R,G,B` | 0 | black point per channel (env `BLACK`) |
| `--dither` | off | Bayer dithering during RGB565 conversion |
| `--testcard [bars\|ramps]` | – | show a test card instead of the slideshow |
| `--once` | off | only one round, then exit |
| `--check` | – | only check the options, then exit (display untouched) |

`--gamma`, `--limit`, `--black` and `--dither` are corrections for
panels with a wrong initialization. With the matching overlay they are
not needed.

Unreadable images are skipped. If the directory is empty, the program
waits and checks again.

## Transitions

| Name | Effect |
|---|---|
| `cut` | hard cut, the next picture replaces the current one at once (default) |
| `stack` | the pictures lie on a stack: the current one slides off to the left, right, top or bottom, with a slight sideways drift and a soft shadow; the next one already lies still underneath |
| `fade`, `wipe`, `push`, `blinds`, `dissolve`, `iris`, `zoom`, `split` | older effects, available but not part of the default |

With several names, one is picked at random for each picture, never the same
one twice in a row. The next picture is loaded and scaled while the current
one is shown, so a transition never waits for it. Transitions are time-based:
if frames are slow, frames are dropped and the duration stays the same.

### Configuring

| Setting | Option | Env | Default |
|---|---|---|---|
| which transition(s) | `--transitions cut\|stack\|...` | `TRANSITIONS` | `cut` |
| duration in seconds | `--duration S` | `DURATION` | 2.0 |
| speed curve | `--easing ease-in-out\|ease-out\|linear` | `EASING` | `ease-in-out` |
| random seed (direction and drift of each `stack`) | `--seed N` | `SEED` | new each start, logged |

Examples:

```bash
# directly
python3 fb-slideshow.py --transitions stack --duration 3 --easing ease-out

# on hydra, from itaka (kept across reboots and deploys, restarts the show)
make config SHOW_ARGS="--duration 4"    # on top of the service defaults
make config SHOW_ARGS=                  # back to the service defaults

# from GitHub
gh workflow run show.yml -f action=config -f show_args="--transitions stack"
```

On hydra the service always starts the show with
`--transitions stack --duration 3 --easing ease-out` (`DEFAULT_ARGS` in
`ci/fb-slideshow.service`, changed by editing that file and `make deploy`).
Options saved with `make config` come after them, so they add to these
defaults and win where they set the same option (e.g. `--transitions cut`).
The program's own defaults in the table above apply when it is started by hand.

To repeat a run exactly, take the seed from the start line in the log
(`make status`) and pass it with `--seed`.

Fixed in the code (not options): the exit directions of `stack` (left,
right, up, down), its maximum sideways drift (8 % of the display, constant
`STACK_MAX_DRIFT`), and the shadow (4 % of the display wide, 35 % darkening
at the edge, in `make_stack`).

Frame rate on hydra (Raspberry Pi 3 B, 480x320 RGB565 over SPI at 15 MHz,
measured with `make measure-fps`): a full frame takes ~164 ms on the SPI bus,
so the panel can show at most **6.1 frames/s**. A 2 s `stack` transition
reaches **~4.9 frames/s** on the panel (about 10 full frames). The CPU is not
the limit (the program renders ~8 frames/s); only a higher SPI clock would
make transitions smoother.

## Pause and exit

Pause and resume (toggles on every signal, the current image stays):

```bash
pkill -USR1 -f fb-slideshow.py
# or targeted:
kill -USR1 <PID>
```

Exit: `SIGTERM` (`pkill -f fb-slideshow.py`) or Ctrl+C. The display is
set to black.

When the show runs as a service (see below), prefer `make pause` /
`make resume` / `make stop` from itaka, or `systemctl --user stop fb-slideshow`
on hydra.

## Tests

```bash
python3 -m pytest -q
```

## Deployment and CI

The show runs on hydra as a systemd **user** service (`fb-slideshow.service`)
and starts at boot (linger is enabled for the user, no `sudo` needed). It is
restarted automatically if it fails, e.g. while `/dev/fb1` is not ready yet
after boot. Logs go to the journal, which is kept in RAM on hydra.

Everything is driven from itaka with `make` (ssh/scp to hydra):

hydra is reached at the first address that answers over ssh: the WireGuard
VPN address `192.168.4.203` (works from any network), then the name `hydra`
(`/etc/hosts`, home LAN), then the Wi-Fi address `192.168.4.142`
(`HYDRA_CANDIDATES` in the Makefile). `make hydra-host` shows which one is
used; `make deploy HYDRA=<address>` forces one.

| Command | What it does |
|---|---|
| `make test` | tests on itaka, with hydra's Python / numpy / Pillow versions (`requirements-ci.txt`) |
| `make deploy` | copy to `hydra:~/fb-slideshow` and install/update the service |
| `make test-hydra` | tests on hydra, version check, verify the service file |
| `make start` / `stop` / `restart` | control the service |
| `make pause` / `resume` | pause or resume (`SIGUSR1`), the image stays |
| `make status` | state, boot setting, options, recent log |
| `make config SHOW_ARGS="--shuffle --hold 20"` | set the show's options on top of the service defaults, replacing the previously saved ones (kept across reboots and deploys; `SHOW_ARGS=` goes back to the service defaults). Invalid options are rejected and nothing changes |
| `make measure-fps` | frame rate of transitions on the real panel (stops the show meanwhile) |
| `make ci` | test + deploy + test-hydra + restart |
| `make hydra-host` | which address of hydra is used |

GitHub Actions runs the same steps on a self-hosted runner on itaka: the
tests on every push and pull request, and on pushes to `main` also deploy
and restart. The show can also be controlled from GitHub:

```bash
gh workflow run show.yml -f action=pause
gh workflow run show.yml -f action=config -f show_args="--shuffle"
```

On hydra itself: `systemctl --user status fb-slideshow`,
`journalctl --user-unit fb-slideshow`.

The service file in the repo (`ci/fb-slideshow.service`) is installed
unchanged; `make deploy` copies it and reloads systemd when it changed. By
hand on hydra:

```bash
cp ~/fb-slideshow/ci/fb-slideshow.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user restart fb-slideshow.service
```
