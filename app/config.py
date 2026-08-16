from dataclasses import dataclass
from pathlib import Path
import os


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_dotenv() -> None:
    """Load a local .env file without adding a runtime dependency."""
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env_flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    mode: str
    queue_name: str
    host: str
    port: int
    data_dir: Path
    storage_dir: Path
    color_option: str
    color_mono: str
    color_color: str
    reload: bool
    max_upload_bytes: int
    cups_wait_seconds: int
    retention_hours: int
    admin_token: str


def get_settings() -> Settings:
    load_dotenv()
    data_dir = Path(os.getenv("TCP_PRINTER_DATA_DIR", PROJECT_ROOT / "data"))
    storage_dir = Path(os.getenv("TCP_PRINTER_STORAGE_DIR", PROJECT_ROOT / "storage"))
    data_dir.mkdir(parents=True, exist_ok=True)
    storage_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        mode=os.getenv("TCP_PRINTER_MODE", "dry-run").strip().lower(),
        queue_name=os.getenv("TCP_PRINTER_QUEUE", "CP1025").strip(),
        host=os.getenv("TCP_PRINTER_HOST", "0.0.0.0").strip(),
        port=int(os.getenv("TCP_PRINTER_PORT", "8080")),
        data_dir=data_dir,
        storage_dir=storage_dir,
        color_option=os.getenv("TCP_PRINTER_COLOR_OPTION", "print-color-mode").strip(),
        color_mono=os.getenv("TCP_PRINTER_COLOR_MONO", "monochrome").strip(),
        color_color=os.getenv("TCP_PRINTER_COLOR_COLOR", "color").strip(),
        reload=env_flag("TCP_PRINTER_RELOAD"),
        max_upload_bytes=int(os.getenv("TCP_PRINTER_MAX_UPLOAD_MB", "200")) * 1024 * 1024,
        cups_wait_seconds=int(os.getenv("TCP_PRINTER_CUPS_WAIT_SECONDS", "900")),
        retention_hours=max(1, int(os.getenv("TCP_PRINTER_RETENTION_HOURS", "24"))),
        admin_token=os.getenv("TCP_PRINTER_ADMIN_TOKEN", "").strip(),
    )
