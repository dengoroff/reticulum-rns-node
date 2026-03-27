from __future__ import annotations

import os
from datetime import datetime
from contextlib import asynccontextmanager
from io import BytesIO

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import qrcode
import qrcode.image.svg

from app.diagnostics import collect_diagnostics
from app.db import init_db
from app.lxmf_service import service
from app.repository import get_message, list_messages


BASE_DIR = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
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


def render(request: Request, template: str, **context):
    return templates.TemplateResponse(template, {"request": request, **context})


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    stats = service.stats()
    qr_svg = generate_qr_svg(f"lxmf://{stats['address']}") if stats.get("address") else None
    return render(request, "dashboard.html", stats=stats, qr_svg=qr_svg)


@app.get("/inbox", response_class=HTMLResponse)
async def inbox(request: Request):
    return render(request, "messages.html", title="Inbox", messages=list_messages("inbox"))


@app.get("/outbox", response_class=HTMLResponse)
async def outbox(request: Request):
    return render(request, "messages.html", title="Outbox", messages=list_messages("outbox"))


@app.get("/send", response_class=HTMLResponse)
async def send_form(request: Request):
    return render(request, "send.html", error=None, success=None)


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


@app.post("/messages/{message_id}/retry")
async def retry_message(message_id: int):
    service.retry_message(message_id)
    return RedirectResponse(url=f"/messages/{message_id}", status_code=303)


@app.post("/messages/{message_id}/cancel")
async def cancel_message(message_id: int):
    service.cancel_message(message_id)
    return RedirectResponse(url=f"/messages/{message_id}", status_code=303)


@app.post("/send", response_class=HTMLResponse)
async def send_message(
    request: Request,
    destination: str = Form(...),
    content: str = Form(...),
    title: str = Form(""),
):
    try:
        service.send_message(destination, content, title or None)
        return RedirectResponse(url="/outbox", status_code=303)
    except Exception as exc:
        return render(request, "send.html", error=str(exc), success=None)


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
