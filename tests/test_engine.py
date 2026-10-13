"""Tests del motor: happy path, supresion, reintento sin duplicar."""
from datetime import datetime, timedelta, timezone

import pytest

from app import store
from app.engine import enroll, tick

UTC = timezone.utc


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "test.db")
    store.init_db(path)
    conn = store.connect(path)
    yield conn
    conn.close()


def sender_ok(sent):
    def _send(to, subject, html):
        sent.append((to, subject))
        return "msg_123"
    return _send


def test_happy_path(db):
    now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    demo = datetime(2026, 10, 22, 16, 0, tzinfo=UTC)  # +52h
    r = enroll(db, sequence="KD_OWN_FUNNEL", email="TEST@x.com",
               name="Prueba", demo_at=demo,
               data={"clinic": "Dental Prueba"}, now=now)
    assert r["created"] is True
    # 4 toques programados
    n = db.execute("SELECT COUNT(*) FROM touches").fetchone()[0]
    assert n == 4

    sent = []
    # t0 inmediato
    rep = tick(db, now=now, sender=sender_ok(sent), dry_run=False)
    assert rep["sent"] == 1
    assert sent[0][0] == "test@x.com"
    assert "agendada" in sent[0][1]

    # T-24h: demo 22-oct 16:00 -> toque el 21-oct 16:00
    rep = tick(db, now=datetime(2026, 10, 21, 16, 1, tzinfo=UTC),
               sender=sender_ok(sent), dry_run=False)
    assert rep["sent"] == 1
    assert "Manana" in sent[1][1]

    # +1d post: 23-oct 16:00
    rep = tick(db, now=datetime(2026, 10, 23, 16, 1, tzinfo=UTC),
               sender=sender_ok(sent), dry_run=False)
    assert rep["sent"] == 1

    # +7d: 29-oct 16:00
    rep = tick(db, now=datetime(2026, 10, 29, 16, 1, tzinfo=UTC),
               sender=sender_ok(sent), dry_run=False)
    assert rep["sent"] == 1
    assert len(sent) == 4


def test_suppression_cancels_pending(db):
    now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    demo = datetime(2026, 10, 22, 16, 0, tzinfo=UTC)
    r = enroll(db, sequence="KD_OWN_FUNNEL", email="a@x.com",
               demo_at=demo, now=now)
    sent = []
    tick(db, now=now, sender=sender_ok(sent), dry_run=False)
    assert len(sent) == 1
    # El lead responde -> supresion manual
    assert store.suppress_enrollment(db, r["enrollment_id"]) is True
    db.commit()
    rep = tick(db, now=datetime(2026, 10, 29, 17, 0, tzinfo=UTC),
               sender=sender_ok(sent), dry_run=False)
    assert rep["sent"] == 0 and rep["claimed"] == 0
    assert len(sent) == 1  # no se mando nada mas
    pend = db.execute(
        "SELECT COUNT(*) FROM touches WHERE status='pending'").fetchone()[0]
    assert pend == 0


def test_retry_without_duplicating(db):
    now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    enroll(db, sequence="KD_OWN_FUNNEL", email="b@x.com",
           demo_at=datetime(2026, 10, 22, 16, 0, tzinfo=UTC), now=now)

    calls = {"n": 0}

    def flaky(to, subject, html):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("gmail 500")
        return "msg_2"

    # intento 1 falla -> reintento programado, no duplicado
    rep = tick(db, now=now, sender=flaky, dry_run=False,
               backoff_base_s=60)
    assert rep["sent"] == 0
    # antes del backoff no hay nada due
    rep = tick(db, now=now + timedelta(seconds=30), sender=flaky,
               dry_run=False, backoff_base_s=60)
    assert rep["sent"] == 0
    # despues del backoff se envia una sola vez
    rep = tick(db, now=now + timedelta(seconds=61), sender=flaky,
               dry_run=False, backoff_base_s=60)
    assert rep["sent"] == 1
    assert calls["n"] == 2


def test_racing_workers_claim_once(db):
    now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    enroll(db, sequence="KD_OWN_FUNNEL", email="c@x.com",
           demo_at=datetime(2026, 10, 22, 16, 0, tzinfo=UTC), now=now)
    # Dos workers reclaman "a la vez": el claim es atomico.
    a = store.claim_due_touches(db, now_iso=now.isoformat(),
                                token="worker_a")
    b = store.claim_due_touches(db, now_iso=now.isoformat(),
                                token="worker_b")
    assert len(a) == 1 and len(b) == 0
    db.commit()


def test_idempotent_enroll(db):
    now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    demo = datetime(2026, 10, 22, 16, 0, tzinfo=UTC)
    r1 = enroll(db, sequence="KD_OWN_FUNNEL", email="d@x.com",
                demo_at=demo, now=now)
    r2 = enroll(db, sequence="KD_OWN_FUNNEL", email="d@x.com",
                demo_at=demo, now=now)
    assert r1["created"] is True and r2["created"] is False
    assert r1["enrollment_id"] == r2["enrollment_id"]
    n = db.execute("SELECT COUNT(*) FROM touches").fetchone()[0]
    assert n == 4  # no se duplicaron los toques


def test_disabled_sequence_cannot_enroll(db):
    with pytest.raises(ValueError, match="deshabilitada"):
        enroll(db, sequence="DEMO_NURTURE", email="e@x.com",
               now=datetime(2026, 10, 20, 12, 0, tzinfo=UTC))


def test_stale_claim_recovery(db):
    now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    enroll(db, sequence="KD_OWN_FUNNEL", email="f@x.com",
           demo_at=datetime(2026, 10, 22, 16, 0, tzinfo=UTC), now=now)
    # Worker reclama y "muere" (claimed hace 1h, sin finalizar)
    old = (now - timedelta(hours=1)).isoformat()
    db.execute("UPDATE touches SET status='claimed', claimed_by='dead', "
               "claimed_at=? WHERE step_key='t0_confirm'", (old,))
    db.commit()
    sent = []
    rep = tick(db, now=now, sender=sender_ok(sent), dry_run=False,
               stale_claim_s=900)
    assert rep["recovered_stale"] == 1
    assert rep["sent"] == 1  # el toque se recupero y envio
