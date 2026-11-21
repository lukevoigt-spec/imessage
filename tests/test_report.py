"""Tests for the offline report: one self-contained page holding everything the app has worked out."""

from __future__ import annotations

import ast
import json
import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_plans import GOALS, PlanBase  # noqa: E402
from imx import check, plans, reading, report, server  # noqa: E402

VOID = {"meta", "br", "img", "input", "hr", "link", "circle", "line", "rect", "polygon", "path"}


class Balance(HTMLParser):
    """Checks that every element that is opened is closed, in order."""

    def __init__(self):
        super().__init__()
        self.stack, self.errors, self.ids, self.links, self.scripts = [], [], set(), [], 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        if tag == "a" and (attrs.get("href") or "").startswith("#"):
            self.links.append(attrs["href"][1:])
        if tag == "script" or any(name.startswith("on") for name in attrs):
            self.scripts += 1
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append("unexpected </%s> with %s open" % (tag, self.stack[-3:]))
            return
        self.stack.pop()


def parsed(page):
    checker = Balance()
    checker.feed(page)
    checker.close()
    return checker


class ReportTests(PlanBase):
    def everything(self):
        self.write_histories()
        plans.save_goals(GOALS)
        self.write_portrait()
        plans.start(self.conn, "partner")
        self.finish(plans.runner)
        plans.start(self.conn, "decade")
        self.finish(plans.runner)
        plans.start(self.conn, "month", "2025-03")
        self.finish(plans.runner)

    def test_before_anything_is_written_every_section_says_so(self):
        made = report.build(self.conn)
        page = made["html"]
        self.assertEqual(made["problems"], [])
        self.assertEqual(made["sections"], report.SECTION_KEYS)
        for phrase in ("The portrait has not been written yet.", "The plan has not been written yet.",
                       "The reality check has not been written yet.", "No relationship histories have been written yet.",
                       "The accuracy check has not been run."):
            self.assertIn(phrase, page)
        checker = parsed(page)
        self.assertEqual((checker.errors, checker.stack), ([], []))

    def test_everything_is_in_one_balanced_page_with_working_evidence_links(self):
        self.everything()
        made = report.build(self.conn)
        page = made["html"]
        self.assertEqual(made["problems"], [])
        self.assertTrue(page.startswith("<!doctype html>"))
        for phrase in ("A planner who shows care through logistics.", "You fix when she wants to be heard.", "Work wins most weeks.",
                       "History of Avery Stone", "History of Jordan Pike", "Monthly check", "Read: 2025-03", "The research behind it",
                       "not found word for word", "What the reading cost", "Thanks and praise"):
            self.assertIn(phrase, page, phrase)
        self.assertEqual(page.count('<svg class="radar"'), 3)                           # overall, and one per person
        self.assertRegex(page, r'<td>Avery Stone</td><td class="n">\d')               # a real message count, not a dash
        checker = parsed(page)
        self.assertEqual((checker.errors, checker.stack), ([], []))
        self.assertEqual(checker.scripts, 0)                                             # nothing in the page can run
        evidence = [link for link in checker.links if link.startswith("ev-")]
        self.assertGreater(len(evidence), 3)
        self.assertEqual([link for link in checker.links if link not in checker.ids], [])   # every link has somewhere to go
        self.assertEqual(made["evidence"], len({link for link in evidence}))
        self.assertNotIn("http://", page.replace("http://www.w3.org", ""))
        self.assertNotIn("https://", page)                                               # nothing fetched from the internet

    def test_no_raw_messages_from_the_other_side(self):
        self.everything()
        page = report.build(self.conn)["html"]
        # In the fixture, every message the other person sent is an even-numbered "message N"; none may appear.
        theirs = [n for n in re.findall(r"message (\d+)\b", page) if int(n) % 2 == 0]
        self.assertEqual(theirs, [])

    def test_text_from_anywhere_is_escaped(self):
        self.everything()
        row = reading._store().execute("SELECT body FROM plan_doc WHERE what = 'partner'").fetchone()
        body = json.loads(row[0])
        body["in_brief"] = '<script>alert(1)</script><img src=x onerror="alert(2)">'
        body["moments"][0]["when"] = "</section></main><b>broken</b>"
        reading._write("UPDATE plan_doc SET body = ? WHERE what = 'partner'", (json.dumps(body),))
        page = report.build(self.conn)["html"]
        self.assertNotIn("<script>", page)
        self.assertNotIn("<img", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        checker = parsed(page)
        self.assertEqual((checker.errors, checker.stack, checker.scripts), ([], [], 0))

    def test_sections_can_be_left_out(self):
        self.everything()
        made = report.build(self.conn, ["portrait", "histories"])
        self.assertEqual(made["sections"], ["portrait", "histories"])
        self.assertNotIn('id="partner"', made["html"])
        self.assertNotIn('href="#ev-', made["html"])                                  # no appendix: numbers are plain text
        self.assertIn("Evidence: #", made["html"])
        via_server = server.GET_ROUTES["/api/report"](self.conn, {"sections": "numbers"})
        self.assertEqual(via_server["sections"], ["numbers"])
        self.assertEqual(server.GET_ROUTES["/api/report"](self.conn, {})["sections"], report.SECTION_KEYS)

    def test_one_broken_section_does_not_cost_the_rest(self):
        self.everything()
        real = report.BUILDERS["traits"]
        report.BUILDERS["traits"] = lambda conn, ev: 1 / 0
        try:
            made = report.build(self.conn)
        finally:
            report.BUILDERS["traits"] = real
        self.assertEqual(len(made["problems"]), 1)
        self.assertIn("This section could not be made", made["html"])
        self.assertIn("History of Avery Stone", made["html"])

    def test_contents_say_what_is_ready(self):
        before = {s["key"]: s["ready"] for s in report.contents()}
        self.assertFalse(before["portrait"])
        self.everything()
        after = {s["key"]: s["ready"] for s in report.contents()}
        self.assertTrue(all(after[key] for key in ("portrait", "traits", "partner", "decade", "histories")))
        self.assertFalse(after["accuracy"])
        check.start(self.conn, 5)                                                      # all five digests, so the flagged one is in
        self.finish(check.runner)
        self.assertTrue({s["key"]: s["ready"] for s in report.contents()}["accuracy"])
        self.assertIn("Flagged digests", report.build(self.conn, ["accuracy"])["html"])


class ReportLockTests(unittest.TestCase):
    def test_nothing_that_takes_the_store_lock_is_called_while_holding_it(self):
        takers = {"spent", "plan", "start", "status", "read", "summary", "overview", "_digests", "_setting", "_set", "_write",
                  "measures", "months_read", "charts", "contents", "build", "project_spent"}
        tree = ast.parse((ROOT / "imx" / "report.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.With) and any("_store_lock" in ast.dump(item.context_expr) for item in node.items):
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call):
                        called = inner.func.attr if isinstance(inner.func, ast.Attribute) else getattr(inner.func, "id", "")
                        self.assertNotIn(called, takers)


if __name__ == "__main__":
    unittest.main()
