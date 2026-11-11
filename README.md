# fb-slideshow

Small slideshow for the 480x320 SPI display (fbtft, `/dev/fb1`) of a
Raspberry Pi. Shows the `*.jpg` / `*.jpeg` files of a directory in natural
order with changing transitions. Writes directly into the framebuffer
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
| `--duration S` | 1.2 | transition duration (env `DURATION`) |
| `--shuffle` | off | random order |
| `--fill` | off | image fills the display (cropped) instead of being fitted |
| `--transitions LIST` | all | comma-separated from `fade,wipe,push,blinds,dissolve,iris,zoom,split` |
| `--invert auto\|on\|off` | auto | panel inversion |
| `--size WxH`, `--bpp N` | from sysfs | manual override of the framebuffer values |
| `--gamma G` | 1.0 | gamma correction (env `GAMMA`) |
| `--limit N[,N,N]` | 255 | upper brightness limit per channel (env `LIMIT`) |
| `--black R,G,B` | 0 | black point per channel (env `BLACK`) |
| `--dither` | off | Bayer dithering during RGB565 conversion |
| `--testcard [bars\|ramps]` | – | show a test card instead of the slideshow |
| `--once` | off | only one round, then exit |

`--gamma`, `--limit`, `--black` and `--dither` are corrections for
panels with a wrong initialization. With the matching overlay they are
not needed.

Unreadable images are skipped. If the directory is empty, the program
waits and checks again.

## Pause and exit

Pause and resume (toggles on every signal, the current image stays):

```bash
pkill -USR1 -f fb-slideshow.py
# or targeted:
kill -USR1 <PID>
```

Exit: `SIGTERM` (`pkill -f fb-slideshow.py`) or Ctrl+C. The display is
set to black.

## Tests

```bash
python3 -m pytest -q
```
