"""API HTTP del dispatcher."""
import json
import secrets
import urllib.request
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import store
from .config import settings
from .engine import enroll, tick
from .sequences import SEQUENCES

app = FastAPI(title="kd-dispatcher", version="0.1.0")


def _conn():
    return store.connect(settings.db_path)


def _require_admin(x_admin_token: str | None):
    if not settings.admin_token or not x_admin_token or not secrets.compare_digest(
            x_admin_token, settings.admin_token):
        raise HTTPException(status_code=401, detail="admin token invalido")


class EnrollIn(BaseModel):
    sequence: str = Field(examples=["KD_OWN_FUNNEL"])
    email: str
    name: str = ""
    # ISO con zona horaria, ej. "2026-10-20T10:00:00-06:00"
    demo_at: str | None = None
    data: dict = Field(default_factory=dict)
    idempotency_key: str | None = None


class SuppressIn(BaseModel):
    email: str | None = None
    enrollment_id: str | None = None


def _gmail_sender(to: str, subject: str, html: str) -> str:
    """Envia via el workflow n8n 'Code -> Gmail send' (OAuth en n8n)."""
    payload = json.dumps(
        {"to": to, "subject": subject, "html": html}).encode()
    req = urllib.request.Request(
        settings.n8n_gmail_webhook, data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode())
    # El workflow responde con la respuesta de Gmail API ({id, ...})
    return str(body.get("id") or body.get("threadId") or "")


@app.on_event("startup")
def _startup():
    store.init_db(settings.db_path)


@app.get("/healthz")
def healthz():
    return {"ok": True, "dry_run": settings.dry_run,
            "sequences": {k: v["enabled"] for k, v in SEQUENCES.items()}}


@app.post("/enroll")
def enroll_lead(payload: EnrollIn):
    """Inscripcion de un lead a una secuencia (la llama el Booking)."""
    demo_at = None
    if payload.demo_at:
        try:
            demo_at = datetime.fromisoformat(payload.demo_at)
        except ValueError:
            raise HTTPException(400, "demo_at debe ser ISO con zona horaria")
        if demo_at.tzinfo is None:
            raise HTTPException(400, "demo_at debe incluir zona horaria")
    conn = _conn()
    try:
        try:
            result = enroll(
                conn, sequence=payload.sequence, email=payload.email,
                name=payload.name, demo_at=demo_at, data=payload.data,
                idempotency_key=payload.idempotency_key)
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True, **result}
    finally:
        conn.close()


@app.post("/admin/tick")
def admin_tick(x_admin_token: str | None = Header(default=None)):
    """Dispara un ciclo del dispatcher (lo llama el systemd timer c/5min)."""
    _require_admin(x_admin_token)
    conn = _conn()
    try:
        report = tick(conn, sender=_gmail_sender,
                      dry_run=settings.dry_run,
                      max_attempts=settings.max_attempts,
                      stale_claim_s=settings.stale_claim_seconds)
        return {"ok": True, "dry_run": settings.dry_run, **report}
    finally:
        conn.close()


@app.post("/admin/suppress")
def admin_suppress(payload: SuppressIn,
                   x_admin_token: str | None = Header(default=None)):
    """Supresion v1 (manual): cancela toques pendientes del lead.

    Si el lead responde, pide parar, o se cierra -> llamar aqui.
    v2 (documentado en README): supresion automatica por etapa del CRM.
    """
    _require_admin(x_admin_token)
    if not payload.email and not payload.enrollment_id:
        raise HTTPException(400, "email o enrollment_id requerido")
    conn = _conn()
    try:
        if payload.enrollment_id:
            enr = store.get_enrollment(conn, payload.enrollment_id)
            if not enr:
                raise HTTPException(404, "enrollment no encontrado")
            store.suppress_enrollment(conn, payload.enrollment_id)
            conn.commit()
            return {"ok": True, "suppressed": 1}
        n = store.suppress_by_email(conn, payload.email)
        conn.commit()
        return {"ok": True, "suppressed": n}
    finally:
        conn.close()


@app.get("/admin/status")
def admin_status(x_admin_token: str | None = Header(default=None)):
    _require_admin(x_admin_token)
    conn = _conn()
    try:
        return {"ok": True, "dry_run": settings.dry_run,
                "counts": store.counts(conn),
                "recent": store.recent_touches(conn)}
    finally:
        conn.close()
