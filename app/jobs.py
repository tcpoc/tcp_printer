import re
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
import os
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image
from pypdf import PdfReader

from .config import Settings


ALLOWED_SUFFIXES = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".jpg", ".jpeg", ".png"}
OFFICE_SUFFIXES = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
TERMINAL_STATES = {"completed", "cancelled", "stopped", "failed"}


class JobError(ValueError):
    pass


def validate_source_file(path: Path) -> None:
    """Reject files whose content does not match their accepted extension."""
    suffix = path.suffix.lower()
    try:
        with path.open("rb") as source:
            header = source.read(8)
        if suffix == ".pdf" and not header.startswith(b"%PDF-"):
            raise JobError("文件内容不是有效的 PDF。")
        if suffix in IMAGE_SUFFIXES:
            with Image.open(path) as image:
                image.verify()
            return
        if suffix in {".docx", ".xlsx", ".pptx"} and not zipfile.is_zipfile(path):
            raise JobError("Office 文件内容无效或已损坏。")
        if suffix in {".doc", ".xls", ".ppt"} and header != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise JobError("Office 文件内容无效或已损坏。")
    except JobError:
        raise
    except Exception as error:
        raise JobError("文件内容无法读取或已损坏。") from error


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def short_job_id() -> str:
    return "A" + secrets.token_hex(3).upper()


def parse_page_range(value: str, total_pages: int) -> Tuple[str, int]:
    value = (value or "all").strip().lower().replace(" ", "")
    if value in {"", "all", "全部页面"}:
        return "all", total_pages

    pages = set()
    for item in value.split(","):
        if not item:
            raise JobError("页码范围格式不正确。")
        if "-" in item:
            parts = item.split("-", 1)
            if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                raise JobError("页码范围格式不正确。")
            start, end = int(parts[0]), int(parts[1])
            if start < 1 or end < start or end > total_pages:
                raise JobError("页码范围超出文件页数。")
            pages.update(range(start, end + 1))
        else:
            if not item.isdigit():
                raise JobError("页码范围格式不正确。")
            page = int(item)
            if page < 1 or page > total_pages:
                raise JobError("页码范围超出文件页数。")
            pages.add(page)
    return value, len(pages)


class JobStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.lock = threading.Lock()
        self._init_db()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _transaction(self):
        connection = self._connection()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_db(self) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    public_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    pdf_path TEXT,
                    state TEXT NOT NULL,
                    message TEXT,
                    pages INTEGER DEFAULT 0,
                    color_mode TEXT DEFAULT 'monochrome',
                    copies INTEGER DEFAULT 1,
                    page_range TEXT DEFAULT 'all',
                    cups_job_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_session ON jobs(session_id, created_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state, created_at)")

    def create_draft(self, session_id: str, file_name: str, input_path: Path) -> Dict:
        job_id = str(uuid.uuid4())
        timestamp = now_iso()
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO jobs (id, public_id, session_id, file_name, input_path, state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'converting', ?, ?)""",
                (job_id, short_job_id(), session_id, file_name, str(input_path), timestamp, timestamp),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> Optional[Dict]:
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_session(self, session_id: str) -> List[Dict]:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE session_id = ? ORDER BY created_at DESC LIMIT 20", (session_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def list_recent(self, limit: int = 100) -> List[Dict]:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),)
            ).fetchall()
        return [dict(row) for row in rows]

    def update(self, job_id: str, **values) -> Dict:
        if not values:
            job = self.get(job_id)
            if not job:
                raise JobError("未找到任务。")
            return job
        values["updated_at"] = now_iso()
        columns = ", ".join(f"{key} = ?" for key in values)
        with self._transaction() as connection:
            connection.execute(f"UPDATE jobs SET {columns} WHERE id = ?", (*values.values(), job_id))
        job = self.get(job_id)
        if not job:
            raise JobError("未找到任务。")
        return job

    def next_pending(self) -> Optional[Dict]:
        """Atomically claim one job so a restart cannot submit it twice."""
        with self._transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE state = 'pending' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            timestamp = now_iso()
            claimed = connection.execute(
                "UPDATE jobs SET state = 'printing', message = ?, updated_at = ? WHERE id = ? AND state = 'pending'",
                ("正在发送到打印机", timestamp, row["id"]),
            )
            if claimed.rowcount != 1:
                return None
        return self.get(row["id"])

    def fail_interrupted_prints(self) -> None:
        """Never resubmit a job after an unclean service restart."""
        with self._transaction() as connection:
            connection.execute(
                """UPDATE jobs
                   SET state = 'failed', message = ?, updated_at = ?
                   WHERE state = 'printing'""",
                ("打印服务曾重启，无法确认该任务是否已出纸，请检查打印机后重新提交。", now_iso()),
            )

    def purge_expired(self, cutoff: datetime, storage_root: Path) -> int:
        """Remove old non-active jobs and their files without touching active work."""
        cutoff_iso = cutoff.astimezone(timezone.utc).isoformat()
        with self._transaction() as connection:
            rows = connection.execute(
                """SELECT id, input_path, pdf_path FROM jobs
                   WHERE updated_at < ? AND state NOT IN ('pending', 'printing')""",
                (cutoff_iso,),
            ).fetchall()

        root = storage_root.resolve()
        removed_ids = []
        for row in rows:
            paths = [Path(path) for path in (row["input_path"], row["pdf_path"]) if path]
            safe_paths = []
            for path in paths:
                try:
                    resolved = path.resolve()
                    resolved.relative_to(root)
                except (OSError, ValueError):
                    continue
                safe_paths.append(resolved)
            try:
                for path in safe_paths:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink(missing_ok=True)
                job_dir = root / row["id"]
                if job_dir.exists():
                    shutil.rmtree(job_dir)
            except OSError:
                continue
            removed_ids.append(row["id"])

        if removed_ids:
            with self._transaction() as connection:
                connection.executemany("DELETE FROM jobs WHERE id = ?", ((job_id,) for job_id in removed_ids))

        # A crash between writing an upload and creating its DB row can leave an
        # orphan in uploads/. Remove only old files that are not referenced by any job.
        with self._transaction() as connection:
            references = {Path(row[0]).resolve() for row in connection.execute("SELECT input_path FROM jobs") if row[0]}
        uploads_dir = root / "uploads"
        if uploads_dir.exists():
            for orphan in uploads_dir.rglob("*"):
                if not orphan.is_file() or orphan.resolve() in references:
                    continue
                try:
                    if datetime.fromtimestamp(orphan.stat().st_mtime, timezone.utc) < cutoff:
                        orphan.unlink()
                except OSError:
                    continue
        return len(removed_ids)


class FileConverter:
    def __init__(self, settings: Settings):
        self.settings = settings

    def convert(self, job: Dict) -> Tuple[Path, int]:
        source = Path(job["input_path"])
        suffix = source.suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise JobError("暂不支持此文件类型。")
        output_dir = self.settings.storage_dir / job["id"]
        output_dir.mkdir(parents=True, exist_ok=True)

        if suffix == ".pdf":
            pdf_path = output_dir / "document.pdf"
            shutil.copy2(source, pdf_path)
        elif suffix in IMAGE_SUFFIXES:
            pdf_path = output_dir / "document.pdf"
            with Image.open(source) as image:
                image.convert("RGB").save(pdf_path, "PDF", resolution=150.0)
        else:
            pdf_path = self._convert_office(source, output_dir)

        try:
            pages = len(PdfReader(str(pdf_path)).pages)
        except Exception as error:
            raise JobError("无法读取转换后的 PDF。") from error
        if pages < 1:
            raise JobError("文件没有可打印页面。")
        return pdf_path, pages

    def _convert_office(self, source: Path, output_dir: Path) -> Path:
        executable = shutil.which("soffice") or shutil.which("libreoffice")
        if not executable:
            raise JobError("服务器尚未安装 LibreOffice，暂时无法转换 Office 文件。")
        profile = output_dir / "office-profile"
        profile_uri = profile.resolve().as_uri()
        result = subprocess.run(
            [
                executable,
                "--headless",
                f"-env:UserInstallation={profile_uri}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output_dir),
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        pdf_path = output_dir / f"{source.stem}.pdf"
        if result.returncode != 0 or not pdf_path.exists():
            raise JobError("Office 文件转换失败，请检查文件内容或字体。")
        return pdf_path


class PrintBackend:
    def __init__(self, settings: Settings):
        self.settings = settings

    def status(self) -> Dict[str, str]:
        if self.settings.mode != "cups":
            return {"state": "online", "label": "打印机在线"}
        try:
            result = subprocess.run(
                ["lpstat", "-l", "-p", self.settings.queue_name], capture_output=True, text=True, timeout=8, check=False,
                env=self._cups_env(),
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return {"state": "offline", "label": "打印服务不可用"}
        output = (result.stdout + result.stderr).lower()
        if result.returncode != 0 or "not found" in output:
            return {"state": "offline", "label": "未找到打印机队列"}
        if "disabled" in output:
            return {"state": "offline", "label": "打印机已暂停或发生故障"}
        if any(term in output for term in ("media-empty", "media-needed", "out of paper", "paper empty", "paper out", "缺纸")):
            return {"state": "attention", "label": "打印机缺纸"}
        if any(term in output for term in ("paper jam", "jammed", "卡纸")):
            return {"state": "attention", "label": "打印机卡纸"}
        if any(term in output for term in ("toner-empty", "toner low", "toner-low", "碳粉")):
            return {"state": "attention", "label": "碳粉不足"}
        return {"state": "online", "label": "打印机在线"}

    @staticmethod
    def _cups_env() -> Dict[str, str]:
        environment = os.environ.copy()
        environment["LC_ALL"] = "C"
        environment["LANG"] = "C"
        return environment

    def submit(self, job: Dict) -> Optional[str]:
        if self.settings.mode != "cups":
            time.sleep(1)
            return None
        color = self.settings.color_color if job["color_mode"] == "color" else self.settings.color_mono
        command = [
            "lp",
            "-d",
            self.settings.queue_name,
            "-n",
            str(job["copies"]),
            "-o",
            f"{self.settings.color_option}={color}",
        ]
        if job["page_range"] != "all":
            command.extend(["-o", f"page-ranges={job['page_range']}"])
        command.append(job["pdf_path"])
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False, env=self._cups_env())
        except FileNotFoundError as error:
            raise JobError("服务器未安装 CUPS 客户端。") from error
        except subprocess.TimeoutExpired as error:
            raise JobError("发送到打印机超时，请检查打印机是否在线。") from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            reason = detail[-1] if detail else "CUPS 未返回具体原因"
            raise JobError(f"任务未能发送到打印机：{reason[:160]}")
        match = re.search(r"(?:request id is\s+)?([^\s]+-\d+)\b", result.stdout, re.IGNORECASE)
        if not match:
            raise JobError("打印服务没有返回作业编号。")
        return match.group(1)

    def wait_until_finished(self, cups_job_id: Optional[str], stop_event: threading.Event) -> None:
        if self.settings.mode != "cups":
            stop_event.wait(1)
            return
        if not cups_job_id:
            raise JobError("打印服务没有返回作业编号。")
        deadline = time.monotonic() + self.settings.cups_wait_seconds
        failures = 0
        while time.monotonic() < deadline and not stop_event.wait(2):
            try:
                result = subprocess.run(
                    ["lpstat", "-W", "not-completed", "-o", self.settings.queue_name],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                    env=self._cups_env(),
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                failures += 1
                if failures >= 3:
                    raise JobError("无法确认打印机作业状态，请检查打印机。")
                continue
            if result.returncode == 0:
                failures = 0
                if cups_job_id not in result.stdout:
                    return
            else:
                failures += 1
                if failures >= 3:
                    raise JobError("无法确认打印机作业状态，请检查打印机。")
        if stop_event.is_set():
            return
        raise JobError("打印等待超时，请检查打印机是否缺纸、卡纸或离线。")

    def cancel(self, cups_job_id: Optional[str]) -> None:
        if self.settings.mode != "cups" or not cups_job_id:
            return
        try:
            result = subprocess.run(["cancel", cups_job_id], capture_output=True, text=True, timeout=15, check=False, env=self._cups_env())
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            raise JobError("无法连接打印服务，停止任务失败。") from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            reason = detail[-1] if detail else "CUPS 未返回具体原因"
            raise JobError(f"无法停止打印任务：{reason[:160]}")


class PrintWorker(threading.Thread):
    def __init__(self, store: JobStore, backend: PrintBackend, storage_dir: Path, retention_hours: int):
        super().__init__(daemon=True, name="print-worker")
        self.store = store
        self.backend = backend
        self.storage_dir = storage_dir
        self.retention_hours = retention_hours
        self.stop_event = threading.Event()

    def run(self) -> None:
        next_cleanup = 0.0
        while not self.stop_event.is_set():
            if time.monotonic() >= next_cleanup:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=self.retention_hours)
                try:
                    self.store.purge_expired(cutoff, self.storage_dir)
                except (OSError, sqlite3.Error):
                    pass
                next_cleanup = time.monotonic() + 3600
            job = self.store.next_pending()
            if not job:
                self.stop_event.wait(1)
                continue
            try:
                cups_job_id = self.backend.submit(job)
                self.store.update(job["id"], cups_job_id=cups_job_id, message="正在打印")
                self.backend.wait_until_finished(cups_job_id, self.stop_event)
                latest = self.store.get(job["id"])
                if latest and latest["state"] == "printing":
                    message = "模拟打印完成" if self.backend.settings.mode != "cups" else "已发送至打印机，请等待设备完成出纸。"
                    self.store.update(job["id"], state="completed", message=message)
            except JobError as error:
                self.store.update(job["id"], state="failed", message=str(error))
            except Exception:
                self.store.update(job["id"], state="failed", message="打印服务发生未知错误。")

    def stop(self) -> None:
        self.stop_event.set()
