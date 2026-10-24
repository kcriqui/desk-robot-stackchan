"""The Log tab's ring of recent output (brain/logbook.py)."""

import io
import unittest
from unittest.mock import patch

from brain import logbook


class LogbookTests(unittest.TestCase):
    def setUp(self):
        self.ring = patch.object(logbook, "_lines", logbook.collections.deque(maxlen=5))
        self.ring.start()
        self.seq = patch.object(logbook, "_seq", 0)
        self.seq.start()

    def tearDown(self):
        self.ring.stop()
        self.seq.stop()

    def test_whole_lines_are_kept_and_passed_on(self):
        out = io.StringIO()
        tee = logbook._Tee(out)
        print("robot connected!", file=tee)
        tee.write("GLaDOS [neutral]: What ")
        self.assertEqual(logbook.since(0), (1, ["robot connected!"]))   # half a line waits for the rest
        tee.write("is it now?\nKevin: hi\n")
        self.assertEqual(logbook.since(1), (3, ["GLaDOS [neutral]: What is it now?", "Kevin: hi"]))
        self.assertEqual(out.getvalue(), "robot connected!\nGLaDOS [neutral]: What is it now?\nKevin: hi\n")

    def test_only_the_newest_are_kept_and_a_page_ahead_starts_over(self):
        for i in range(8):
            logbook.add(f"line {i}")
        self.assertEqual(logbook.since(0), (8, [f"line {i}" for i in range(3, 8)]))
        self.assertEqual(logbook.since(6), (8, ["line 6", "line 7"]))
        self.assertEqual(logbook.since(50)[1][0], "line 3")    # the brain restarted under it: everything kept


if __name__ == "__main__":
    unittest.main()
