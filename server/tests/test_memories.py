"""Rocky's long-term memory: keeping, matching, forgetting, expiring, and
surviving restarts, hand edits and a damaged file."""

import datetime as dt
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from brain import personality, thinking
from brain.memories import MAX_FACTS, Memories

DAY = dt.date(2026, 10, 22)


class MemoriesTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "data" / "memories.json"   # folder made on first save

    def tearDown(self):
        self.dir.cleanup()

    def test_remembers_across_restarts(self):
        m = Memories(self.path)
        fact, new = m.add("Kevin's cats are named Milo and Luna", today=DAY)
        self.assertTrue(new)
        self.assertEqual(fact.id, 1)
        again = Memories(self.path)   # a restarted brain
        self.assertEqual([f.text for f in again.facts(DAY)], ["Kevin's cats are named Milo and Luna"])
        self.assertIn("Kevin's cats are named Milo and Luna (learned 2026-10-22)", again.prompt(DAY))

    def test_empty_memory_adds_nothing_to_the_prompt(self):
        self.assertEqual(Memories(self.path).prompt(DAY), "")
        self.assertFalse(self.path.exists())   # nothing written until there's something to keep

    def test_same_fact_again_is_not_a_duplicate(self):
        m = Memories(self.path)
        m.add("Kevin likes green tea.", today=DAY)
        fact, new = m.add("  kevin likes  GREEN tea ", today=DAY + dt.timedelta(days=3))
        self.assertFalse(new)
        self.assertEqual(len(m.facts(DAY)), 1)
        self.assertEqual(fact.added, "2026-10-25")   # refreshed

    def test_a_copied_date_note_is_dropped(self):
        m = Memories(self.path)
        fact, _ = m.add("Kevin's favorite color is purple. (learned 2026-10-22)", today=DAY)
        self.assertEqual(fact.text, "Kevin's favorite color is purple.")
        fact, new = m.add("Kevin's favorite color is purple. (Learned 2026-10-22, true until 2026-11-01)", today=DAY)
        self.assertFalse(new)   # the same fact, not a second one

    def test_refuses_secrets_and_junk(self):
        m = Memories(self.path)
        for bad in ["", "   ", "Kevin's wifi password is hunter2", "the PIN code is 1234",
                    "card number 4111 1111", "x" * 201]:
            with self.assertRaises(ValueError, msg=bad):
                m.add(bad, today=DAY)
        self.assertEqual(m.facts(DAY), [])

    def test_memory_has_a_limit(self):
        m = Memories(self.path)
        for i in range(MAX_FACTS):
            m.add(f"fact number {i}", today=DAY)
        with self.assertRaises(ValueError):
            m.add("one too many", today=DAY)

    def test_forget_by_words_number_or_all(self):
        m = Memories(self.path)
        m.add("Kevin's cats are named Milo and Luna", today=DAY)
        m.add("Kevin's sister is Anna", today=DAY)
        m.add("Kevin drinks green tea in the morning", today=DAY)
        # Loose wording still finds it: "cat names" vs "cats are named".
        self.assertEqual([f.id for f in m.forget("the cat names", today=DAY)], [1])
        self.assertEqual(m.forget("the dog", today=DAY), [])            # no match, nothing lost
        self.assertEqual(m.forget("", today=DAY), [])
        self.assertEqual([f.id for f in m.forget("#2", today=DAY)], [2])
        self.assertEqual([f.id for f in m.forget("everything", today=DAY)], [3])
        self.assertEqual(m.facts(DAY), [])
        # Forgotten facts stay in the file for a human to restore.
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual([f["id"] for f in data["forgotten"]], [1, 2, 3])

    def test_ids_are_not_reused_while_facts_remain(self):
        m = Memories(self.path)
        m.add("one", today=DAY)
        m.add("two", today=DAY)
        m.forget("1", today=DAY)
        self.assertEqual(m.add("three", today=DAY)[0].id, 3)

    def test_time_bound_facts_expire(self):
        m = Memories(self.path)
        fact, _ = m.add("Kevin has a cold", days=7, today=DAY)
        self.assertEqual(fact.until, "2026-10-28")   # today is day 1 of 7
        self.assertIn("true until 2026-10-28", m.prompt(DAY))
        self.assertEqual(len(m.facts(dt.date(2026, 10, 28))), 1)
        self.assertEqual(m.facts(dt.date(2026, 10, 29)), [])
        self.assertEqual(Memories(self.path).facts(DAY), [])   # dropped from the file too
        with self.assertRaises(ValueError):
            m.add("forever and a day", days=0, today=DAY)

    def test_picks_up_hand_edits(self):
        m = Memories(self.path)
        m.add("Kevin's sister is Anna", today=DAY)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["facts"][0]["text"] = "Kevin's sister is Hanna"
        self.path.write_text(json.dumps(data), encoding="utf-8")
        later = time.time() + 5   # make sure the change is seen even on a coarse clock
        os.utime(self.path, (later, later))
        self.assertEqual([f.text for f in m.facts(DAY)], ["Kevin's sister is Hanna"])

    def test_damaged_file_is_set_aside(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        m = Memories(self.path)
        self.assertEqual(m.facts(DAY), [])
        self.assertTrue(self.path.with_name("memories.json.bad").exists())
        m.add("a fresh start", today=DAY)
        self.assertEqual(len(Memories(self.path).facts(DAY)), 1)


class PeopleAndPersonalitiesTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "memories.json"
        self.m = Memories(self.path, owner="Kevin")

    def test_facts_are_about_someone(self):
        self.assertEqual(self.m.add("likes purple", today=DAY)[0].about, "Kevin")       # nobody said: the owner
        self.assertEqual(self.m.add("likes tea", today=DAY, about="Regina")[0].about, "Regina")
        self.assertEqual(self.m.add("the wifi is slow", today=DAY, about="everyone")[0].about, "")
        _, new = self.m.add("likes purple", today=DAY, about="Regina")                   # same words, someone else
        self.assertTrue(new)
        self.assertEqual(self.m.people(), ["Kevin", "Regina"])
        p = self.m.prompt(DAY)
        self.assertLess(p.index("About Kevin:"), p.index("About Regina:"))
        self.assertLess(p.index("About Regina:"), p.index("About everyone"))

    def test_old_facts_are_about_the_owner(self):
        self.path.write_text(json.dumps({"facts": [{"id": 1, "text": "likes purple", "added": "2026-10-22"}]}))
        self.assertEqual(Memories(self.path, owner="Kevin").facts(DAY)[0].about, "Kevin")

    def test_shared_unless_kept_to_one_personality(self):
        self.m.add("the robot's name for the cat is Captain", today=DAY, by="stackchan")
        self.m.add("owes Kevin a dance", today=DAY, by="stackchan", only=True)
        self.assertNotIn("owes Kevin", self.m.prompt(DAY, char_id="rocky"))
        self.assertIn("Captain", self.m.prompt(DAY, char_id="rocky"))
        self.assertIn("owes Kevin", self.m.prompt(DAY, char_id="stackchan"))
        self.assertFalse(self.m.add("by hand", today=DAY, only=True)[0].only)  # no personality to keep it to

    def test_edit(self):
        f, _ = self.m.add("likes tee", today=DAY, by="rocky")
        f = self.m.edit(f.id, text="likes tea", about="Regina", until="2026-12-31", keep_to="rocky")
        self.assertEqual((f.text, f.about, f.until, f.only), ("likes tea", "Regina", "2026-12-31", True))
        self.assertEqual(Memories(self.path, owner="Kevin").facts(DAY)[0].about, "Regina")   # saved
        self.assertEqual(self.m.edit(f.id, until="").until, "")
        self.assertFalse(self.m.edit(f.id, keep_to="").only)                    # all personalities again
        g, _ = self.m.add("Rocky calls the cat Captain", today=DAY)              # added by hand...
        g = self.m.edit(g.id, keep_to="glados")                                  # ...kept to one personality
        self.assertEqual((g.by, g.only), ("glados", True))
        self.assertNotIn("Captain", self.m.prompt(DAY, char_id="rocky"))
        for bad in ({"text": "the card number is 1234"}, {"text": " "}, {"until": "soon"}):
            with self.assertRaises(ValueError, msg=bad):
                self.m.edit(f.id, **bad)
        with self.assertRaises(ValueError):
            self.m.edit(99, text="nope")


class MemoryWiringTests(unittest.TestCase):
    def test_tools_and_prompt_know_about_memory(self):
        names = {t["function"]["name"] for t in thinking.TOOLS}
        self.assertLessEqual({"remember", "forget"}, names)
        self.assertIn("`remember`", personality.SYSTEM_PROMPT)
        self.assertIn("Never remember passwords", personality.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
