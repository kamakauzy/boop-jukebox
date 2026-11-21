import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

import controller


class FakePort:
    def __init__(self):
        self.received = bytearray()
        self.writes = []

    @property
    def in_waiting(self):
        return len(self.received)

    def read(self, count):
        data = bytes(self.received[:count])
        del self.received[:count]
        return data

    def write(self, data):
        self.writes.append(data)
        if data == b"ART\n":
            self.received.extend(b"PLAY\r\nREADY\r\n")
        elif data.startswith(b"MIC:"):
            self.received.extend(b"MIC_OK\r\n")
        elif data.startswith(b"TITLE:"):
            self.received.extend(b"TITLE_OK\r\n")
        else:
            self.received.extend(b"ROW\r\n")


class ControllerTests(unittest.TestCase):
    def test_artwork_preserves_aspect_and_encodes_rgb565(self):
        image = Image.new("RGB", (2, 1), (255, 0, 0))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        pixels = controller.artwork_pixels(buffer.getvalue())
        self.assertEqual(len(pixels), 200 * 200 * 2)
        self.assertEqual(pixels[:2], b"\0\0")
        self.assertEqual(pixels[(50 * 200) * 2: (50 * 200) * 2 + 2], b"\xf8\0")

    def test_transfer_acknowledges_each_row_and_handles_button(self):
        port = FakePort()
        device = controller.Device(port)
        with patch.object(controller, "press_keys") as press:
            device.send_artwork(bytes(200 * 200 * 2))
        press.assert_called_once_with(controller.KEYS["PLAY"])
        self.assertEqual(len(port.writes), 201)
        self.assertTrue(all(len(row) == 400 for row in port.writes[1:]))

    def test_bad_image_size_is_rejected(self):
        with self.assertRaises(ValueError):
            controller.Device(FakePort()).send_artwork(b"bad")

    def test_reports_actual_mute_and_unknown_states(self):
        port = FakePort()
        device = controller.Device(port)
        for muted in (True, False, None):
            device.set_mute_state(muted)
        self.assertEqual(port.writes, [b"MIC:1\n", b"MIC:0\n", b"MIC:?\n"])

    def test_monitor_stale_state_is_unknown_not_unmuted(self):
        state = controller.DiscordMuteState()
        with patch.object(controller.time, "monotonic", return_value=10):
            state.update('{"muted": true, "error": null}')
            self.assertEqual(state.current(), (True, None))
        with patch.object(controller.time, "monotonic", return_value=14):
            muted, error = state.current()
            self.assertIsNone(muted)
            self.assertIsNotNone(error)

    def test_monitor_rejects_non_boolean_mute(self):
        with self.assertRaises(ValueError):
            controller.DiscordMuteState().update('{"muted": "false", "error": null}')

    def test_title_serialization_is_bounded_and_single_line(self):
        port = FakePort()
        device = controller.Device(port)
        properties = SimpleNamespace(title="Café\nTrack 🎵" + "x" * 200,
                                     artist="Artist", album_title="Album")
        device.set_title(controller.display_track(properties))
        expected = b"TITLE:Cafe Track ?" + b"x" * (160 - len("Cafe Track ?")) + b"\n"
        expected = expected[:-1] + b" | Artist | Album\n"
        self.assertEqual(port.writes, [expected])

    def test_missing_track_metadata_uses_placeholder_or_omits_fields(self):
        self.assertEqual(controller.display_track(None), "No track playing")
        metadata = SimpleNamespace(title="Song", artist="", album_title=None)
        self.assertEqual(controller.display_track(metadata), "Song")
        metadata.title = " \n "
        self.assertEqual(controller.display_track(metadata), "No track playing")


if __name__ == "__main__":
    unittest.main()
