from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import psycopg2
from fastapi import FastAPI, HTTPException, Query
from psycopg2.extras import RealDictCursor


app = FastAPI(
    title="API de Plataforma de Datos",
    description="Consulta de agregados analíticos y resultados del pipeline.",
    version="1.0.0",
)
OUT_DIR = Path(
    os.getenv("OUT_DIR", str(Path(__file__).resolve().parents[1] / "out"))
)


def _connect() -> psycopg2.extensions.connection:
    password = os.getenv("PG_ANALYST_PASSWORD")
    if not password:
        raise HTTPException(
            status_code=503,
            detail="Falta configurar PG_ANALYST_PASSWORD para la API.",
        )
    try:
        return psycopg2.connect(
            host=os.getenv("PG_HOST", "localhost"),
            port=int(os.getenv("PG_PORT", "5432")),
            dbname=os.getenv("PG_DATABASE", "bia_datos"),
            user=os.getenv("PG_ANALYST_USER", "rol_analista"),
            password=password,
            connect_timeout=5,
        )
    except psycopg2.Error as exc:
        raise HTTPException(
            status_code=503,
            detail="No fue posible consultar PostgreSQL; verifica que el pipeline y Docker estén listos.",
        ) from exc


def _read_artifact(name: str) -> Any:
    path = OUT_DIR / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"No existe out/{name}; ejecuta el proceso correspondiente y vuelve a intentar.",
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo leer el artefacto out/{name}.",
        ) from exc


def _query(sql: str, params: tuple[object, ...] = ()) -> list[dict[str, Any]]:
    connection = _connect()
    try:
        with connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(sql, params)
                return [dict(row) for row in cursor.fetchall()]
    except HTTPException:
        raise
    except psycopg2.Error as exc:
        raise HTTPException(
            status_code=503,
            detail="Falló la consulta analítica de PostgreSQL.",
        ) from exc
    finally:
        connection.close()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/catalogos")
def catalogos() -> dict[str, list[str]]:
    months = _query(
        "SELECT DISTINCT to_char(mes, 'YYYY-MM') AS mes "
        "FROM analytics.vw_consumo_mensual ORDER BY mes"
    )
    borders = _query(
        "SELECT DISTINCT frontera_id FROM analytics.vw_consumo_mensual "
        "ORDER BY frontera_id"
    )
    return {
        "meses": [row["mes"] for row in months],
        "fronteras": [row["frontera_id"] for row in borders],
    }


@app.get("/api/indicadores")
def indicadores() -> dict[str, Any]:
    rows = _query(
        """
        SELECT count(*) AS registros,
               count(DISTINCT frontera_id) AS fronteras,
               coalesce(sum(consumo_kwh), 0) AS consumo_kwh,
               count(DISTINCT mes) AS meses
        FROM analytics.vw_consumo_mensual
        """
    )
    return rows[0]


@app.get("/api/consumos")
def consumos(
    mes: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    frontera_id: str | None = Query(default=None, min_length=1, max_length=80),
    limit: int = Query(default=250, ge=1, le=1000),
) -> dict[str, Any]:
    conditions: list[str] = []
    params: list[object] = []
    if mes:
        conditions.append("to_char(mes, 'YYYY-MM') = %s")
        params.append(mes)
    if frontera_id:
        conditions.append("frontera_id = %s")
        params.append(frontera_id)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)
    rows = _query(
        f"""
        SELECT frontera_id, to_char(mes, 'YYYY-MM') AS mes,
               consumo_kwh, horas_con_dato, mercado, operador_red, estado
        FROM analytics.vw_consumo_mensual
        {where}
        ORDER BY mes DESC, frontera_id
        LIMIT %s
        """,
        tuple(params),
    )
    return {"data": rows, "limite": limit}


@app.get("/api/calidad")
def calidad() -> dict[str, Any]:
    return _read_artifact("dq_report.json")


@app.get("/api/metricas")
def metricas() -> dict[str, Any]:
    return _read_artifact("metrics.json")


@app.get("/api/alertas")
def alertas(limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
    alerts = _read_artifact("alertas.json")
    if not isinstance(alerts, list):
        raise HTTPException(
            status_code=500,
            detail="El artefacto out/alertas.json no tiene el formato esperado.",
        )
    return {"data": alerts[:limit], "total": len(alerts)}
