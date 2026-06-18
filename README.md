# rpiclock

An ambient **Matrix digital-rain + clock** display for a Raspberry Pi driving a screen.
Built and tuned for the official **7" DSI touchscreen** (800×480), running headless
(no desktop) — pygame renders straight to the framebuffer via SDL's KMSDRM driver and
auto-starts on boot as a systemd service.

It cycles through 9 ambient scenes (or you can pin it to one):

| Scene | What it is |
|-------|------------|
| `matrix` | Film-accurate Matrix rain (real *Matrix Code NFI* glyphs, depth layers, blur, dot-matrix streams, gradient streams, flashing, falling dust + lone glyphs) |
| `flowfield` | Generative flow-field particles |
| `aquarium` | Virtual fish tank |
| `plasma` | Classic plasma effect |
| `fractal` | Animated Mandelbrot zoom |
| `wordclock` | "IT IS HALF PAST TEN" word clock |
| `flipclock` | Split-flap flip clock |
| `world-iss` | World clocks + live ISS tracker |
| `radar` | Live rain radar (RainViewer) for your location |

## Install

```bash
git clone https://github.com/jeremiahng11/rpiclock.git
cd rpiclock
./install.sh
```

That installs the dependencies (pygame, numpy, fonts), the bundled Matrix font, and a
systemd service (`rpiclock.service`) that starts the display on `tty1` and restarts on boot.

## Controls

- **Tap the screen** → cycle backlight brightness **10% → 30% → 40% → back to 10%**.
- Restart / reboot always returns to **10%**.

## Requirements

- Raspberry Pi (3/4/5) running Raspberry Pi OS / Debian, with the KMS/DRM display driver (default on Pi 4).
- A screen on `tty1`. Brightness control only works on panels that expose
  `/sys/class/backlight/*/brightness` (the official 7" DSI does — `10-0045`).
- Internet (optional) for the weather-radar and ISS scenes; geolocates by IP, falls back to Singapore.

## Customising

- **Different resolution:** edit `W, H` at the top of `display.py`.
- **Pin one scene** (no cycling): `sudo systemctl edit rpiclock.service` and add
  ```
  [Service]
  Environment=SCENE=matrix
  ```
- **Service controls:**
  ```bash
  sudo systemctl restart rpiclock     # restart
  sudo systemctl stop rpiclock        # stop
  journalctl -u rpiclock -f           # or tail ./rpiclock.log
  ```

## Files

- `display.py` — the whole app (all scenes + Matrix engine)
- `fonts/matrix-code-nfi.ttf` — the Matrix Code NFI font, bundled
- `install.sh` — installer
