from __future__ import annotations

import json
import os
import sys
import time
import uuid
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from kafka import KafkaConsumer
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError
from kafka.structs import TopicPartition

from src.batch import _read_csv, parse_local_timestamp
from src.common import ROOT, ensure_output_dirs, load_env, log_event, write_json
from tools.producer import publish_file


BOGOTA = ZoneInfo("America/Bogota")
GRID_START = datetime(2026, 6, 1, 0, 0, tzinfo=BOGOTA)
INTERVAL = timedelta(minutes=15)
GRID_SIZE = 96


def _deserialize_event(raw: bytes | None) -> dict[str, object]:
    if raw is None:
        raise ValueError("Null Kafka message is not a measurement")
    return json.loads(raw.decode("utf-8"))


def build_alerts(events: list[dict[str, object]], limits: dict[str, Decimal]) -> list[dict[str, str]]:
    seen_intervals: dict[str, set[datetime]] = defaultdict(set)
    last_processed: dict[str, datetime] = {}
    alerts: list[dict[str, str]] = []

    for event in events:
        try:
            border_id = str(event["frontera_id"])
            timestamp = parse_local_timestamp(str(event["ts_medicion"]), "timestamp")
            amount = Decimal(str(event["kwh"]))
        except (KeyError, ValueError, InvalidOperation) as exc:
            raise ValueError("Invalid streaming record") from exc
        if not amount.is_finite() or border_id not in limits:
            raise ValueError("Streaming record has an invalid value or unknown border")
        if border_id in last_processed and timestamp < last_processed[border_id]:
            alerts.append({
                "tipo": "LECTURA_RETROACTIVA",
                "frontera_id": border_id,
                "ts": timestamp.isoformat(),
            })
        last_processed[border_id] = timestamp
        if amount > limits[border_id]:
            alerts.append({
                "tipo": "CONSUMO_FUERA_DE_BANDA",
                "frontera_id": border_id,
                "ts": timestamp.isoformat(),
            })
        offset = timestamp - GRID_START
        if offset.total_seconds() >= 0 and offset.total_seconds() % INTERVAL.total_seconds() == 0:
            index = int(offset.total_seconds() // INTERVAL.total_seconds())
            if index < GRID_SIZE:
                seen_intervals[border_id].add(timestamp)

    for border_id in sorted(limits):
        missing_run = 0
        for index in range(GRID_SIZE):
            slot = GRID_START + index * INTERVAL
            if slot in seen_intervals[border_id]:
                missing_run = 0
                continue
            missing_run += 1
            if missing_run == 3:
                alerts.append({
                    "tipo": "FRONTERA_SIN_REPORTE",
                    "frontera_id": border_id,
                    "ts": slot.isoformat(),
                })
    deduplicated = {
        (alert["tipo"], alert["frontera_id"], alert["ts"]): alert
        for alert in alerts
    }
    return sorted(
        deduplicated.values(),
        key=lambda alert: (alert["ts"], alert["frontera_id"], alert["tipo"]),
    )


def _read_limits() -> dict[str, Decimal]:
    path = ROOT / "data" / "maestros" / "limites_frontera.csv"
    records = _read_csv(path)
    limits: dict[str, Decimal] = {}
    for row in records:
        try:
            limits[row["frontera_id"]] = Decimal(row["max_kwh"])
        except (KeyError, InvalidOperation) as exc:
            raise ValueError("Invalid border limit file") from exc
    if not limits:
        raise ValueError("No streaming limits were found")
    return limits


def _ensure_topic(bootstrap: str, topic: str) -> None:
    admin = KafkaAdminClient(bootstrap_servers=bootstrap, client_id="bia-stream-admin")
    try:
        try:
            result = admin.create_topics([
                NewTopic(name=topic, num_partitions=1, replication_factor=1)
            ])
            if isinstance(result, Mapping):
                for future in result.values():
                    future.result(timeout=20)
        except TopicAlreadyExistsError:
            pass
    finally:
        admin.close()


def _consume_run(bootstrap: str, topic: str, source: Path) -> list[dict[str, object]]:
    _ensure_topic(bootstrap, topic)
    group_id = f"bia-stream-{uuid.uuid4()}"
    consumer = KafkaConsumer(
        bootstrap_servers=bootstrap,
        group_id=group_id,
        enable_auto_commit=False,
        auto_offset_reset="latest",
        value_deserializer=_deserialize_event,
        consumer_timeout_ms=1000,
    )
    try:
        deadline = time.monotonic() + 30
        partitions = None
        while time.monotonic() < deadline:
            partitions = consumer.partitions_for_topic(topic)
            if partitions:
                break
            time.sleep(0.25)
        if not partitions:
            raise TimeoutError("Kafka topic did not become available")
        assigned = [TopicPartition(topic, number) for number in sorted(partitions)]
        consumer.assign(assigned)
        starting_offsets = consumer.end_offsets(assigned)
        for partition, offset in starting_offsets.items():
            consumer.seek(partition, offset)

        with source.open("r", encoding="utf-8") as handle:
            expected = sum(1 for line in handle if line.strip())
        produced = publish_file(str(source), topic, bootstrap, rate=0)
        target = {
            TopicPartition(topic, partition): starting_offsets[TopicPartition(topic, partition)] + count
            for partition, count in produced.items()
        }
        if expected != sum(produced.values()):
            raise RuntimeError("Producer count differs from source record count")

        events: list[dict[str, object]] = []
        consumed_by_partition: dict[TopicPartition, int] = defaultdict(int)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            records = consumer.poll(timeout_ms=1000)
            for partition, messages in records.items():
                events.extend(message.value for message in messages)
                consumed_by_partition[partition] += len(messages)
            if target and all(
                consumed_by_partition[TopicPartition(topic, partition)] >= count
                for partition, count in produced.items()
            ):
                return events
        raise TimeoutError("Timed out while draining the finite Kafka input")
    finally:
        consumer.close()


def run_stream() -> list[dict[str, str]]:
    load_env()
    ensure_output_dirs()
    bootstrap = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
    topic = os.getenv("KAFKA_TOPIC", "mediciones.raw")
    source = ROOT / "data" / "eventos" / "mediciones_stream.ndjson"
    started = time.monotonic()
    run_id = str(uuid.uuid4())
    try:
        events = _consume_run(bootstrap, topic, source)
        alerts = build_alerts(events, _read_limits())
        write_json(ROOT / "out" / "alertas.json", alerts)
        log_event(
            "stream_completed",
            run_id=run_id,
            eventos_procesados=len(events),
            alertas_emitidas=len(alerts),
            duracion_segundos=round(time.monotonic() - started, 6),
            resultado="SUCCESS",
        )
        return alerts
    except Exception as exc:
        log_event(
            "stream_failed",
            run_id=run_id,
            error_type=type(exc).__name__,
            resultado="FAILED",
        )
        raise


def main() -> int:
    try:
        alerts = run_stream()
    except Exception as exc:
        print(f"Stream failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    print(f"Stream OK: {len(alerts)} alertas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
