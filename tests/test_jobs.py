import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.jobs import JobError, JobStore, parse_page_range, validate_source_file


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


if __name__ == "__main__":
    unittest.main()
