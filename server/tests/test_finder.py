import socket
import time
import unittest
from unittest import mock

from brain import finder


class AnswerTests(unittest.TestCase):
    def test_answers_the_robots_question_with_the_port(self):
        self.assertEqual(finder.answer(b"rockybrain?", 8765), b"rockybrain! 8765")

    def test_ignores_anything_else(self):
        for packet in (b"", b"hello", b"rockybrain! 8765", b"rockybrain?x"):
            self.assertIsNone(finder.answer(packet, 8765))


class WifiMessageTests(unittest.TestCase):
    def test_scan(self):
        self.assertEqual(finder.wifi_message({"op": "scan"}), {"type": "wifi_scan"})

    def test_add_use_forget(self):
        self.assertEqual(finder.wifi_message({"op": "add", "ssid": "Home", "password": "12345678"}),
                         {"type": "wifi_add", "ssid": "Home", "password": "12345678"})
        self.assertEqual(finder.wifi_message({"op": "use", "ssid": "Home"}), {"type": "wifi_use", "ssid": "Home"})
        self.assertEqual(finder.wifi_message({"op": "forget", "ssid": "Home"}), {"type": "wifi_forget", "ssid": "Home"})

    def test_open_network_has_no_password(self):
        self.assertEqual(finder.wifi_message({"op": "add", "ssid": "Cafe"})["password"], "")

    def test_refuses_bad_requests(self):
        for bad in ({"op": "add"}, {"op": "add", "ssid": ""}, {"op": "add", "ssid": "x" * 33},
                    {"op": "add", "ssid": "Home", "password": "short"},
                    {"op": "add", "ssid": "Home", "password": "x" * 64},
                    {"op": "add", "ssid": 5}, {"op": "reboot", "ssid": "Home"}, {}):
            with self.assertRaises(ValueError, msg=bad):
                finder.wifi_message(bad)

    def test_a_refusal_does_not_repeat_the_password(self):
        with self.assertRaises(ValueError) as e:
            finder.wifi_message({"op": "add", "ssid": "Home", "password": "secret"})
        self.assertNotIn("secret", str(e.exception))


class ServeTests(unittest.TestCase):
    def test_answers_over_udp(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            free = probe.getsockname()[1]  # not finder.PORT: a running brain has that one
        with mock.patch.object(finder, "PORT", free):
            finder.serve(1234)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            for _ in range(10):  # the thread may still be starting
                s.sendto(finder.ASK, ("127.0.0.1", free))
                try:
                    self.assertEqual(s.recv(64), b"rockybrain! 1234")
                    return
                except TimeoutError:
                    time.sleep(0.05)
            self.fail("no answer")


if __name__ == "__main__":
    unittest.main()
