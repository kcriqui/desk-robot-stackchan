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


class JoinFailureTests(unittest.TestCase):
    def test_says_why(self):
        self.assertIn("password is wrong", finder.join_failure("Home", 15))
        self.assertIn("2.4 GHz", finder.join_failure("Home", 201))
        self.assertIn("wouldn't let it", finder.join_failure("Home", 205))
        self.assertIn("no reason given", finder.join_failure("Home", 0))

    def test_no_saved_password_comes_first(self):
        self.assertIn("no password saved", finder.join_failure("Home", 210, False))


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


class SavedPasswordTests(unittest.TestCase):
    def setUp(self):
        finder._saved = None

    def tool(self, platform, outputs):
        """finder with this platform, and _run answering by the first matching words of the command."""
        def run(args):
            line = " ".join(args)
            return next((out for words, out in outputs.items() if words in line), "")
        return mock.patch.object(finder.sys, "platform", platform), mock.patch.object(finder, "_run", run)

    def test_windows(self):
        a, b = self.tool("win32", {
            "profiles": "    All User Profile     : Home\n    All User Profile     : Corner Cafe\n",
            "name=Home": "    Security key           : Present\n    Key Content            : hunter2hunter2\n",
            "name=Cafe": "    Security key           : Absent\n",
        })
        with a, b:
            self.assertEqual(finder.saved_networks(), {"Home", "Corner Cafe"})
            self.assertEqual(finder.saved_password("Home"), "hunter2hunter2")
            self.assertEqual(finder.saved_password("Cafe"), "")
            self.assertIsNone(finder.saved_password("Nowhere"))

    def test_linux(self):
        a, b = self.tool("linux", {
            "NAME,TYPE": "Home:802-11-wireless\nWired connection 1:802-3-ethernet\nHome 1:802-11-wireless\n",
            "wireless.ssid connection show id Home 1": "Phone\n",
            "wireless.ssid connection show id Home": "Home\n",
            "psk connection show id Home 1": "",
            "key-mgmt connection show id Home 1": "wpa-psk\n",
            "psk connection show id Home": "hunter2hunter2\n",
        })
        with a, b:
            self.assertEqual(finder.saved_networks(), {"Home", "Phone"})
            self.assertEqual(finder.saved_password("Home"), "hunter2hunter2")
            self.assertIsNone(finder.saved_password("Phone"))  # secured, but it wouldn't say
            self.assertIsNone(finder.saved_password("Nowhere"))

    def test_saved_message_uses_the_computers_password(self):
        with mock.patch.object(finder, "saved_password", return_value="hunter2hunter2"):
            self.assertEqual(finder.saved_message("Home"),
                             {"type": "wifi_add", "ssid": "Home", "password": "hunter2hunter2"})

    def test_refusals_never_repeat_the_password(self):
        with mock.patch.object(finder, "saved_password", return_value=None):
            with self.assertRaises(ValueError):
                finder.saved_message("Home")
        with mock.patch.object(finder, "saved_password", return_value="tooshort"[:5]):
            with self.assertRaises(ValueError) as e:
                finder.saved_message("Home")
            self.assertNotIn("tooshort"[:5], str(e.exception))

    def test_names_are_cached(self):
        with mock.patch.object(finder, "_read_saved_networks", return_value={"Home"}) as read:
            finder.saved_networks()
            finder.saved_networks()
        self.assertEqual(read.call_count, 1)


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
