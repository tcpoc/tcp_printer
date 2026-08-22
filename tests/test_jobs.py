import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.config import get_settings
from app.jobs import FileConverter, JobError, JobStore, PrintBackend, parse_page_range, validate_source_file


class PageRangeTests(unittest.TestCase):
    def test_all_pages(self):
        self.assertEqual(parse_page_range("all", 6), ("all", 6))

    def test_deduplicates_pages(self):
        self.assertEqual(parse_page_range("1-3, 2, 5", 6), ("1-3,2,5", 4))

    def test_rejects_page_outside_document(self):
        with self.assertRaisesRegex(ValueError, "超出"):
            parse_page_range("1-7", 6)


class FileValidationTests(unittest.TestCase):
    def test_rejects_a_non_pdf_with_pdf_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_pdf = Path(directory) / "fake.pdf"
            fake_pdf.write_bytes(b"not a PDF")
            with self.assertRaisesRegex(JobError, "PDF"):
                validate_source_file(fake_pdf)

    def test_accepts_a_file_with_pdf_signature(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "document.pdf"
            pdf.write_bytes(b"%PDF-1.7\n")
            validate_source_file(pdf)


class QueueTests(unittest.TestCase):
    def test_queue_pause_state_is_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / "printer.db")
            self.assertFalse(store.queue_paused())
            store.set_queue_paused(True)
            self.assertTrue(store.queue_paused())
            self.assertEqual(store.queue_counts(), {"ready": 0, "pending": 0, "printing": 0})

    def test_claiming_a_job_changes_it_to_printing_once(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / "printer.db")
            job = store.create_draft("session", "demo.pdf", Path(directory) / "demo.pdf")
            store.update(job["id"], state="pending", message="已加入打印队列")

            claimed = store.next_pending()

            self.assertEqual(claimed["id"], job["id"])
            self.assertEqual(claimed["state"], "printing")
            self.assertIsNone(store.next_pending())

    def test_purge_removes_old_finished_files_but_keeps_active_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = JobStore(root / "printer.db")
            old_file = root / "uploads" / "old.pdf"
            old_file.parent.mkdir()
            old_file.write_bytes(b"old")
            old_job = store.create_draft("session", "old.pdf", old_file)
            store.update(old_job["id"], state="completed", message="打印完成")
            active_file = root / "uploads" / "active.pdf"
            active_file.write_bytes(b"active")
            orphan_file = root / "uploads" / "orphan.pdf"
            orphan_file.write_bytes(b"orphan")
            active_job = store.create_draft("session", "active.pdf", active_file)
            store.update(active_job["id"], state="printing", message="正在打印")

            old_timestamp = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
            with store._transaction() as connection:
                connection.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (old_timestamp, old_job["id"]))
            old_epoch = (datetime.now(timezone.utc) - timedelta(hours=48)).timestamp()
            os.utime(orphan_file, (old_epoch, old_epoch))

            self.assertEqual(store.purge_expired(datetime.now(timezone.utc) - timedelta(hours=24), root), 1)
            self.assertIsNone(store.get(old_job["id"]))
            self.assertIsNotNone(store.get(active_job["id"]))
            self.assertFalse(old_file.exists())
            self.assertTrue(active_file.exists())
            self.assertFalse(orphan_file.exists())

    def test_immediate_cleanup_removes_all_finished_jobs_but_keeps_active_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = JobStore(root / "printer.db")
            finished_file = root / "uploads" / "finished.pdf"
            finished_file.parent.mkdir()
            finished_file.write_bytes(b"finished")
            finished = store.create_draft("session", "finished.pdf", finished_file)
            store.update(finished["id"], state="completed", message="done")

            active_file = root / "uploads" / "active.pdf"
            active_file.write_bytes(b"active")
            active = store.create_draft("session", "active.pdf", active_file)
            store.update(active["id"], state="pending", message="queued")

            removed = store.purge_expired(
                datetime.now(timezone.utc), root, all_finished=True
            )
            self.assertEqual(removed, 1)
            self.assertIsNone(store.get(finished["id"]))
            self.assertIsNotNone(store.get(active["id"]))
            self.assertFalse(finished_file.exists())
            self.assertTrue(active_file.exists())


class ConverterSelectionTests(unittest.TestCase):
    def setUp(self):
        self.settings = replace(get_settings(), office_converter="auto")
        self.converter = FileConverter(self.settings)

    @patch("app.jobs.platform.system", return_value="Windows")
    def test_auto_uses_word_for_docx_on_windows(self, _system):
        self.assertEqual(self.converter._office_converter_for(Path("report.docx")), "word")

    @patch("app.jobs.platform.system", return_value="Windows")
    def test_auto_keeps_libreoffice_for_excel_on_windows(self, _system):
        self.assertEqual(self.converter._office_converter_for(Path("report.xlsx")), "libreoffice")

    @patch("app.jobs.platform.system", return_value="Linux")
    def test_auto_uses_libreoffice_on_ubuntu(self, _system):
        self.assertEqual(self.converter._office_converter_for(Path("report.doc")), "libreoffice")

    def test_word_rejects_non_word_office_document(self):
        converter = FileConverter(replace(self.settings, office_converter="word"))
        with self.assertRaisesRegex(JobError, "仅支持 DOC 和 DOCX"):
            converter._office_converter_for(Path("report.xlsx"))


class WindowsPrintTests(unittest.TestCase):
    def test_expands_page_ranges_to_zero_based_page_numbers(self):
        self.assertEqual(PrintBackend._windows_page_numbers("1-2,4", 4), [0, 1, 3])

    def test_decodes_windows_spooler_job_statuses(self):
        self.assertEqual(PrintBackend._windows_job_issue(0x0040), "打印机缺纸")
        self.assertEqual(PrintBackend._windows_job_issue(0x0400), "打印机需要人工处理")

    def test_keeps_none_when_windows_driver_does_not_return_a_job_id(self):
        backend = PrintBackend(replace(get_settings(), mode="windows"))
        self.assertIsNone(backend._windows_submitted_job_id(None, None, set(), None))


if __name__ == "__main__":
    unittest.main()
