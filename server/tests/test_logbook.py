"""The brain's log (brain/logbook.py): timestamped lines, the Log tab's ring, a file a day."""

import io
import os
import tempfile
import unittest
from unittest.mock import patch

from brain import logbook


class LogbookTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(logbook, "_lines", logbook.collections.deque(maxlen=5)),
                        patch.object(logbook, "_seq", 0), patch.object(logbook, "_dir", None),
                        patch.object(logbook, "_file", None), patch.object(logbook, "_file_day", ""),
                        patch.object(logbook, "stamp", lambda at=None: "12:34:56")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        if logbook._file is not None:
            logbook._file.close()
        for p in self.patches:
            p.stop()

    def test_whole_lines_are_stamped_kept_and_passed_on(self):
        out = io.StringIO()
        tee = logbook._Tee(out)
        print("robot connected!", file=tee)
        tee.write("GLaDOS [neutral]: What ")
        self.assertEqual(logbook.since(0), (1, ["12:34:56 robot connected!"]))  # half a line waits for the rest
        self.assertEqual(out.getvalue(), "12:34:56 robot connected!\n12:34:56 GLaDOS [neutral]: What ")  # shown now
        tee.write("is it now?\nKevin: hi\n")
        self.assertEqual(logbook.since(1), (3, ["12:34:56 GLaDOS [neutral]: What is it now?", "12:34:56 Kevin: hi"]))
        self.assertEqual(out.getvalue(),
                         "12:34:56 robot connected!\n12:34:56 GLaDOS [neutral]: What is it now?\n12:34:56 Kevin: hi\n")

    def test_only_the_newest_are_kept_and_a_page_ahead_starts_over(self):
        for i in range(8):
            logbook.add(f"line {i}")
        self.assertEqual(logbook.since(0), (8, [f"line {i}" for i in range(3, 8)]))
        self.assertEqual(logbook.since(6), (8, ["line 6", "line 7"]))
        self.assertEqual(logbook.since(50)[1][0], "line 3")    # the brain restarted under it: everything kept

    def test_lines_go_to_a_file_a_day_and_old_days_are_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
                open(os.path.join(d, f"brain-{day}.log"), "w").close()
            with patch.object(logbook, "_dir", d), patch.object(logbook, "_days", 2), \
                    patch.object(logbook.time, "strftime", lambda f, *a: "2026-10-27"):
                logbook.add("12:34:56 touch: pat")
                logbook._file.close()
                logbook._file = None
            self.assertEqual(sorted(os.listdir(d)), ["brain-2026-10-03.log", "brain-2026-10-27.log"])
            with open(os.path.join(d, "brain-2026-10-27.log"), encoding="utf-8") as f:
                self.assertEqual(f.read().splitlines()[-1], "12:34:56 touch: pat")

    def test_a_failed_write_reopens_the_file_later(self):
        class Stale:  # an open file on a share that went away while the PC slept
            def write(self, _):
                raise OSError("stale handle")

            def close(self):
                raise OSError("stale handle")

        with tempfile.TemporaryDirectory() as d:
            clock = [100.0]
            with patch.object(logbook, "_dir", d), patch.object(logbook, "_retry_at", 0.0), \
                    patch.object(logbook, "_file", Stale()), patch.object(logbook, "_file_day", "2026-10-28"), \
                    patch.object(logbook.time, "strftime", lambda f, *a: "2026-10-28"), \
                    patch.object(logbook.time, "monotonic", lambda: clock[0]):
                logbook.add("lost")            # the write fails: the stale file is dropped
                logbook.add("too soon")        # still waiting to retry
                clock[0] += logbook.RETRY_S
                logbook.add("back")            # a fresh file
                logbook._file.close()
                logbook._file = None
            with open(os.path.join(d, "brain-2026-10-28.log"), encoding="utf-8") as f:
                self.assertEqual(f.read().splitlines()[-1], "back")


if __name__ == "__main__":
    unittest.main()
