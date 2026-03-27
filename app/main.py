from __future__ import annotations

import base64
import os
from urllib.parse import quote
from datetime import datetime
from contextlib import asynccontextmanager
from io import BytesIO
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import qrcode
import qrcode.image.svg

from app.diagnostics import collect_diagnostics
from app.db import init_db
from app.lxmf_service import service
from app.repository import count_messages, delete_message as delete_message_record, get_message, list_messages


BASE_DIR = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.filters["filesize"] = lambda value: human_size(value)
templates.env.filters["datetime_ts"] = lambda value: (
    datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    if value not in (None, "")
    else "-"
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    service.start()
    yield


app = FastAPI(title="Reticulum Node UI", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

ATTACHMENT_LIMIT_BYTES = 5 * 1024 * 1024


def render(request: Request, template: str, **context):
    return templates.TemplateResponse(template, {"request": request, **context})


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    stats = service.stats()
    qr_svg = generate_qr_svg(f"lxmf://{stats['address']}") if stats.get("address") else None
    return render(request, "dashboard.html", stats=stats, qr_svg=qr_svg)


@app.get("/inbox", response_class=HTMLResponse)
async def inbox(request: Request, page: int = Query(1, ge=1)):
    return render_messages_page(request, "Inbox", "inbox", page)


@app.get("/outbox", response_class=HTMLResponse)
async def outbox(request: Request, page: int = Query(1, ge=1)):
    return render_messages_page(request, "Outbox", "outbox", page)


@app.get("/send", response_class=HTMLResponse)
async def send_form(request: Request):
    return render(request, "send.html", error=None, success=None, attachment_limit_bytes=ATTACHMENT_LIMIT_BYTES)


@app.get("/diagnostics", response_class=HTMLResponse)
async def diagnostics(request: Request):
    return render(request, "diagnostics.html", diagnostics=collect_diagnostics(), title="Diagnostics")


@app.post("/announce")
async def announce_now():
    service.announce_now("manual-ui")
    return RedirectResponse(url="/diagnostics", status_code=303)


@app.get("/messages/{message_id}", response_class=HTMLResponse)
async def message_details(request: Request, message_id: int):
    message = get_message(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return render(request, "message_detail.html", message=message, title="Message Details")


@app.get("/messages/{message_id}/attachments/{attachment_index}")
async def download_attachment(message_id: int, attachment_index: int):
    message = get_message(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    attachments = message.get("attachments") or []
    if attachment_index < 0 or attachment_index >= len(attachments):
        raise HTTPException(status_code=404, detail="Attachment not found")

    attachment = attachments[attachment_index]
    try:
        payload = base64.b64decode(attachment.get("data_b64") or "")
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Attachment payload is invalid") from exc

    filename = attachment.get("filename") or f"attachment-{attachment_index + 1}"
    content_type = attachment.get("content_type") or "application/octet-stream"
    safe_ascii = "".join(char if 32 <= ord(char) < 127 and char not in {'"', "\\"} else "_" for char in filename)
    headers = {"Content-Disposition": f"attachment; filename=\"{safe_ascii}\"; filename*=UTF-8''{quote(filename)}"}
    return Response(content=payload, media_type=content_type, headers=headers)


@app.post("/messages/{message_id}/retry")
async def retry_message(message_id: int):
    service.retry_message(message_id)
    return RedirectResponse(url=f"/messages/{message_id}", status_code=303)


@app.post("/messages/{message_id}/cancel")
async def cancel_message(message_id: int):
    service.cancel_message(message_id)
    return RedirectResponse(url=f"/messages/{message_id}", status_code=303)


@app.post("/messages/{message_id}/delete")
async def delete_message(message_id: int):
    message = get_message(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    redirect_to = f"/{message['direction']}"
    delete_message_record(message_id)
    return RedirectResponse(url=redirect_to, status_code=303)


@app.post("/send", response_class=HTMLResponse)
async def send_message(
    request: Request,
    destination: str = Form(...),
    content: str = Form(...),
    title: str = Form(""),
    attachments: list[UploadFile] = File(default=[]),
):
    try:
        parsed_attachments = await parse_attachments(attachments)
        total_bytes = sum(item["size"] for item in parsed_attachments)
        if total_bytes > ATTACHMENT_LIMIT_BYTES:
            raise ValueError(
                f"Attachments are limited to {human_size(ATTACHMENT_LIMIT_BYTES)} total per message. "
                f"Current selection is {human_size(total_bytes)}."
            )
        service.send_message(destination, content, title or None, attachments=parsed_attachments)
        return RedirectResponse(url="/outbox", status_code=303)
    except Exception as exc:
        return render(
            request,
            "send.html",
            error=str(exc),
            success=None,
            attachment_limit_bytes=ATTACHMENT_LIMIT_BYTES,
        )


@app.get("/api/node")
async def node_info():
    return service.stats()


@app.get("/api/inbox")
async def inbox_api():
    return list_messages("inbox")


@app.get("/api/outbox")
async def outbox_api():
    return list_messages("outbox")


@app.get("/api/diagnostics")
async def diagnostics_api():
    return collect_diagnostics()


def generate_qr_svg(value: str) -> str:
    qr = qrcode.QRCode(border=1, box_size=8)
    qr.add_data(value)
    qr.make(fit=True)
    image = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    buffer = BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode("utf-8")


def render_messages_page(request: Request, title: str, direction: str, page: int):
    per_page = 10
    total = count_messages(direction)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    offset = (page - 1) * per_page
    messages = list_messages(direction, limit=per_page, offset=offset)
    return render(
        request,
        "messages.html",
        title=title,
        messages=messages,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        has_prev=page > 1,
        has_next=page < total_pages,
    )


async def parse_attachments(files: list[UploadFile]) -> list[dict[str, Any]]:
    attachments = []
    for upload in files:
        if not upload.filename:
            continue
        data = await upload.read()
        attachments.append(
            {
                "filename": upload.filename,
                "content_type": upload.content_type or "application/octet-stream",
                "size": len(data),
                "data_b64": base64.b64encode(data).decode("ascii"),
            }
        )
    return attachments


def human_size(value: Any) -> str:
    if value in (None, ""):
        return "-"
    size = float(value)
    units = ("B", "KB", "MB", "GB")
    unit = units[0]
    for candidate in units:
        unit = candidate
        if size < 1024 or candidate == units[-1]:
            break
        size /= 1024
    return f"{size:.0f} {unit}" if unit == "B" else f"{size:.2f} {unit}"
