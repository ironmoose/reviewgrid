import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from reviewgrid.model import build_action, load_reviews

CONTROLLER_ID = "11111111-1111-4111-8111-111111111111"

CHILD = "22222222-2222-4222-8222-222222222222"


class QueueTests(unittest.TestCase):
    def load(self, records):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps({"prs": records}))
            return load_reviews(path)

    def test_orders_ready_then_running_then_queued_then_watched(self):
        rows = self.load({
            "example/repo#1": {"status": "archived", "archived_from": "posted"},
            "example/repo#2": {"status": "pending"},
            "example/repo#3": {"status": "reviewing"},
            "example/repo#4": {"status": "awaiting-approval"},
            "example/repo#5": {"status": "merged"},
            "example/repo#6": {"status": "archived", "archived_from": "closed"},
        })
        self.assertEqual([row.key for row in rows], [
            "example/repo#4", "example/repo#3", "example/repo#2", "example/repo#1"])

    def test_ready_opens_controller_and_copies_non_approval_prompt(self):
        row = self.load({"example/repo#4": {
            "status": "awaiting-approval", "session_id": CHILD, "head_sha": "a" * 40}})[0]
        url, prompt = build_action(row, CONTROLLER_ID)
        self.assertEqual(url, f"codex://threads/{CONTROLLER_ID}")
        self.assertIn("show the exact drafted comments", prompt)
        self.assertIn(row.key, prompt)
        self.assertIn("a" * 40, prompt)

    def test_running_uses_real_codex_child_but_legacy_id_falls_back(self):
        rows = self.load({"example/repo#1": {"status": "reviewing", "session_id": CHILD},
                          "example/repo#2": {"status": "reviewing", "session_id": "local_old"}})
        self.assertEqual(build_action(rows[0], CONTROLLER_ID)[0], f"codex://threads/{CHILD}")
        self.assertEqual(build_action(rows[1], CONTROLLER_ID)[0], f"codex://threads/{CONTROLLER_ID}")

    def test_untrusted_links_and_invalid_records_are_rejected(self):
        rows = self.load({"example/repo#1": {"status": "reviewing", "session_id": "evil?prompt=send"},
                          "../../bad#1": {"status": "reviewing"},
                          "example/repo#2": None})
        self.assertEqual(len(rows), 1)
        self.assertEqual(build_action(rows[0], CONTROLLER_ID)[0], f"codex://threads/{CONTROLLER_ID}")

    def test_bad_queue_is_an_error_instead_of_a_false_empty_inbox(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"prs":')
            with self.assertRaises(ValueError):
                load_reviews(path)
            path.write_text('{"prs": []}')
            with self.assertRaises(ValueError):
                load_reviews(path)

    def test_ready_requires_a_valid_controller_even_with_a_child(self):
        row = self.load({"example/repo#1": {
            "status": "clean", "session_id": CHILD}})[0]
        for controller in ("", "not-a-uuid"):
            with self.subTest(controller=controller):
                with self.assertRaisesRegex(ValueError, "controller-thread"):
                    build_action(row, controller)


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication([])

    def test_error_keeps_rows_and_click_copies_prompt_without_queue_write(self):
        from PySide6.QtCore import QSettings
        from reviewgrid.app import ReviewWindow
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            original = json.dumps({"prs": {"example/repo#1": {
                "title": "<b>plain title</b>", "status": "awaiting-approval"}}})
            path.write_text(original)
            opened = Mock(return_value=True)
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
            window = ReviewWindow(path, settings, opened, controller_id=CONTROLLER_ID)
            window.activate_review(window.reviews[0])
            self.assertEqual(opened.call_args.args[0].toString(), f"codex://threads/{CONTROLLER_ID}")
            self.assertIn("show the exact drafted comments", self.qt.clipboard().text())
            self.assertEqual(path.read_text(), original)
            path.write_text('{')
            window.refresh()
            self.assertEqual(len(window.reviews), 1)
            self.assertIn("last good", window.health.text())
            window.close()

    def test_empty_inbox_and_failed_open_are_visible(self):
        from PySide6.QtCore import QSettings
        from reviewgrid.app import ReviewWindow
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"prs": {}}')
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
            window = ReviewWindow(path, settings, lambda url: False, controller_id=CONTROLLER_ID)
            self.assertIn("No tracked PRs", window.empty.text())
            window.open_controller()
            self.assertIn("Could not open", window.feedback.text())
            window.close()

    def test_cards_leave_room_for_wrapped_titles_and_followup_label(self):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QLabel, QPushButton
        from reviewgrid.app import ReviewWindow
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps({"prs": {"example/repo#1": {
                "title": "a long review title with several words " * 4,
                "status": "reviewing", "kind": "followup"}}}))
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
            window = ReviewWindow(path, settings, controller_id=CONTROLLER_ID)
            window.show()
            self.qt.processEvents()
            card = window.findChild(QPushButton, "review")
            self.assertGreaterEqual(card.height(), 100)
            for text in card.findChildren(QLabel):
                self.assertGreaterEqual(text.height(), text.fontMetrics().height())
            window.close()


if __name__ == "__main__":
    unittest.main()
