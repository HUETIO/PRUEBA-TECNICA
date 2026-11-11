from __future__ import annotations

from decimal import Decimal
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api import app
from src.frontend import main as frontend_main


class DashboardApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    @patch("src.api._query")
    def test_consumption_filters_use_bound_parameters(self, query) -> None:
        query.return_value = [
            {
                "frontera_id": "CO123",
                "mes": "2026-04",
                "consumo_kwh": Decimal("12.500"),
                "horas_con_dato": 4,
                "mercado": "regulado",
                "operador_red": "operador",
                "estado": "activa",
            }
        ]

        response = self.client.get(
            "/api/consumos",
            params={"mes": "2026-04", "frontera_id": "CO123", "limit": 5},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"][0]["frontera_id"], "CO123")
        query.assert_called_once()
        self.assertEqual(query.call_args.args[1], ("2026-04", "CO123", 5))

    @patch("src.api._query")
    def test_catalogs_are_read_from_the_analytics_view(self, query) -> None:
        query.side_effect = [
            [{"mes": "2026-04"}],
            [{"frontera_id": "CO123"}],
        ]

        response = self.client.get("/api/catalogos")

        self.assertEqual(
            response.json(),
            {"meses": ["2026-04"], "fronteras": ["CO123"]},
        )
        self.assertEqual(query.call_count, 2)

    @patch("src.api._read_artifact")
    def test_alerts_are_limited_without_losing_total(self, read_artifact) -> None:
        read_artifact.return_value = [
            {"tipo": "A", "frontera_id": "CO123", "ts": "2026-04-01"},
            {"tipo": "B", "frontera_id": "CO456", "ts": "2026-04-02"},
        ]

        response = self.client.get("/api/alertas?limit=1")

        self.assertEqual(response.json()["total"], 2)
        self.assertEqual(len(response.json()["data"]), 1)

    def test_invalid_month_is_rejected(self) -> None:
        response = self.client.get("/api/consumos?mes=abril")

        self.assertEqual(response.status_code, 422)

    @patch("src.frontend._get_json")
    def test_frontend_builds_and_refreshes_with_api_data(self, get_json) -> None:
        responses = {
            "/api/catalogos": {"meses": ["2026-04"], "fronteras": ["CO123"]},
            "/api/indicadores": {
                "consumo_kwh": 42,
                "fronteras": 1,
                "meses": 1,
                "registros": 1,
            },
            "/api/consumos": {"data": []},
            "/api/calidad": {
                "filas_leidas": 10,
                "horas_sin_lectura_valida_total": 2,
                "fronteras_huerfanas_facturacion": 0,
            },
            "/api/metricas": {"filas_rechazadas": 1},
            "/api/alertas": {"data": [], "total": 0},
        }
        get_json.side_effect = lambda path, params=None: responses[path]
        page = SimpleNamespace(add=lambda *controls: None, update=lambda: None)

        frontend_main(page)

        self.assertEqual(
            [call.args[0] for call in get_json.call_args_list],
            [
                "/api/catalogos",
                "/api/indicadores",
                "/api/consumos",
                "/api/calidad",
                "/api/metricas",
                "/api/alertas",
            ],
        )


if __name__ == "__main__":
    unittest.main()
