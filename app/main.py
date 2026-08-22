import mimetypes
import secrets
import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .config import PROJECT_ROOT, get_settings
from .jobs import ALLOWED_SUFFIXES, FileConverter, JobError, JobStore, PrintBackend, PrintWorker, TERMINAL_STATES, parse_page_range, validate_source_file


settings = get_settings()
store = JobStore(settings.data_dir / "printer.db")
converter = FileConverter(settings)
backend = PrintBackend(settings)
worker = PrintWorker(store, backend, settings.storage_dir, settings.retention_hours)


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.fail_interrupted_prints()
    worker.start()
    yield
    worker.stop()


# Windows and some Linux installations do not register .mjs by default.
# PDF.js is loaded as an ES module and browsers reject text/plain responses.
mimetypes.add_type("application/javascript", ".mjs")

app = FastAPI(title="TCP Printer", lifespan=lifespan)
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "app" / "templates"))
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "app" / "static")), name="static")


@app.middleware("http")
async def disable_page_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class SubmitOptions(BaseModel):
    color_mode: str = Field("monochrome")
    copies: int = Field(1, ge=1, le=99)
    page_range: str = Field("all")


def session_id(request: Request, response: Optional[Response] = None) -> str:
    identifier = request.cookies.get("tcp_printer_session")
    if not identifier:
        identifier = secrets.token_urlsafe(24)
        if response is not None:
            response.set_cookie("tcp_printer_session", identifier, httponly=True, samesite="lax")
    return identifier


def job_for_session(job_id: str, request: Request) -> dict:
    job = store.get(job_id)
    if not job or job["session_id"] != session_id(request):
        raise HTTPException(status_code=404, detail="未找到任务。")
    return job


def public_job(job: dict) -> dict:
    return {
        key: job[key]
        for key in (
            "id",
            "public_id",
            "file_name",
            "state",
            "message",
            "pages",
            "color_mode",
            "copies",
            "page_range",
            "created_at",
            "updated_at",
        )
    }


def admin_job(job: dict) -> dict:
    payload = public_job(job)
    payload["printer_job_id"] = job.get("cups_job_id")
    payload["session_id"] = job["session_id"]
    return payload


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    response = templates.TemplateResponse("index.html", {"request": request, "mode": settings.mode})
    session_id(request, response)
    return response


def require_admin(request: Request) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=404, detail="管理员功能未启用。")
    supplied = request.headers.get("x-admin-token", "")
    if not supplied or not secrets.compare_digest(supplied, settings.admin_token):
        raise HTTPException(status_code=401, detail="管理员令牌不正确。")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    if not settings.admin_token:
        raise HTTPException(status_code=404, detail="管理员功能未启用。")
    return templates.TemplateResponse("admin.html", {"request": request})


@app.get("/health")
async def health():
    return {"ok": True, "mode": settings.mode}


@app.get("/api/printer")
async def printer_status():
    return backend.status()


@app.get("/api/admin/status")
async def admin_status(request: Request):
    require_admin(request)
    usage = shutil.disk_usage(settings.storage_dir)
    printer_details = backend.diagnostics()
    return {
        "printer": printer_details["status"],
        "printer_details": printer_details,
        "mode": settings.mode,
        "queue": {"paused": store.queue_paused(), **store.queue_counts()},
        "retention_hours": settings.retention_hours,
        "storage": {"used": usage.used, "free": usage.free, "total": usage.total},
        "jobs": [admin_job(job) for job in store.list_recent()],
    }


@app.post("/api/admin/queue/pause")
async def admin_pause_queue(request: Request):
    require_admin(request)
    store.set_queue_paused(True)
    return {"paused": True}


@app.post("/api/admin/queue/resume")
async def admin_resume_queue(request: Request):
    require_admin(request)
    store.set_queue_paused(False)
    return {"paused": False}


@app.post("/api/admin/cleanup")
async def admin_cleanup(request: Request):
    require_admin(request)
    removed = store.purge_expired(
        datetime.now(timezone.utc), settings.storage_dir, all_finished=True
    )
    return {"removed_jobs": removed, "message": f"已清理 {removed} 个已结束任务"}


@app.post("/api/admin/jobs/{job_id}/cancel")
async def admin_cancel(job_id: str, request: Request):
    require_admin(request)
    job = store.get(job_id)
    if not job or job["state"] not in {"converting", "ready", "pending"}:
        raise HTTPException(status_code=409, detail="任务当前无法取消。")
    return public_job(store.update(job_id, state="cancelled", message="管理员已取消任务"))


@app.post("/api/admin/jobs/{job_id}/stop")
async def admin_stop(job_id: str, request: Request):
    require_admin(request)
    job = store.get(job_id)
    if not job or job["state"] != "printing":
        raise HTTPException(status_code=409, detail="任务当前没有在打印。")
    backend.cancel(job.get("cups_job_id"))
    return public_job(store.update(job_id, state="stopped", message="管理员已停止后续打印"))


@app.post("/api/admin/printer/jobs/{printer_job_id}/cancel")
async def admin_cancel_printer_job(printer_job_id: str, request: Request):
    require_admin(request)
    if settings.mode != "windows":
        raise HTTPException(status_code=409, detail="当前打印模式不支持直接管理 Windows 打印作业。")
    try:
        backend.cancel(printer_job_id)
    except JobError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"cancelled": printer_job_id}


@app.post("/api/uploads")
async def upload_file(request: Request, file: UploadFile = File(...)):
    current_session = request.cookies.get("tcp_printer_session")
    new_session = not current_session
    if not current_session:
        current_session = secrets.token_urlsafe(24)
    if not file.filename:
        raise HTTPException(status_code=400, detail="请选择文件。")
    safe_name = Path(file.filename).name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="暂不支持此文件类型。")

    upload_dir = settings.storage_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    destination = upload_dir / f"{secrets.token_hex(12)}{suffix}"
    size = 0
    with destination.open("wb") as target:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > settings.max_upload_bytes:
                target.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(status_code=400, detail="文件超过服务器允许的上传大小。")
            target.write(chunk)

    try:
        validate_source_file(destination)
    except JobError as error:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(error)) from error

    job = store.create_draft(current_session, safe_name, destination)
    try:
        pdf_path, pages = converter.convert(job)
        job = store.update(job["id"], state="ready", message="文件已准备完成", pdf_path=str(pdf_path), pages=pages)
    except JobError as error:
        job = store.update(job["id"], state="failed", message=str(error))
    payload = public_job(job)
    if job["state"] == "ready":
        payload["preview_url"] = f"/api/jobs/{job['id']}/preview"
    response = JSONResponse(payload)
    if new_session:
        response.set_cookie("tcp_printer_session", current_session, httponly=True, samesite="lax")
    return response


@app.get("/api/jobs")
async def list_jobs(request: Request):
    return [public_job(job) for job in store.list_session(session_id(request))]


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, request: Request):
    return public_job(job_for_session(job_id, request))


@app.get("/api/jobs/{job_id}/preview")
async def preview(job_id: str, request: Request):
    job = job_for_session(job_id, request)
    if not job.get("pdf_path") or not Path(job["pdf_path"]).exists():
        raise HTTPException(status_code=404, detail="预览文件不存在。")
    # 明确要求浏览器内嵌预览；微信等内置 WebView 仍可能强制交给系统浏览器处理。
    return FileResponse(
        job["pdf_path"],
        media_type="application/pdf",
        headers={"Content-Disposition": "inline"},
    )


@app.post("/api/jobs/{job_id}/submit")
async def submit(job_id: str, options: SubmitOptions, request: Request):
    job = job_for_session(job_id, request)
    if job["state"] != "ready":
        raise HTTPException(status_code=409, detail="该任务当前不能提交。")
    if options.color_mode not in {"monochrome", "color"}:
        raise HTTPException(status_code=400, detail="颜色模式不正确。")
    try:
        page_range, _ = parse_page_range(options.page_range, job["pages"])
    except JobError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    job = store.update(
        job_id,
        state="pending",
        message="已加入打印队列",
        color_mode=options.color_mode,
        copies=options.copies,
        page_range=page_range,
    )
    return public_job(job)


@app.post("/api/jobs/{job_id}/cancel")
async def cancel(job_id: str, request: Request):
    job = job_for_session(job_id, request)
    if job["state"] not in {"converting", "ready", "pending"}:
        raise HTTPException(status_code=409, detail="任务当前无法取消。")
    return public_job(store.update(job_id, state="cancelled", message="任务已取消"))


@app.post("/api/jobs/{job_id}/stop")
async def stop(job_id: str, request: Request):
    job = job_for_session(job_id, request)
    if job["state"] != "printing":
        raise HTTPException(status_code=409, detail="任务当前没有在打印。")
    backend.cancel(job.get("cups_job_id"))
    return public_job(store.update(job_id, state="stopped", message="已停止后续打印"))
