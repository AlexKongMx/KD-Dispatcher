"""Configuracion por variables de entorno (nada secreto en el repo)."""
import os


class Settings:
    db_path: str = os.environ.get(
        "DISPATCHER_DB", "/opt/kd-dispatcher/data/dispatcher.db"
    )
    # Webhook del workflow n8n "Code -> Gmail send". En el VPS se llama
    # por loopback para no depender del hairpin HTTPS (roto n8n->n8n).
    n8n_gmail_webhook: str = os.environ.get(
        "N8N_GMAIL_WEBHOOK", "http://127.0.0.1:5678/webhook/code-gmail-send"
    )
    # DRY_RUN=true: no se llama al webhook; los toques se marcan sent
    # con nota "dry-run". Para pruebas y pre-aprobacion de copy.
    dry_run: bool = os.environ.get("DISPATCHER_DRY_RUN", "true").lower() in (
        "1", "true", "yes",
    )
    admin_token: str = os.environ.get("DISPATCHER_ADMIN_TOKEN", "")
    sender_name: str = "Kong Designs"
    sender_email: str = "alex@kongdesigns.com"
    max_attempts: int = int(os.environ.get("DISPATCHER_MAX_ATTEMPTS", "5"))
    stale_claim_seconds: int = int(os.environ.get("DISPATCHER_STALE_CLAIM_S", "900"))


settings = Settings()
