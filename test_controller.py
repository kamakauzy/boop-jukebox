import io
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


if __name__ == "__main__":
    unittest.main()
