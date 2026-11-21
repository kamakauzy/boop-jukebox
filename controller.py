import argparse
import asyncio
import ctypes
from ctypes import wintypes
import hashlib
import io
import json
from pathlib import Path
import subprocess
import time
import unicodedata

import news
from PIL import Image, ImageOps
import serial
from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MediaManager,
)
from winrt.windows.storage.streams import Buffer, InputStreamOptions


ART_SIZE = 200
KEYS = {
    "PREV": (0xB1,),
    "PLAY": (0xB3,),
    "NEXT": (0xB0,),
    "MUTE": (0x11, 0x36),
}

class DiscordMuteState:
    def __init__(self):
        self.muted = None
        self.error = "Waiting for Discord mute state"
        self.updated = 0

    def update(self, line):
        data = json.loads(line)
        muted = data["muted"]
        error = data["error"]
        if muted is not None and type(muted) is not bool:
            raise ValueError("Discord monitor sent an invalid mute state")
        if error is not None and not isinstance(error, str):
            raise ValueError("Discord monitor sent an invalid error")
        if (muted is None) != (error is not None):
            raise ValueError("Discord monitor sent an inconsistent state")
        self.muted = muted
        self.error = error
        self.updated = time.monotonic()

    def current(self):
        if time.monotonic() - self.updated > 3:
            return None, "Discord mute monitor is not responding"
        return self.muted, self.error


async def read_discord_state(process, state):
    while line := await process.stdout.readline():
        state.update(line)
    raise RuntimeError("Discord mute monitor stopped unexpectedly")


class KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("vk", wintypes.WORD),
        ("scan", wintypes.WORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra", ctypes.c_size_t),
    ]


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("data", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra", ctypes.c_size_t),
    ]


class InputUnion(ctypes.Union):
    _fields_ = [("keyboard", KeyboardInput), ("mouse", MouseInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("data", InputUnion)]


def press_keys(keys):
    send_input = ctypes.windll.user32.SendInput
    send_input.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
    send_input.restype = wintypes.UINT
    events = [
        Input(1, InputUnion(keyboard=KeyboardInput(key, 0, flags, 0, 0)))
        for key, flags in (
            [(key, 0) for key in keys] + [(key, 2) for key in reversed(keys)]
        )
    ]
    inputs = (Input * len(events))(*events)
    if send_input(len(inputs), inputs, ctypes.sizeof(Input)) != len(inputs):
        error = ctypes.get_last_error()
        releases = (Input * len(keys))(*events[len(keys):])
        send_input(len(releases), releases, ctypes.sizeof(Input))
        raise OSError(error, "Windows did not accept the complete key sequence")


def artwork_pixels(data):
    with Image.open(io.BytesIO(data)) as original:
        image = ImageOps.contain(original.convert("RGB"), (ART_SIZE, ART_SIZE))
    canvas = Image.new("RGB", (ART_SIZE, ART_SIZE))
    canvas.paste(image, ((ART_SIZE - image.width) // 2, (ART_SIZE - image.height) // 2))
    result = bytearray()
    for r, g, b in canvas.getdata():
        value = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        result.extend((value >> 8, value & 0xFF))
    return bytes(result)


def display_text(text, max_length=160):
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_title = "".join(
        char if 32 <= ord(char) <= 126 else " " if char.isspace()
        else "" if unicodedata.combining(char) else "?"
        for char in normalized
    )
    result = " ".join(ascii_title.split())
    return result if max_length is None else result[:max_length]


def display_track(properties):
    if properties is None:
        return "No track playing"
    title = display_text(properties.title) or "No track playing"
    artist = display_text(properties.artist)
    album = display_text(properties.album_title)
    return " | ".join(part for part in (title, artist, album) if part)


async def spotify_properties(manager):
    session = next(
        (s for s in manager.get_sessions() if "spotify" in s.source_app_user_model_id.lower()),
        None,
    )
    return await session.try_get_media_properties_async() if session is not None else None


async def spotify_artwork(properties):
    if properties is None or properties.thumbnail is None:
        return None
    stream = await properties.thumbnail.open_read_async()
    try:
        if stream.size == 0 or stream.size > 10 * 1024 * 1024:
            raise ValueError(f"Unexpected artwork size: {stream.size} bytes")
        data = await stream.read_async(Buffer(stream.size), stream.size, InputStreamOptions.NONE)
        return artwork_pixels(bytes(data))
    finally:
        stream.close()


def story_seconds(story):
    headline = story.split(" | Wikipedia", 1)[0]
    return max(15, len(headline) * 0.12)


class Device:
    def __init__(self, port):
        self.port = port
        self.pending = bytearray()
        self.page = "MEDIA"
        self.skip_story = False

    def read_message(self):
        while self.port.in_waiting:
            char = self.port.read(1)
            if char == b"\n":
                message = self.pending.decode("ascii", errors="replace").strip()
                self.pending.clear()
                return message
            self.pending.extend(char)
            if len(self.pending) > 256:
                raise RuntimeError("Device sent an overlong message")
        return None

    def handle(self, message):
        if message in ("PAGE:NEWS", "PAGE:MEDIA"):
            self.page = message[5:]
            print(f"Display page: {self.page}", flush=True)
        elif message == "NEXT_STORY":
            self.skip_story = True
        elif message in KEYS:
            press_keys(KEYS[message])
            print(f"Sent {message}", flush=True)
        elif message and message != "STARTED":
            print(f"Device: {message}", flush=True)

    def wait_for(self, *expected):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            message = self.read_message()
            if message in expected:
                return message
            if message and message.startswith("ERROR:"):
                raise RuntimeError(message)
            if message:
                self.handle(message)
            time.sleep(0.002)
        raise TimeoutError(f"Device did not acknowledge {' or '.join(expected)}")

    def send_artwork(self, pixels):
        if len(pixels) != ART_SIZE * ART_SIZE * 2:
            raise ValueError("Artwork has the wrong size")
        self.port.write(b"ART\n")
        self.wait_for("READY")
        stride = ART_SIZE * 2
        for start in range(0, len(pixels), stride):
            self.port.write(pixels[start:start + stride])
            self.wait_for("ROW")

    def clear_artwork(self):
        self.port.write(b"CLEAR\n")
        self.wait_for("CLEARED")

    def set_title(self, title):
        self.port.write(f"TITLE:{title}\n".encode("ascii"))
        self.wait_for("TITLE_OK")

    def set_news(self, headline):
        self.port.write(f"NEWS:{headline}\n".encode("ascii"))
        self.wait_for("NEWS_OK")

    def set_story(self, index, count):
        self.port.write(f"STORY:{index}/{count}\n".encode("ascii"))
        self.wait_for("STORY_OK")

    def set_mute_state(self, muted):
        if muted is not None and type(muted) is not bool:
            raise ValueError("Mute state must be a boolean or unknown")
        value = "?" if muted is None else ("1" if muted else "0")
        self.port.write(f"MIC:{value}\n".encode("ascii"))
        self.wait_for("MIC_OK")


async def run(port_name, calibrate=False):
    monitor = await asyncio.create_subprocess_exec(
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(Path(__file__).with_name("discord-state.ps1")),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    state = DiscordMuteState()
    reader = asyncio.create_task(read_discord_state(monitor, state))
    try:
        await run_device(port_name, calibrate, state, reader)
    finally:
        reader.cancel()
        try:
            await reader
        except asyncio.CancelledError:
            pass
        finally:
            if monitor.returncode is None:
                monitor.terminate()
            await monitor.wait()
            stderr = (await monitor.stderr.read()).decode(errors="replace").strip()
            if stderr:
                print(f"Discord mute monitor: {stderr}", flush=True)


async def run_device(port_name, calibrate, state, reader):
    manager = await MediaManager.request_async()
    with serial.Serial(port_name, 115200, timeout=0, write_timeout=3) as port:
        device = Device(port)
        print(f"Listening on {port_name}. Press Ctrl+C to stop.", flush=True)
        await asyncio.sleep(2)
        if calibrate:
            port.write(b"CAL\n")
        last_art = object()
        last_title = object()
        next_poll = 0
        last_error = None
        was_calibrating = None
        last_mute = object()
        last_mute_error = None
        next_mute_poll = 0
        last_page = None
        stories = []
        news_task = None
        news_index = 0
        next_news_refresh = 0
        next_news_story = 0
        news_dirty = True
        while True:
            if reader.done():
                await reader
            message = device.read_message()
            if message:
                device.handle(message)
            if device.page != last_page:
                last_page = device.page
                last_art = object()
                news_dirty = True
                next_poll = 0
            now = time.monotonic()
            if device.page == "NEWS" and news_task is None and now >= next_news_refresh:
                news_task = asyncio.create_task(
                    asyncio.to_thread(news.fetch_news, display_text, artwork_pixels)
                )
                next_news_refresh = now + 900
                if not stories:
                    device.set_news("Loading Wikipedia headlines...")
            if news_task is not None and news_task.done():
                try:
                    stories = news_task.result()
                    news_index = 0
                    news_dirty = True
                    next_news_story = time.monotonic() + story_seconds(stories[0][0])
                    print(f"Loaded {len(stories)} Wikimedia news stories", flush=True)
                except (OSError, RuntimeError, ValueError, KeyError) as error:
                    print(f"Could not load Wikimedia news: {error}", flush=True)
                    if not stories:
                        device.set_news("News unavailable - check connection | Wikipedia")
                news_task = None
            if device.page == "NEWS" and stories:
                if device.skip_story or time.monotonic() >= next_news_story:
                    device.skip_story = False
                    news_index = (news_index + 1) % len(stories)
                    news_dirty = True
                    next_news_story = time.monotonic() + story_seconds(stories[news_index][0])
                if news_dirty:
                    headline, image = stories[news_index]
                    device.set_news(headline)
                    device.set_story(news_index + 1, len(stories))
                    device.send_artwork(image)
                    news_dirty = False
            if time.monotonic() >= next_poll:
                port.write(b"PING\n")
                status = device.wait_for("STATUS_READY", "STATUS_NEWS", "STATUS_CAL")
                is_calibrating = status == "STATUS_CAL"
                if not is_calibrating:
                    device.page = "NEWS" if status == "STATUS_NEWS" else "MEDIA"
                if is_calibrating:
                    if was_calibrating is not True:
                        print("Touch each crosshair on the display to calibrate.", flush=True)
                    was_calibrating = True
                    next_poll = time.monotonic() + 1
                    await asyncio.sleep(0.01)
                    continue
                if was_calibrating:
                    last_art = object()
                    last_title = object()
                    last_mute = object()
                    print("Touch calibration complete.", flush=True)
                was_calibrating = False
                try:
                    properties = await spotify_properties(manager)
                    title = display_track(properties)
                    last_error = None
                except (OSError, RuntimeError, ValueError) as error:
                    if str(error) != last_error:
                        print(f"Could not read Spotify metadata: {error}", flush=True)
                        last_error = str(error)
                    properties = None
                    title = "No track playing"
                if title != last_title:
                    device.set_title(title)
                    last_title = title
                try:
                    pixels = await spotify_artwork(properties)
                except (OSError, RuntimeError, ValueError) as error:
                    print(f"Could not read Spotify artwork: {error}", flush=True)
                    pixels = None
                digest = hashlib.sha256(pixels).digest() if pixels is not None else None
                if device.page == "MEDIA" and digest != last_art:
                    if pixels is None:
                        device.clear_artwork()
                        print("Spotify artwork unavailable", flush=True)
                    else:
                        device.send_artwork(pixels)
                        print("Spotify artwork updated", flush=True)
                    last_art = digest
                next_poll = time.monotonic() + 5
            if not was_calibrating and time.monotonic() >= next_mute_poll:
                muted, error = state.current()
                if error != last_mute_error and error is not None:
                    print(f"Discord mute state unavailable: {error}", flush=True)
                last_mute_error = error
                device.set_mute_state(muted)
                if muted != last_mute:
                    print(f"Discord mute state: {muted}", flush=True)
                    last_mute = muted
                next_mute_poll = time.monotonic() + 0.5
            await asyncio.sleep(0.01)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="USB media buttons and Spotify artwork")
    parser.add_argument("--port", required=True, help="Windows serial port, e.g. COM8")
    parser.add_argument("--calibrate", action="store_true", help="recalibrate the touchscreen")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.port, args.calibrate))
    except KeyboardInterrupt:
        print("\nStopped.")
