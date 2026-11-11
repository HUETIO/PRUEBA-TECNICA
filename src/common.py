from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def pg_config(role: str = "ingest") -> dict[str, Any]:
    load_env()
    if role == "super":
        user_key, password_key = "PG_SUPERUSER", "PG_SUPERPASSWORD"
    else:
        user_key, password_key = "PG_INGEST_USER", "PG_INGEST_PASSWORD"
    return {
        "host": os.getenv("PG_HOST", "localhost"),
        "port": int(os.getenv("PG_PORT", "5432")),
        "dbname": os.getenv("PG_DATABASE", "bia_datos"),
        "user": os.getenv(user_key, "rol_ingesta"),
        "password": os.getenv(password_key, ""),
        "connect_timeout": 10,
    }


def ensure_output_dirs() -> None:
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "logs").mkdir(exist_ok=True)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def log_event(event: str, **fields: Any) -> None:
    ensure_output_dirs()
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    with (ROOT / "logs" / "pipeline.log").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
