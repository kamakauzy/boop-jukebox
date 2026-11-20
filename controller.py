import argparse
import asyncio
import ctypes
from ctypes import wintypes
import hashlib
import io
import time

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


async def spotify_artwork(manager):
    session = next(
        (s for s in manager.get_sessions() if "spotify" in s.source_app_user_model_id.lower()),
        None,
    )
    if session is None:
        return None
    properties = await session.try_get_media_properties_async()
    if properties.thumbnail is None:
        return None
    stream = await properties.thumbnail.open_read_async()
    try:
        if stream.size == 0 or stream.size > 10 * 1024 * 1024:
            raise ValueError(f"Unexpected artwork size: {stream.size} bytes")
        data = await stream.read_async(Buffer(stream.size), stream.size, InputStreamOptions.NONE)
        return artwork_pixels(bytes(data))
    finally:
        stream.close()


class Device:
    def __init__(self, port):
        self.port = port
        self.pending = bytearray()

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
        if message in KEYS:
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


async def run(port_name, calibrate=False):
    manager = await MediaManager.request_async()
    with serial.Serial(port_name, 115200, timeout=0, write_timeout=3) as port:
        device = Device(port)
        print(f"Listening on {port_name}. Press Ctrl+C to stop.", flush=True)
        await asyncio.sleep(2)
        if calibrate:
            port.write(b"CAL\n")
        last_art = object()
        next_poll = 0
        last_error = None
        was_calibrating = None
        while True:
            message = device.read_message()
            if message:
                device.handle(message)
            if time.monotonic() >= next_poll:
                port.write(b"PING\n")
                is_calibrating = device.wait_for("STATUS_READY", "STATUS_CAL") == "STATUS_CAL"
                if is_calibrating:
                    if was_calibrating is not True:
                        print("Touch each crosshair on the display to calibrate.", flush=True)
                    was_calibrating = True
                    next_poll = time.monotonic() + 1
                    await asyncio.sleep(0.01)
                    continue
                if was_calibrating:
                    last_art = object()
                    print("Touch calibration complete.", flush=True)
                was_calibrating = False
                try:
                    pixels = await spotify_artwork(manager)
                    last_error = None
                except (OSError, RuntimeError, ValueError) as error:
                    if str(error) != last_error:
                        print(f"Could not read Spotify artwork: {error}", flush=True)
                        last_error = str(error)
                    pixels = None
                digest = hashlib.sha256(pixels).digest() if pixels is not None else None
                if digest != last_art:
                    if pixels is None:
                        device.clear_artwork()
                        print("Spotify artwork unavailable", flush=True)
                    else:
                        device.send_artwork(pixels)
                        print("Spotify artwork updated", flush=True)
                    last_art = digest
                next_poll = time.monotonic() + 5
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
