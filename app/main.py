from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import init_db
from app.lxmf_service import service
from app.repository import get_message, list_messages


BASE_DIR = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


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
    return render(request, "dashboard.html", stats=service.stats())


@app.get("/inbox", response_class=HTMLResponse)
async def inbox(request: Request):
    return render(request, "messages.html", title="Inbox", messages=list_messages("inbox"))


@app.get("/outbox", response_class=HTMLResponse)
async def outbox(request: Request):
    return render(request, "messages.html", title="Outbox", messages=list_messages("outbox"))


@app.get("/send", response_class=HTMLResponse)
async def send_form(request: Request):
    return render(request, "send.html", error=None, success=None)


@app.get("/messages/{message_id}", response_class=HTMLResponse)
async def message_details(request: Request, message_id: int):
    message = get_message(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return render(request, "message_detail.html", message=message, title="Message Details")


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
