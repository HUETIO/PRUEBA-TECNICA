from __future__ import annotations

import calendar
import csv
import hashlib
import io
import json
import re
import sys
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TypedDict
from zoneinfo import ZoneInfo

import psycopg2
import yaml
from psycopg2.extras import execute_values

from src.common import ROOT, ensure_output_dirs, log_event, pg_config, write_json
from src.crypto import PII_FIELDS, encryption_key, protect_client


BOGOTA = ZoneInfo("America/Bogota")
class SourceFormatError(ValueError):
    """A complete source file could not be trusted enough to process."""


@dataclass(frozen=True)
class Measurement:
    frontera_id: str
    timestamp: datetime
    consumo_kwh: Decimal
    version: int
    calidad: str
    source_file: str
    source_row: int

    @property
    def key(self) -> tuple[str, datetime]:
        return self.frontera_id, self.timestamp

    @property
    def signature(self) -> tuple[object, ...]:
        return self.key + (self.consumo_kwh, self.version, self.calidad)

    @property
    def precedence(self) -> tuple[int, str, int]:
        return self.version, self.source_file, self.source_row


class DQReport(TypedDict):
    filas_leidas: int
    duplicados_exactos: int
    duplicados_conflictivos: int
    registros_negativos: int
    registros_nulos: int
    registros_outlier: int
    registros_mwh_convertidos: int
    correcciones_tardias_aplicadas: int
    horas_sin_lectura_valida_total: int
    horas_sin_lectura_valida_por_frontera: dict[str, int]
    fronteras_huerfanas_facturacion: int
    clientes_con_pii_en_texto_libre: int
    registros_no_numericos: int
    unidades_no_soportadas: int
    fronteras_huerfanas_consumo: int


def _read_csv(path: Path) -> list[dict[str, str]]:
    csv.field_size_limit(16 * 1024 * 1024)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SourceFormatError(f"{path.name}: cannot read source file") from exc
    text = None
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise SourceFormatError(f"{path.name}: unsupported text encoding")
    header = text.splitlines()[0] if text.splitlines() else ""
    delimiter = ";" if header.count(";") > header.count(",") else ","
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        if not reader.fieldnames or any(not name for name in reader.fieldnames):
            raise SourceFormatError(f"{path.name}: empty or missing CSV header")
        if len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise SourceFormatError(f"{path.name}: duplicate CSV column names")
        records: list[dict[str, str]] = []
        for row_number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise SourceFormatError(f"{path.name}: malformed row {row_number}")
            records.append({key.strip(): (value or "").strip() for key, value in row.items()})
        return records
    except csv.Error as exc:
        raise SourceFormatError(f"{path.name}: malformed CSV structure") from exc


def parse_local_timestamp(raw: str, column: str) -> datetime:
    value = raw.strip()
    if not value:
        raise ValueError("empty timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc if column == "timestamp_utc" else BOGOTA)
    return parsed.astimezone(BOGOTA)


def parse_consumption(value: str, unit: str) -> Decimal:
    normalized = value.strip().replace(",", ".")
    try:
        amount = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("valor no numérico") from exc
    if not amount.is_finite():
        raise ValueError("valor no numérico")
    normalized_unit = unit.strip().lower()
    if normalized_unit in ("", "kwh"):
        return amount
    if normalized_unit == "mwh":
        return amount * Decimal(1000)
    raise ValueError("unsupported unit")


def load_classification() -> dict[str, dict[str, str]]:
    path = ROOT / "data_classification.yaml"
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        model = document["model"]
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise RuntimeError("data_classification.yaml is missing or invalid") from exc
    allowed = {"Público", "Interno", "Confidencial", "Restringido-PII"}
    if not isinstance(model, dict) or not model:
        raise RuntimeError("data_classification.yaml must classify model columns")
    expected_columns = {
        "core.dim_cliente": {
            "cliente_id", "razon_social", "nit", "email_contacto",
            "celular_contacto", "direccion", "ciudad", "departamento",
            "representante_legal", "observaciones",
        },
        "core.dim_frontera": {
            "frontera_id", "codigo_sic", "cliente_id", "mercado", "operador_red",
            "nivel_tension", "fecha_registro", "estado", "min_kwh", "max_kwh",
        },
        "core.fact_consumo_horario": {
            "frontera_id", "ts_local", "consumo_kwh", "version_lectura",
            "calidad_lectura", "source_file", "source_row",
        },
        "core.facturacion": {
            "factura_id", "frontera_id", "periodo", "consumo_facturado_kwh",
            "tarifa_cop_kwh", "valor_total_cop", "source_file",
        },
        "core.cuarentena": {
            "source_file", "record_key", "reason", "payload_sha256", "first_seen_at",
        },
        "core.audit_log": {
            "run_id", "actor", "started_at", "finished_at", "rows_read",
            "rows_loaded", "rows_rejected", "result", "details",
        },
        "analytics.vw_consumo_mensual": {
            "frontera_id", "mes", "consumo_kwh", "horas_con_dato",
            "mercado", "operador_red", "estado",
        },
    }
    if set(model) != set(expected_columns):
        raise RuntimeError("data_classification.yaml must cover every model table")
    for table, columns in model.items():
        if not isinstance(columns, dict) or any(level not in allowed for level in columns.values()):
            raise RuntimeError(f"Invalid classification for {table}")
        if set(columns) != expected_columns[table]:
            raise RuntimeError(f"data_classification.yaml does not cover every column in {table}")
    client_columns = model.get("core.dim_cliente", {})
    if any(client_columns.get(name) != "Restringido-PII" for name in PII_FIELDS):
        raise RuntimeError("All encrypted customer columns must be classified Restringido-PII")
    return model


def _validate_headers(path: Path, rows: list[dict[str, str]], required: set[str]) -> None:
    if rows:
        actual = set(rows[0])
    else:
        raw = path.read_bytes()
        header_text = raw.decode("utf-8-sig", errors="replace").splitlines()[0]
        delimiter = ";" if header_text.count(";") > header_text.count(",") else ","
        actual = {part.strip() for part in next(csv.reader([header_text], delimiter=delimiter))}
    missing = required - actual
    if missing:
        raise SourceFormatError(
            f"{path.name}: missing required columns ({', '.join(sorted(missing))})"
        )


def _payload_hash(row: Mapping[str, object]) -> str:
    safe = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(safe.encode("utf-8")).hexdigest()


def _load_measurements(files: list[Path], border_ids: set[str]) -> tuple[
    dict[tuple[str, datetime], Measurement],
    list[tuple[str, str, str, str]],
    DQReport,
    set[str],
]:
    candidates: list[Measurement] = []
    quarantined: list[tuple[str, str, str, str]] = []
    dq: Counter[str] = Counter()
    observed_months: set[str] = set()

    for path in files:
        filename_month = re.search(r"\d{4}-\d{2}", path.name)
        parsed_timestamp = False
        rows = _read_csv(path)
        if not rows:
            raw_header = path.read_bytes().decode("utf-8-sig", errors="replace").splitlines()
            if not raw_header:
                raise SourceFormatError(f"{path.name}: empty CSV file")
            delimiter = ";" if raw_header[0].count(";") > raw_header[0].count(",") else ","
            columns = set(next(csv.reader([raw_header[0]], delimiter=delimiter)))
            timestamp_column = "timestamp_utc" if "timestamp_utc" in columns else "timestamp"
            value_column = "consumo_kwh" if "consumo_kwh" in columns else "kwh"
            _validate_headers(
                path, rows,
                {"frontera_id", timestamp_column, value_column, "version_lectura"},
            )
            if filename_month:
                observed_months.add(filename_month.group())
            continue
        headers = set(rows[0])
        timestamp_column = "timestamp_utc" if "timestamp_utc" in headers else "timestamp"
        value_column = "consumo_kwh" if "consumo_kwh" in headers else "kwh"
        _validate_headers(
            path,
            rows,
            {"frontera_id", timestamp_column, value_column, "version_lectura"},
        )
        dq["filas_leidas"] += len(rows)
        for row_number, row in enumerate(rows, start=2):
            record_key = f"{path.name}:{row_number}"
            frontera_id = row.get("frontera_id", "")
            try:
                ts = parse_local_timestamp(row.get(timestamp_column, ""), timestamp_column)
                parsed_timestamp = True
                month = ts.strftime("%Y-%m")
                observed_months.add(month)
                if not frontera_id:
                    raise ValueError("frontera_id vacía")
                raw_value = row.get(value_column, "")
                if not raw_value:
                    dq["registros_nulos"] += 1
                    raise ValueError("valor nulo")
                unit = row.get("unidad", "kWh")
                amount = parse_consumption(raw_value, unit)
                if amount < 0:
                    dq["registros_negativos"] += 1
                    raise ValueError("consumo negativo")
                if amount > Decimal("1000000"):
                    dq["registros_outlier"] += 1
                    raise ValueError("consumo fuera del máximo físico")
                version = int(row["version_lectura"])
                if version < 0:
                    raise ValueError("version_lectura negativa")
                if frontera_id not in border_ids:
                    raise ValueError("frontera de consumo inexistente en maestro")
                if unit.strip().lower() == "mwh":
                    dq["registros_mwh_convertidos"] += 1
                candidates.append(
                    Measurement(
                        frontera_id,
                        ts,
                        amount.quantize(Decimal("0.001")),
                        version,
                        row.get("calidad_lectura", ""),
                        path.name,
                        row_number,
                    )
                )
            except (ValueError, InvalidOperation, OverflowError) as exc:
                reason = str(exc) or "registro no válido"
                if reason == "valor no numérico":
                    dq["registros_no_numericos"] += 1
                if reason == "unsupported unit":
                    dq["unidades_no_soportadas"] += 1
                if "frontera de consumo inexistente" in reason:
                    dq["fronteras_huerfanas_consumo"] += 1
                quarantined.append((path.name, record_key, reason, _payload_hash(row)))
        if not parsed_timestamp and filename_month:
            observed_months.add(filename_month.group())

    grouped: dict[tuple[str, datetime], list[Measurement]] = defaultdict(list)
    for item in candidates:
        grouped[item.key].append(item)
    winners: dict[tuple[str, datetime], Measurement] = {}
    for key, group in grouped.items():
        if len(group) > 1:
            observed_signatures: set[tuple[object, ...]] = set()
            for item in sorted(group, key=lambda row: (row.source_file, row.source_row)):
                if item.signature in observed_signatures:
                    dq["duplicados_exactos"] += 1
                elif observed_signatures:
                    dq["duplicados_conflictivos"] += 1
                    observed_signatures.add(item.signature)
                else:
                    observed_signatures.add(item.signature)
        winner = max(group, key=lambda item: item.precedence)
        winners[key] = winner
        if "_correccion" in winner.source_file and any(
            item.source_file != winner.source_file for item in group
        ):
            dq["correcciones_tardias_aplicadas"] += 1
    valid_keys = set(winners)
    per_border: Counter[str] = Counter()
    for frontera_id in sorted(border_ids):
        for month in sorted(observed_months):
            year, month_number = (int(value) for value in month.split("-"))
            expected_hours = calendar.monthrange(year, month_number)[1] * 24
            first = datetime(year, month_number, 1, tzinfo=BOGOTA)
            for hour_index in range(expected_hours):
                timestamp = first.timestamp() + hour_index * 3600
                expected_ts = datetime.fromtimestamp(timestamp, BOGOTA)
                if (frontera_id, expected_ts) not in valid_keys:
                    per_border[frontera_id] += 1
    dq["horas_sin_lectura_valida_total"] = sum(per_border.values())
    dq_report: DQReport = {
        "filas_leidas": dq["filas_leidas"],
        "duplicados_exactos": dq["duplicados_exactos"],
        "duplicados_conflictivos": dq["duplicados_conflictivos"],
        "registros_negativos": dq["registros_negativos"],
        "registros_nulos": dq["registros_nulos"],
        "registros_outlier": dq["registros_outlier"],
        "registros_mwh_convertidos": dq["registros_mwh_convertidos"],
        "correcciones_tardias_aplicadas": dq["correcciones_tardias_aplicadas"],
        "horas_sin_lectura_valida_total": int(dq["horas_sin_lectura_valida_total"]),
        "horas_sin_lectura_valida_por_frontera": dict(sorted(per_border.items())),
        "fronteras_huerfanas_facturacion": 0,
        "clientes_con_pii_en_texto_libre": 0,
        "registros_no_numericos": dq["registros_no_numericos"],
        "unidades_no_soportadas": dq["unidades_no_soportadas"],
        "fronteras_huerfanas_consumo": dq["fronteras_huerfanas_consumo"],
    }
    return winners, quarantined, dq_report, observed_months


def _load_clients() -> list[dict[str, str]]:
    path = ROOT / "data" / "maestros" / "clientes.csv"
    rows = _read_csv(path)
    _validate_headers(
        path,
        rows,
        {
            "cliente_id", "razon_social", "nit", "email_contacto",
            "celular_contacto", "direccion", "ciudad", "departamento",
            "representante_legal", "observaciones",
        },
    )
    return rows


def _load_borders() -> list[dict[str, object]]:
    source = ROOT / "data" / "maestros" / "fronteras.csv"
    limit_source = ROOT / "data" / "maestros" / "limites_frontera.csv"
    borders = _read_csv(source)
    limits = _read_csv(limit_source)
    _validate_headers(
        source,
        borders,
        {"frontera_id", "codigo_sic", "cliente_id", "mercado", "operador_red",
         "nivel_tension", "fecha_registro", "estado"},
    )
    _validate_headers(limit_source, limits, {"frontera_id", "min_kwh", "max_kwh"})
    by_id = {row["frontera_id"]: row for row in limits}
    if len(by_id) != len(limits):
        raise SourceFormatError("limites_frontera.csv contains duplicate border IDs")
    result: list[dict[str, object]] = []
    for row in borders:
        limit = by_id.get(row["frontera_id"])
        if not limit:
            raise SourceFormatError("A border has no operating limits")
        result.append({
            **row,
            "nivel_tension": int(row["nivel_tension"]),
            "min_kwh": Decimal(limit["min_kwh"]),
            "max_kwh": Decimal(limit["max_kwh"]),
        })
    return result


def _load_invoices(
    border_ids: set[str],
) -> tuple[list[dict[str, object]], list[tuple[str, str, str, str]], int]:
    invoice_files = sorted((ROOT / "data" / "facturacion").glob("*.json"))
    invoices: list[dict[str, object]] = []
    quarantine: list[tuple[str, str, str, str]] = []
    orphan_count = 0
    for path in invoice_files:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            records = document["facturas"]
            period = str(document["periodo"])
            if not isinstance(records, list):
                raise ValueError
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise SourceFormatError(f"{path.name}: malformed invoice JSON") from exc
        for index, row in enumerate(records, start=1):
            if not isinstance(row, dict):
                raise SourceFormatError(f"{path.name}: malformed invoice record")
            invoice_id = str(row.get("factura_id", f"row-{index}"))
            border_id = str(row.get("frontera_id", ""))
            if border_id not in border_ids:
                orphan_count += 1
                quarantine.append((
                    path.name,
                    invoice_id,
                    "factura huérfana: frontera inexistente",
                    _payload_hash(row),
                ))
                continue
            try:
                invoices.append({
                    "factura_id": invoice_id,
                    "frontera_id": border_id,
                    "periodo": period,
                    "consumo_facturado_kwh": Decimal(str(row["consumo_facturado_kwh"])),
                    "tarifa_cop_kwh": Decimal(str(row["tarifa_cop_kwh"])),
                    "valor_total_cop": Decimal(str(row["valor_total_cop"])),
                    "source_file": path.name,
                })
            except (KeyError, InvalidOperation, ValueError) as exc:
                raise SourceFormatError(f"{path.name}: invalid invoice amounts") from exc
    return invoices, quarantine, orphan_count


def _validate_classification() -> None:
    classification = load_classification()
    client_model = classification["core.dim_cliente"]
    restricted = {name for name, level in client_model.items() if level == "Restringido-PII"}
    if restricted != set(PII_FIELDS):
        raise RuntimeError("Customer encryption fields and classification do not match")


def _upsert(connection, query: str, values: Sequence[Sequence[object]]) -> None:
    if values:
        with connection.cursor() as cursor:
            execute_values(cursor, query, values, page_size=5000)


def _load_to_database(
    clients: list[dict[str, str]],
    borders: list[dict[str, object]],
    winners: dict[tuple[str, datetime], Measurement],
    invoices: list[dict[str, object]],
    quarantine: list[tuple[str, str, str, str]],
    key: bytes,
    run_id: str,
    run_started_at: datetime,
    rows_read: int,
    rows_loaded: int,
    rows_rejected: int,
) -> list[tuple[str, str, Decimal, int]]:
    connection = psycopg2.connect(**pg_config("ingest"))
    try:
        with connection:
            protected_clients: list[tuple[object, ...]] = []
            for row in clients:
                protected, _ = protect_client(row, key)
                protected_clients.append(tuple(
                    protected[column]
                    for column in (
                        "cliente_id", "razon_social", "nit", "email_contacto",
                        "celular_contacto", "direccion", "ciudad", "departamento",
                        "representante_legal", "observaciones",
                    )
                ))
            _upsert(
                connection,
                """
                INSERT INTO core.dim_cliente
                    (cliente_id, razon_social, nit, email_contacto, celular_contacto,
                     direccion, ciudad, departamento, representante_legal, observaciones)
                VALUES %s
                ON CONFLICT (cliente_id) DO UPDATE SET
                    razon_social = EXCLUDED.razon_social, nit = EXCLUDED.nit,
                    email_contacto = EXCLUDED.email_contacto,
                    celular_contacto = EXCLUDED.celular_contacto,
                    direccion = EXCLUDED.direccion, ciudad = EXCLUDED.ciudad,
                    departamento = EXCLUDED.departamento,
                    representante_legal = EXCLUDED.representante_legal,
                    observaciones = EXCLUDED.observaciones
                """,
                protected_clients,
            )
            border_values = [
                (
                    row["frontera_id"], row["codigo_sic"], row["cliente_id"],
                    row["mercado"], row["operador_red"], row["nivel_tension"],
                    row["fecha_registro"], row["estado"], row["min_kwh"], row["max_kwh"],
                )
                for row in borders
            ]
            _upsert(
                connection,
                """
                INSERT INTO core.dim_frontera
                    (frontera_id, codigo_sic, cliente_id, mercado, operador_red,
                     nivel_tension, fecha_registro, estado, min_kwh, max_kwh)
                VALUES %s
                ON CONFLICT (frontera_id) DO UPDATE SET
                    codigo_sic = EXCLUDED.codigo_sic, cliente_id = EXCLUDED.cliente_id,
                    mercado = EXCLUDED.mercado, operador_red = EXCLUDED.operador_red,
                    nivel_tension = EXCLUDED.nivel_tension,
                    fecha_registro = EXCLUDED.fecha_registro, estado = EXCLUDED.estado,
                    min_kwh = EXCLUDED.min_kwh, max_kwh = EXCLUDED.max_kwh
                """,
                border_values,
            )
            fact_values = [
                (
                    row.frontera_id, row.timestamp, row.consumo_kwh, row.version,
                    row.calidad, row.source_file, row.source_row,
                )
                for row in winners.values()
            ]
            _upsert(
                connection,
                """
                INSERT INTO core.fact_consumo_horario
                    (frontera_id, ts_local, consumo_kwh, version_lectura,
                     calidad_lectura, source_file, source_row)
                VALUES %s
                ON CONFLICT (frontera_id, ts_local) DO UPDATE SET
                    consumo_kwh = EXCLUDED.consumo_kwh,
                    version_lectura = EXCLUDED.version_lectura,
                    calidad_lectura = EXCLUDED.calidad_lectura,
                    source_file = EXCLUDED.source_file, source_row = EXCLUDED.source_row
                WHERE (core.fact_consumo_horario.version_lectura,
                       core.fact_consumo_horario.source_file,
                       core.fact_consumo_horario.source_row)
                    <= (EXCLUDED.version_lectura, EXCLUDED.source_file, EXCLUDED.source_row)
                """,
                fact_values,
            )
            invoice_values = [
                tuple(row[column] for column in (
                    "factura_id", "frontera_id", "periodo", "consumo_facturado_kwh",
                    "tarifa_cop_kwh", "valor_total_cop", "source_file",
                ))
                for row in invoices
            ]
            _upsert(
                connection,
                """
                INSERT INTO core.facturacion
                    (factura_id, frontera_id, periodo, consumo_facturado_kwh,
                     tarifa_cop_kwh, valor_total_cop, source_file)
                VALUES %s
                ON CONFLICT (factura_id) DO UPDATE SET
                    frontera_id = EXCLUDED.frontera_id, periodo = EXCLUDED.periodo,
                    consumo_facturado_kwh = EXCLUDED.consumo_facturado_kwh,
                    tarifa_cop_kwh = EXCLUDED.tarifa_cop_kwh,
                    valor_total_cop = EXCLUDED.valor_total_cop,
                    source_file = EXCLUDED.source_file
                """,
                invoice_values,
            )
            _upsert(
                connection,
                """
                INSERT INTO core.cuarentena
                    (source_file, record_key, reason, payload_sha256)
                VALUES %s
                ON CONFLICT (source_file, record_key, reason) DO UPDATE SET
                    payload_sha256 = EXCLUDED.payload_sha256
                """,
                quarantine,
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT frontera_id,
                           to_char(date_trunc('month', ts_local AT TIME ZONE 'America/Bogota'),
                                   'YYYY-MM'),
                           round(sum(consumo_kwh), 3),
                           count(*)::integer
                    FROM core.fact_consumo_horario
                    GROUP BY frontera_id,
                             date_trunc('month', ts_local AT TIME ZONE 'America/Bogota')
                    ORDER BY frontera_id, 2
                    """
                )
                monthly = cursor.fetchall()
                cursor.execute(
                    """
                    INSERT INTO core.audit_log
                        (run_id, actor, started_at, finished_at, rows_read,
                         rows_loaded, rows_rejected, result, details)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'SUCCESS', %s::jsonb)
                    """,
                    (
                        run_id,
                        "rol_ingesta",
                        run_started_at,
                        datetime.now(timezone.utc),
                        rows_read,
                        rows_loaded,
                        rows_rejected,
                        json.dumps({
                            "dim_cliente": len(clients),
                            "dim_frontera": len(borders),
                            "fact_consumo_horario": len(winners),
                            "facturacion": len(invoices),
                            "cuarentena": len(quarantine),
                        }),
                    ),
                )
        return monthly
    finally:
        connection.close()


def _record_failed_run(
    run_id: str,
    started_at: datetime,
    rows_read: int,
    rows_rejected: int,
    error_type: str,
) -> None:
    connection = psycopg2.connect(**pg_config("ingest"))
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO core.audit_log
                        (run_id, actor, started_at, finished_at, rows_read,
                         rows_loaded, rows_rejected, result, details)
                    VALUES (%s, 'rol_ingesta', %s, %s, %s, 0, %s, 'FAILED', %s::jsonb)
                    """,
                    (
                        run_id,
                        started_at,
                        datetime.now(timezone.utc),
                        rows_read,
                        rows_rejected,
                        json.dumps({"error_type": error_type}),
                    ),
                )
    finally:
        connection.close()


def _write_monthly_csv(rows: list[tuple[str, str, Decimal, int]]) -> None:
    path = ROOT / "out" / "consumo_mensual.csv"
    temporary = path.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("frontera_id", "mes", "consumo_kwh", "horas_con_dato"))
        for frontera_id, month, total, hours in rows:
            writer.writerow((frontera_id, month, f"{total:.3f}", hours))
    temporary.replace(path)


def run_batch() -> dict[str, object]:
    ensure_output_dirs()
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())
    rows_read = 0
    rows_rejected = 0
    try:
        _validate_classification()
        key = encryption_key()
        borders = _load_borders()
        border_ids = {str(row["frontera_id"]) for row in borders}
        files = sorted((ROOT / "data" / "consumos").glob("*.csv"))
        if not files:
            raise SourceFormatError("No consumption CSV files were discovered")
        read_started = time.perf_counter()
        winners, quarantine, dq, months = _load_measurements(files, border_ids)
        invoices, invoice_quarantine, orphan_count = _load_invoices(border_ids)
        quarantine.extend(invoice_quarantine)
        dq["fronteras_huerfanas_facturacion"] = orphan_count
        client_rows = _load_clients()
        dq["fronteras_huerfanas_facturacion"] = orphan_count
        rows_read = (
            dq["filas_leidas"] + len(invoices) + orphan_count
            + len(client_rows) + len(borders)
            + len(_read_csv(ROOT / "data" / "maestros" / "limites_frontera.csv"))
        )
        rows_rejected = len(quarantine)
        dq["clientes_con_pii_en_texto_libre"] = sum(
            protect_client(row, key)[1] for row in client_rows
        )
        rows_loaded = len(winners) + len(invoices) + len(client_rows) + len(borders)
        read_duration = time.perf_counter() - read_started
        load_started = time.perf_counter()
        monthly = _load_to_database(
            client_rows, borders, winners, invoices, quarantine, key,
            run_id, started_at, rows_read, rows_loaded, rows_rejected,
        )
        load_duration = time.perf_counter() - load_started
        _write_monthly_csv(monthly)
        write_json(ROOT / "out" / "dq_report.json", dq)
        duration = time.perf_counter() - started
        metrics = {
            "run_id": run_id,
            "timestamp_utc": started_at.isoformat(),
            "filas_leidas": rows_read,
            "filas_consumo_leidas": dq["filas_leidas"],
            "filas_cargadas": rows_loaded,
            "filas_consumo_cargadas": len(winners),
            "filas_facturacion_cargadas": len(invoices),
            "filas_maestros_cargadas": len(client_rows) + len(borders),
            "filas_rechazadas": rows_rejected,
            "rechazadas_por_motivo": dict(sorted(Counter(
                record[2] for record in quarantine
            ).items())),
            "duracion_segundos": {
                "lectura_y_validacion": round(read_duration, 6),
                "carga_postgres": round(load_duration, 6),
                "total": round(duration, 6),
            },
            "archivos_consumo_procesados": len(files),
            "meses_procesados": sorted(months),
        }
        write_json(ROOT / "out" / "metrics.json", metrics)
        log_event(
            "batch_completed",
            run_id=run_id,
            filas_leidas=dq["filas_leidas"],
            filas_cargadas=metrics["filas_cargadas"],
            filas_rechazadas=metrics["filas_rechazadas"],
            duracion_segundos=round(duration, 6),
            resultado="SUCCESS",
        )
        return metrics
    except Exception as exc:
        try:
            _record_failed_run(run_id, started_at, rows_read, rows_rejected, type(exc).__name__)
        except psycopg2.Error as audit_error:
            log_event(
                "batch_failure_audit_unavailable",
                run_id=run_id,
                error_type=type(audit_error).__name__,
            )
        log_event(
            "batch_failed",
            run_id=run_id,
            error_type=type(exc).__name__,
            resultado="FAILED",
        )
        raise


def main() -> int:
    try:
        metrics = run_batch()
    except Exception as exc:
        print(f"Batch failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    print(
        f"Batch OK: {metrics['filas_cargadas']} filas cargadas, "
        f"{metrics['filas_rechazadas']} en cuarentena."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
