# kd-dispatcher

Piloto de migración n8n → código: el Reminder Dispatcher reconstruido como
servicio FastAPI. Valida el patrón antes de migrar el Booking Service.

**Arquitectura:** el código es el "cerebro" (decide qué y cuándo mandar);
n8n queda como "manos" (el envío real sale por el workflow
`Code → Gmail send`, que usa la credencial OAuth `Gmail Kong Designs`).

## Secuencias

| Secuencia | Estado | Pasos |
|---|---|---|
| `KD_OWN_FUNNEL` | activa | T+0 confirmación, T-24h recordatorio, +1d post-llamada, +7d check-in |
| `DEMO_NURTURE` | **deshabilitada** (diseño pendiente de OK de Alex) | placeholders sin copy |

## Endpoints

- `GET /healthz` — estado + secuencias habilitadas
- `POST /enroll` `{sequence, email, name, demo_at (ISO con zona), data, idempotency_key?}` — inscribe y programa toques. Idempotente.
- `POST /admin/tick` — un ciclo: reclama toques vencidos (claim atómico), envía, finaliza. Lo llama el systemd timer cada 5 min.
- `POST /admin/suppress` `{email} | {enrollment_id}` — **supresión v1 (manual)**: cancela toques pendientes. Llamar si el lead responde, pide parar o se cierra.
- `GET /admin/status` — conteos y toques recientes.

Admin requiere header `X-Admin-Token` (ver `.env`, no va al repo).

## Supresión

- **v1 (actual):** manual vía `/admin/suppress`. Regla operativa: al recibir
  respuesta, marcar "no contactar" o cerrar el trato → suprimir.
- **v2 (propuesta, no implementada):** supresión automática por etapa del CRM
  (el servicio consulta la etapa del lead antes de cada toque y cancela los
  pendientes si cambió a `cerrado`/`no-contactar`). Requiere el CRM con
  etapas, hoy inexistente.

## Configuración (variables de entorno)

| Var | Default | Notas |
|---|---|---|
| `DISPATCHER_DB` | `/opt/kd-dispatcher/data/dispatcher.db` | SQLite (WAL) |
| `N8N_GMAIL_WEBHOOK` | `http://127.0.0.1:5678/webhook/code-gmail-send` | loopback: evita el hairpin HTTPS roto n8n→n8n |
| `DISPATCHER_DRY_RUN` | `true` | `true` = no llama al webhook; marca sent con nota `dry-run`. Poner `false` solo con copy aprobado |
| `DISPATCHER_ADMIN_TOKEN` | (vacío = admin bloqueado) | token para `/admin/*` |

## Deploy (VPS Hostinger, systemd)

```bash
# en el VPS, como root
mkdir -p /opt/kd-dispatcher/{app,data,logs}
# copiar app/, requirements.txt, systemd/
python3 -m venv /opt/kd-dispatcher/.venv
/opt/kd-dispatcher/.venv/bin/pip install -r requirements.txt
# crear /opt/kd-dispatcher/.env (ver .env.example; NO commitear)
cp systemd/kd-dispatcher*.service systemd/kd-dispatcher-tick.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now kd-dispatcher.service kd-dispatcher-tick.timer
curl -s http://127.0.0.1:8001/healthz
```

## Cutover (cuando Alex lo apruebe)

1. Copy de los 4 emails aprobado por Alex.
2. `DISPATCHER_DRY_RUN=false` + prueba real solo a `alextar@gmail.com`.
3. En el Booking Service (workflow `MTqYMLwm1zyniJ0L`): agregar el nodo HTTP
   propuesto (ver reporte del piloto) que llama `POST /enroll` al reservar.
4. Verificar un booking real end-to-end (enroll → tick → email recibido).
5. Apagar el dispatcher de n8n (`FZXIa7qCmrxi6SZc`) solo entonces.

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/ -q
```

Casos: happy path (4 toques en orden), supresión cancela pendientes,
reintento con backoff sin duplicar, claim atómico entre workers,
enroll idempotente, secuencia deshabilitada no inscribe, recuperación
de claims rancios.
