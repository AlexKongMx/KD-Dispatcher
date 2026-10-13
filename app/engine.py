"""Motor: enroll, schedule, tick (claim -> send -> finalize), supresion."""
import re
import uuid
from datetime import datetime, timedelta, timezone

from . import store
from .sequences import get_sequence

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# offset: "enroll+0s" | "enroll+24h" | "demo-24h" | "demo+7d"
OFFSET_RE = re.compile(r"^(enroll|demo)([+-])(\d+)(s|m|h|d)$")


def parse_offset(offset):
    m = OFFSET_RE.match(offset)
    if not m:
        raise ValueError(f"offset invalido: {offset}")
    base, sign, qty, unit = m.groups()
    qty = int(qty)
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    delta = timedelta(seconds=qty * mult)
    if sign == "-":
        delta = -delta
    return base, delta


def compute_schedule(sequence_name, enroll_at, demo_at):
    """Devuelve [(step_key, scheduled_for_utc_iso), ...]."""
    seq = get_sequence(sequence_name)
    out = []
    for step in seq["steps"]:
        base, delta = parse_offset(step["offset"])
        anchor = demo_at if base == "demo" else enroll_at
        if anchor is None:
            raise ValueError(
                f"paso {step['key']} requiere demo_at y no se proporciono")
        scheduled = anchor + delta
        # Si el toque ya vencio al inscribir (ej. demo en <24h),
        # se programa ASAP en vez de perderlo.
        if scheduled < enroll_at:
            scheduled = enroll_at
        out.append((step["key"], scheduled.astimezone(timezone.utc)))
    return out


def render_template(template, context):
    return template.format(**{k: context.get(k, "") for k in
                              ("name", "demo_date", "demo_time",
                               "clinic", "email")})


def format_demo_local(demo_at):
    """demo_at (aware) -> (fecha, hora) legibles. v1: formato simple."""
    return (demo_at.strftime("%A %d de %B"),
            demo_at.strftime("%H:%M"))


def build_idempotency_key(sequence, email, demo_at):
    demo_part = demo_at.isoformat() if demo_at else "-"
    return f"{sequence}|{email.strip().lower()}|{demo_part}"


def enroll(conn, *, sequence, email, name="", demo_at=None, data=None,
           idempotency_key=None, now=None):
    """Inscribe un lead y programa sus toques. Idempotente por key."""
    seq = get_sequence(sequence)
    if not seq["enabled"]:
        raise ValueError(f"secuencia deshabilitada: {sequence}")
    if not EMAIL_RE.match(email or ""):
        raise ValueError(f"email invalido: {email}")
    now = now or datetime.now(timezone.utc)
    if demo_at is not None and demo_at.tzinfo is None:
        raise ValueError("demo_at debe incluir zona horaria")

    key = idempotency_key or build_idempotency_key(sequence, email, demo_at)
    existing = store.find_enrollment_by_key(conn, key)
    if existing:
        return {"enrollment_id": existing["id"], "created": False}

    eid = store.create_enrollment(
        conn, sequence=sequence, email=email, name=name,
        demo_at=demo_at.isoformat() if demo_at else None,
        data=data, idempotency_key=key)
    for step_key, scheduled in compute_schedule(sequence, now, demo_at):
        store.add_touch(conn, enrollment_id=eid, step_key=step_key,
                        scheduled_for=scheduled.isoformat())
    conn.commit()
    return {"enrollment_id": eid, "created": True}


def _touch_context(touch):
    data = {}
    try:
        import json
        data = json.loads(touch.get("data_json") or "{}")
    except Exception:
        pass
    demo_at = None
    if touch.get("demo_at"):
        demo_at = datetime.fromisoformat(touch["demo_at"])
    ctx = {
        "name": touch.get("name") or "",
        "email": touch.get("email") or "",
        "clinic": data.get("clinic") or "tu clinica",
        "demo_date": "", "demo_time": "",
    }
    if demo_at:
        ctx["demo_date"], ctx["demo_time"] = format_demo_local(demo_at)
    return ctx


def tick(conn, *, now=None, sender, token=None, dry_run=False,
         max_attempts=5, backoff_base_s=900, stale_claim_s=900, limit=50):
    """Un ciclo del dispatcher. Retorna reporte.

    sender: callable(to, subject, html) -> message_id. En tests se inyecta
    un stub; en produccion es el POST al webhook n8n "Code -> Gmail send".
    """
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    token = token or ("tick_" + uuid.uuid4().hex[:8])

    recovered = store.recover_stale_claims(
        conn, now_iso=now_iso, stale_after_seconds=stale_claim_s)
    claimed = store.claim_due_touches(
        conn, now_iso=now_iso, token=token, limit=limit)

    report = {"claimed": len(claimed), "sent": 0, "failed": 0,
              "recovered_stale": recovered, "errors": []}
    for touch in claimed:
        seq = get_sequence(touch["sequence"])
        step = next(s for s in seq["steps"] if s["key"] == touch["step_key"])
        if not step["subject"] or not step["html"]:
            store.mark_dead(conn, touch["id"],
                            error="paso sin copy (secuencia deshabilitada?)")
            report["failed"] += 1
            report["errors"].append(
                f"{touch['id']}: paso sin copy")
            continue
        ctx = _touch_context(touch)
        subject = render_template(step["subject"], ctx)
        html = render_template(step["html"], ctx)
        try:
            if dry_run:
                store.mark_sent(conn, touch["id"], note="dry-run")
            else:
                msg_id = sender(touch["email"], subject, html)
                store.mark_sent(
                    conn, touch["id"],
                    note=f"gmail:{msg_id}" if msg_id else None)
            report["sent"] += 1
        except Exception as exc:  # noqa: BLE001 - se registra y reintenta
            attempts = touch["attempts"] + 1
            if attempts >= max_attempts:
                store.mark_dead(conn, touch["id"], error=str(exc))
                report["failed"] += 1
                report["errors"].append(f"{touch['id']}: {exc}")
            else:
                nxt = (now + timedelta(
                    seconds=backoff_base_s * attempts)).isoformat()
                store.mark_failed(conn, touch["id"], error=str(exc),
                                  next_attempt_at=nxt)
    conn.commit()
    return report
