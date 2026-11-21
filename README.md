# Boop Jukebox

A tiny touchscreen with big button energy: boop your music, hush your mic,
and admire the album art. A USB-connected Spotify and Discord control panel
for Windows and the ESP32 Cheap Yellow Display.

## Hardware and software

- ESP32-2432S028R / compatible ILI9341 + XPT2046 touchscreen board
- USB data cable (power and serial communication)
- Windows 10 version 1809 or newer, or Windows 11
- Python 3.13 and PlatformIO
- Spotify and Discord desktop apps

This prototype targets the common ESP32-2432S028R "Cheap Yellow Display":
an ESP32 with a 320x240 ILI9341 display and XPT2046 resistive touch.
The pictured HW-458 appears to be a related board, but its exact display,
touch controller, pinout and calibration have not been verified.

The ESP32 draws the buttons and sends button presses to Windows over USB
serial. The Python companion sends media keys or a Discord hotkey and shows
the current Spotify album artwork beside the controls, when Windows provides
it. This is a still image, not video. The microphone button is red with a
slash when Discord reports self-mute, purple when unmuted, and gray with a
question mark when the state is unavailable. Media keys control whichever Windows media session receives them, not
necessarily Spotify.

The interface has framed 200x200 artwork, raised transport and microphone
buttons, and a scrolling song, artist, and album banner. Spotify supplies metadata
through Windows media controls; missing titles show "No track playing", and
missing artist or album fields are omitted. The display font
supports ASCII: accented Latin letters are simplified and other characters
appear as `?`. The transport icon does not indicate playback state. Firmware and
the Python companion must be updated together for artwork and title transfer.

## News screen

Swipe horizontally across the touchscreen to switch between music and news.
The news page rotates Wikipedia's curated "In the news" headlines, with a
related Wikimedia image, a large scrolling headline, and text and image
attribution accessible by tapping the image (tap again to return). Stories
rotate every 15+ seconds; tap the headline to skip ahead, and the dots in the
bottom bar show which story is displayed. It refreshes
every 15 minutes while open; each story uses the first linked article with a
usable public-domain or Creative Commons image, and stories with none are
skipped, so the number of headlines varies.
The companion fetches news and images over HTTPS; no API key is required.
If the feed is unavailable, the page reports it rather than inventing a story.
Swipe again to return to the music controls. The 200x200 image is resized to
fit without cropping; image license and source links appear on the credits view.

## Setup

1. Install PlatformIO and connect the board with a USB data cable.
2. Build and upload with `pio run -t upload`. Flashing replaces the board's
   existing firmware; save it first if you need to keep it.
3. In the Discord **desktop app**, open User Settings > Keybinds, add **Toggle
   Mute**, and record **Ctrl+6** (the number-row 6). Choose a different shortcut in both
   Discord and `controller.py` if that combination conflicts with another app.
4. Install Python and the companion's dependencies once:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

5. Find the board's COM port in Windows Device Manager or `pio device list`.
   Close any serial monitor or older `controller.ps1`, then run:

   ```powershell
   .\.venv\Scripts\python.exe .\controller.py --port COM8
   ```

   Replace `COM8` if necessary. Keep the window open; Ctrl+C stops it. The
   board cannot control the PC or update artwork unless the script is running.
6. On first startup, tap the center of each crosshair in turn to calibrate
   touch. The board saves calibration across restarts. Open Spotify and Discord,
   then test all four buttons. Discord must be
   running and the hotkey configured. Verify mute status in Discord itself.

The board normally exposes a USB-to-serial connection, not a PC-driven display
or a USB keyboard. No extra ESP32 or Raspberry Pi is needed. The serial port
may reset the ESP32 when opened.

## Actual Discord mute state

The companion reads Discord's microphone toggle using Windows UI Automation
through `discord-state.ps1`. It does not count button presses or read Discord
tokens. Changes from the touchscreen, keyboard, or Discord itself are reflected
while the control is accessible. This reads **self-mute**, not server mute or
whether audio is reaching a voice channel.

Discord must use an English UI and be launched with
`--force-renderer-accessibility`. Quit Discord completely before launching
its installed `Discord.exe` with this argument (or adding it to a shortcut
that directly launches that executable). Repeat this after a normal Discord
restart that omits the argument. The companion does not restart Discord for you.

If Discord is closed, its microphone control is inaccessible (including on
some minimized screens), or the monitor stops responding, the indicator becomes
unknown rather than claiming the microphone is unmuted. The console reports
the reason. A disconnected device also drops a stale known state after 15 seconds.
Artwork transfers can briefly delay state updates.

## Development

```powershell
pio run
.\.venv\Scripts\python.exe -m unittest -v test_controller
```

The older `controller.ps1` is a media-key-only companion; use `controller.py`
for album artwork, touch calibration, and actual mute-state support.

## Troubleshooting

- If the display is blank or touch does not work, check the exact board model,
  display and touch controllers and pinout. This firmware assumes the resistive
  XPT2046 version; a capacitive version needs different touch code.
- If touches land on the wrong buttons, stop the companion and restart it
  with `--calibrate`, then tap each crosshair as accurately as possible.
- If touches do not highlight a button, check the touch controller and calibration.
  If they do highlight a button but do not control the PC, check that the Python
  companion is running and reports the button press.
- If artwork is missing, start playback in the Spotify desktop app; artwork
  depends on what the Windows media session makes available.
- If Windows cannot open the port, check the USB data cable and USB-to-serial
  driver, and close other programs using the port.
- If the Discord button does nothing, check its global keybind and that the
  Discord desktop app is running. Test Ctrl+6 on your physical keyboard and
  leave the Keybinds settings page before testing. Windows may block injected keys across
  applications running at different privilege levels.
