from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from src.batch import (
    SourceFormatError,
    _load_borders,
    _load_invoices,
    _load_measurements,
    _read_csv,
    _validate_classification,
    parse_consumption,
    parse_local_timestamp,
)
from src.crypto import encrypt_text, mask_free_text
from src.stream import _ensure_topic, build_alerts


ROOT = Path(__file__).resolve().parents[1]
BOGOTA = ZoneInfo("America/Bogota")


class BatchParsingTests(unittest.TestCase):
    def test_utc_timestamp_is_normalized_to_bogota(self) -> None:
        timestamp = parse_local_timestamp("2026-02-01T05:00:00Z", "timestamp_utc")
        self.assertEqual(timestamp, datetime(2026, 2, 1, 0, 0, tzinfo=BOGOTA))

    def test_comma_decimal_and_mwh_conversion(self) -> None:
        self.assertEqual(parse_consumption("163,795", "kWh"), Decimal("163.795"))
        self.assertEqual(parse_consumption("1.25", "MWh"), Decimal("1250.00"))

    def test_duplicate_precedence_and_exact_duplicate_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "consumos_2026-01_a.csv"
            second = root / "consumos_2026-01_b.csv"
            first.write_text(
                "frontera_id,timestamp,consumo_kwh,version_lectura,calidad_lectura\n"
                "CO1,2026-01-01 00:00:00,1.000,1,OK\n",
                encoding="utf-8",
            )
            second.write_text(
                "frontera_id,timestamp,consumo_kwh,version_lectura,calidad_lectura\n"
                "CO1,2026-01-01 00:00:00,2.000,1,OK\n"
                "CO1,2026-01-01 00:00:00,2.000,1,OK\n",
                encoding="utf-8",
            )
            winners, _, dq, _ = _load_measurements([first, second], {"CO1"})
        winner = next(iter(winners.values()))
        self.assertEqual(winner.consumo_kwh, Decimal("2.000"))
        self.assertEqual(winner.source_file, second.name)
        self.assertEqual(dq["duplicados_conflictivos"], 1)
        self.assertEqual(dq["duplicados_exactos"], 1)

    def test_later_version_correction_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "consumos_2026-04.csv"
            correction = root / "consumos_2026-04_correccion.csv"
            header = "frontera_id,timestamp,consumo_kwh,version_lectura,calidad_lectura\n"
            base.write_text(header + "CO1,2026-04-02 20:00:00,5.000,1,OK\n", encoding="utf-8")
            correction.write_text(
                header + "CO1,2026-04-02 20:00:00,7.000,2,CORREGIDA\n",
                encoding="utf-8",
            )
            winners, _, dq, _ = _load_measurements([base, correction], {"CO1"})
        winner = next(iter(winners.values()))
        self.assertEqual(winner.consumo_kwh, Decimal("7.000"))
        self.assertEqual(dq["correcciones_tardias_aplicadas"], 1)

    def test_invalid_values_are_quarantined_and_counted_as_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "consumos_2026-01.csv"
            path.write_text(
                "frontera_id,timestamp,consumo_kwh,version_lectura,calidad_lectura\n"
                "CO1,2026-01-01 00:00:00,,1,OK\n"
                "CO1,2026-01-01 01:00:00,-1,1,OK\n"
                "CO1,2026-01-01 02:00:00,1000000.001,1,OK\n",
                encoding="utf-8",
            )
            winners, quarantine, dq, _ = _load_measurements([path], {"CO1"})
        self.assertEqual(winners, {})
        self.assertEqual(len(quarantine), 3)
        self.assertEqual(dq["registros_nulos"], 1)
        self.assertEqual(dq["registros_negativos"], 1)
        self.assertEqual(dq["registros_outlier"], 1)
        self.assertEqual(dq["horas_sin_lectura_valida_total"], 744)

    def test_csv_parser_preserves_hostile_quoted_multiline_and_long_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hostile.csv"
            long_text = "x" * 140_000
            expected = f'línea uno\r\nlínea dos, "comillas" {long_text}'
            path.write_text(
                'id,observaciones\n1,"línea uno\nlínea dos, ""comillas"" '
                + long_text
                + '"\n',
                encoding="cp1252",
            )
            rows = _read_csv(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["observaciones"], expected)

    def test_truncated_csv_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corrupt.csv"
            path.write_text('id,observaciones\n1,"never closed\n', encoding="utf-8")
            with self.assertRaises(SourceFormatError):
                _read_csv(path)

    def test_short_truncated_record_fails_the_whole_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "truncated.csv"
            path.write_text("id,value\n1\n", encoding="utf-8")
            with self.assertRaises(SourceFormatError):
                _read_csv(path)

    @unittest.skipUnless(
        (ROOT / "data").exists(),
        "requires private local source data, which is not included in the repository",
    )
    def test_source_data_profile_and_required_quality_metrics(self) -> None:
        borders = _load_borders()
        files = sorted((ROOT / "data" / "consumos").glob("*.csv"))
        winners, quarantine, dq, months = _load_measurements(
            files, {str(row["frontera_id"]) for row in borders}
        )
        self.assertEqual(dq["filas_leidas"], 115_340)
        self.assertEqual(months, {"2026-01", "2026-02", "2026-03", "2026-04"})
        self.assertEqual(dq["duplicados_exactos"], 180)
        self.assertEqual(dq["duplicados_conflictivos"], 420)
        self.assertEqual(dq["registros_nulos"], 60)
        self.assertEqual(dq["registros_negativos"], 90)
        self.assertEqual(dq["registros_outlier"], 45)
        self.assertEqual(dq["registros_mwh_convertidos"], 400)
        self.assertEqual(dq["correcciones_tardias_aplicadas"], 300)
        self.assertEqual(dq["horas_sin_lectura_valida_total"], 655)
        self.assertTrue(winners)
        self.assertIsInstance(quarantine, list)

    def test_classification_covers_model_columns_and_encrypts_pii(self) -> None:
        _validate_classification()

    @unittest.skipUnless(
        (ROOT / "data").exists(),
        "requires private local source data, which is not included in the repository",
    )
    def test_invoice_orphans_are_excluded_and_quarantined(self) -> None:
        border_ids = {str(row["frontera_id"]) for row in _load_borders()}
        invoices, quarantine, orphan_count = _load_invoices(border_ids)
        self.assertEqual(len(invoices), 160)
        self.assertEqual(orphan_count, 12)
        self.assertEqual(len(quarantine), 12)
        self.assertTrue(all(row["frontera_id"] in border_ids for row in invoices))


class PrivacyTests(unittest.TestCase):
    def test_free_text_pii_is_detected_and_masked(self) -> None:
        text = "correo maria@example.com, celular 3211234567, cédula 12345678"
        masked, found = mask_free_text(text)
        self.assertTrue(found)
        self.assertNotIn("maria@example.com", masked)
        self.assertNotIn("3211234567", masked)
        self.assertNotIn("12345678", masked)
        formatted, formatted_found = mask_free_text(
            "celular +57 321 123 4567, cédula 1.234.567.890"
        )
        self.assertTrue(formatted_found)
        self.assertNotIn("321 123 4567", formatted)
        self.assertNotIn("1.234.567.890", formatted)

    def test_encryption_is_repeatable_and_not_plaintext(self) -> None:
        key = b"k" * 32
        aad = b"core.dim_cliente:CLI0001:nit"
        encrypted = encrypt_text("123456-7", key, aad)
        self.assertEqual(encrypted, encrypt_text("123456-7", key, aad))
        self.assertNotIn(b"123456-7", encrypted)
        self.assertNotEqual(encrypted, encrypt_text("765432-1", key, aad))


class StreamingTests(unittest.TestCase):
    def test_ensure_topic_supports_synchronous_admin_response(self) -> None:
        admin = Mock()
        with patch("src.stream.KafkaAdminClient", return_value=admin):
            _ensure_topic("localhost:9092", "mediciones.raw")
        admin.create_topics.assert_called_once()
        admin.close.assert_called_once()

    def test_ensure_topic_waits_for_legacy_futures(self) -> None:
        future = Mock()
        admin = Mock()
        admin.create_topics.return_value = {"mediciones.raw": future}
        with patch("src.stream.KafkaAdminClient", return_value=admin):
            _ensure_topic("localhost:9092", "mediciones.raw")
        future.result.assert_called_once_with(timeout=20)
        admin.close.assert_called_once()

    def test_alert_thresholds_missing_intervals_and_late_reading(self) -> None:
        events = [
            {"frontera_id": "CO1", "ts_medicion": "2026-06-01T00:00:00-05:00", "kwh": 10},
            {"frontera_id": "CO1", "ts_medicion": "2026-06-01T01:00:00-05:00", "kwh": 10.01},
            {"frontera_id": "CO2", "ts_medicion": "2026-06-01T00:15:00-05:00", "kwh": 20},
            {"frontera_id": "CO2", "ts_medicion": "2026-06-01T00:00:00-05:00", "kwh": 20},
        ]
        alerts = build_alerts(events, {"CO1": Decimal("10"), "CO2": Decimal("20")})
        outside = [a for a in alerts if a["tipo"] == "CONSUMO_FUERA_DE_BANDA"]
        missing = [a for a in alerts if a["tipo"] == "FRONTERA_SIN_REPORTE" and a["frontera_id"] == "CO1"]
        late = [a for a in alerts if a["tipo"] == "LECTURA_RETROACTIVA"]
        self.assertEqual(len(outside), 1)
        self.assertEqual(outside[0]["ts"], "2026-06-01T01:00:00-05:00")
        self.assertEqual(
            [alert["ts"] for alert in missing],
            ["2026-06-01T00:45:00-05:00", "2026-06-01T01:45:00-05:00"],
        )
        self.assertEqual(len(late), 1)
        self.assertEqual(late[0]["ts"], "2026-06-01T00:00:00-05:00")

    @unittest.skipUnless(
        (ROOT / "data").exists(),
        "requires private local source data, which is not included in the repository",
    )
    def test_source_stream_alert_totals(self) -> None:
        import json

        borders = _load_borders()
        with (ROOT / "data" / "eventos" / "mediciones_stream.ndjson").open(
            encoding="utf-8"
        ) as handle:
            events = [json.loads(line) for line in handle if line.strip()]
        alerts = build_alerts(
            events,
            {
                str(row["frontera_id"]): Decimal(str(row["max_kwh"]))
                for row in borders
            },
        )
        self.assertEqual(len(events), 3_795)
        self.assertEqual(
            {kind: sum(alert["tipo"] == kind for alert in alerts) for kind in {
                "CONSUMO_FUERA_DE_BANDA", "FRONTERA_SIN_REPORTE", "LECTURA_RETROACTIVA"
            }},
            {
                "CONSUMO_FUERA_DE_BANDA": 22,
                "FRONTERA_SIN_REPORTE": 6,
                "LECTURA_RETROACTIVA": 5,
            },
        )


if __name__ == "__main__":
    unittest.main()
