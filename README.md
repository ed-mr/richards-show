# fb-slideshow

Kleine Bilderschau für das 480x320-SPI-Display (fbtft, `/dev/fb1`) eines
Raspberry Pi. Zeigt `*.jpg` / `*.jpeg` eines Verzeichnisses in natürlicher
Reihenfolge mit wechselnden Übergängen. Schreibt direkt in den Framebuffer
(mmap), ohne X11 und ohne fbi.

## Voraussetzungen

- Raspberry Pi mit SPI-TFT, Framebuffer `/dev/fb1`
- Display-Overlay passend zum Panel. Getestet: Waveshare 3,5" IPS (B)
  mit `dtoverlay=waveshare35b-v2:bgr=1,rotate=270` in `/boot/firmware/config.txt`
- Python 3, `pip install -r requirements.txt` (numpy, Pillow, pytest)
- Zugriff auf `/dev/fb1` (Gruppe `video`), kein `sudo` nötig

## Start

```bash
python3 fb-slideshow.py                       # /mnt/stick/bilder, 10 s pro Bild
python3 fb-slideshow.py ~/bilder --hold 20 --shuffle
```

Geometrie, Farbtiefe und Stride werden aus sysfs gelesen
(`/sys/class/graphics/fbN/`), die Invertierung aus dem Device Tree
(`--invert auto`). Unterstützt: RGB565 (16 bpp) und BGRA (32 bpp).

## Optionen

| Option | Standard | Bedeutung |
|---|---|---|
| `directory` | `/mnt/stick/bilder` | Bildverzeichnis (Env `BILDER_DIR`) |
| `--fb` | `/dev/fb1` | Framebuffer-Gerät (Env `FB_DEVICE`) |
| `--hold S` | 10 | Sekunden pro Bild (Env `HOLD`) |
| `--duration S` | 1.2 | Dauer des Übergangs (Env `DURATION`) |
| `--shuffle` | aus | zufällige Reihenfolge |
| `--fill` | aus | Bild füllt das Display (beschnitten) statt eingepasst |
| `--transitions LISTE` | alle | kommagetrennt aus `fade,wipe,push,blinds,dissolve,iris,zoom,split` |
| `--invert auto\|on\|off` | auto | Invertierung des Panels |
| `--size BxH`, `--bpp N` | aus sysfs | manuelle Überschreibung der Framebuffer-Werte |
| `--gamma G` | 1.0 | Gamma-Korrektur (Env `GAMMA`) |
| `--limit N[,N,N]` | 255 | Obergrenze der Helligkeit pro Kanal (Env `LIMIT`) |
| `--black R,G,B` | 0 | Schwarzpunkt pro Kanal (Env `BLACK`) |
| `--dither` | aus | Bayer-Dithering bei der RGB565-Wandlung |
| `--testcard [bars\|ramps]` | – | statt der Show eine Testkarte anzeigen |
| `--once` | aus | nur eine Runde, dann Ende |

`--gamma`, `--limit`, `--black` und `--dither` sind Korrekturen für
Panels mit falscher Initialisierung. Mit dem passenden Overlay werden sie
nicht gebraucht.

Nicht lesbare Bilder werden übersprungen. Ist das Verzeichnis leer, wartet
das Programm und prüft erneut.

## Pause und Beenden

Pausieren und Fortsetzen (schaltet bei jedem Signal um, das Bild bleibt stehen):

```bash
pkill -USR1 -f fb-slideshow.py
# oder gezielt:
kill -USR1 <PID>
```

Beenden: `SIGTERM` (`pkill -f fb-slideshow.py`) oder Strg+C. Das Display
wird dabei schwarz gesetzt.

## Tests

```bash
python3 -m pytest -q
```
