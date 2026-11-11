from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import flet as ft


API_URL = os.getenv("DATA_API_URL", "http://127.0.0.1:8000").rstrip("/")


class ApiError(Exception):
    pass


def _get_json(path: str, params: Mapping[str, str] | None = None) -> Any:
    url = f"{API_URL}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"
    try:
        with urlopen(url, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", str(exc))
        except (json.JSONDecodeError, AttributeError):
            detail = str(exc)
        raise ApiError(f"API respondió {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ApiError(f"No se pudo leer {url}: {exc}") from exc


def _format_number(value: object, decimals: int = 0) -> str:
    try:
        formatted = f"{float(value):,.{decimals}f}"
        return formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "—"


def main(page: ft.Page) -> None:
    page.title = "BIA | Plataforma de datos"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = "#0B1220"
    page.padding = 28
    page.scroll = ft.ScrollMode.AUTO

    month_filter = ft.Dropdown(label="Mes", width=180, dense=True)
    border_filter = ft.Dropdown(label="Frontera", width=230, dense=True)
    status_text = ft.Text("Conectando con la API Docker...", color="#93A4B8")
    kpi_row = ft.Row(wrap=True, spacing=12, run_spacing=12)
    quality_row = ft.Row(wrap=True, spacing=12, run_spacing=12)
    consumption_notice = ft.Text(color="#FDB4A8")
    consumption_table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("Mes")),
            ft.DataColumn(ft.Text("Frontera")),
            ft.DataColumn(ft.Text("Consumo (kWh)"), numeric=True),
            ft.DataColumn(ft.Text("Horas"), numeric=True),
            ft.DataColumn(ft.Text("Mercado")),
            ft.DataColumn(ft.Text("Estado")),
        ],
        rows=[],
        column_spacing=22,
        heading_row_color="#172235",
        data_row_min_height=42,
        data_row_max_height=54,
    )
    alerts_table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("Fecha / hora")),
            ft.DataColumn(ft.Text("Frontera")),
            ft.DataColumn(ft.Text("Alerta")),
        ],
        rows=[],
        column_spacing=24,
        heading_row_color="#172235",
        data_row_min_height=40,
        data_row_max_height=52,
    )

    def card(title: str, value: str, note: str = "") -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(title, color="#93A4B8", size=13),
                    ft.Text(value, color="#F1F5F9", size=25, weight=ft.FontWeight.BOLD),
                    ft.Text(note, color="#708199", size=11) if note else ft.Container(height=0),
                ],
                spacing=7,
            ),
            padding=18,
            width=220,
            bgcolor="#111C2E",
            border=ft.Border.all(1, "#26364D"),
            border_radius=12,
        )

    def show_api_error(section: str, exc: Exception) -> ft.Container:
        return ft.Container(
            content=ft.Text(f"{section}: {exc}", color="#FDB4A8", selectable=True),
            padding=12,
            bgcolor="#351E27",
            border_radius=8,
        )

    def refresh(_: ft.ControlEvent | None = None) -> None:
        status_text.value = f"Consultando {API_URL}"
        status_text.color = "#93A4B8"
        kpi_row.controls.clear()
        quality_row.controls.clear()
        consumption_table.rows.clear()
        alerts_table.rows.clear()
        consumption_notice.value = ""

        try:
            metrics = _get_json("/api/indicadores")
            kpi_row.controls.extend(
                [
                    card("Consumo agregado", f"{_format_number(metrics['consumo_kwh'], 3)} kWh"),
                    card("Fronteras", _format_number(metrics["fronteras"])),
                    card("Meses", _format_number(metrics["meses"])),
                    card("Agregados mensuales", _format_number(metrics["registros"])),
                ]
            )
        except (ApiError, KeyError, TypeError) as exc:
            kpi_row.controls.append(show_api_error("Indicadores", exc))

        params = {}
        if month_filter.value:
            params["mes"] = month_filter.value
        if border_filter.value:
            params["frontera_id"] = border_filter.value
        try:
            rows = _get_json("/api/consumos", params).get("data", [])
            consumption_table.rows.extend(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(str(row["mes"]))),
                        ft.DataCell(ft.Text(str(row["frontera_id"]))),
                        ft.DataCell(ft.Text(_format_number(row["consumo_kwh"], 3))),
                        ft.DataCell(ft.Text(_format_number(row["horas_con_dato"]))),
                        ft.DataCell(ft.Text(str(row["mercado"]))),
                        ft.DataCell(ft.Text(str(row["estado"]))),
                    ]
                )
                for row in rows
            )
        except (ApiError, KeyError, TypeError) as exc:
            consumption_notice.value = f"Consumos: {exc}"

        try:
            quality = _get_json("/api/calidad")
            run_metrics = _get_json("/api/metricas")
            quality_row.controls.extend(
                [
                    card("Filas procesadas", _format_number(quality["filas_leidas"])),
                    card("Filas rechazadas", _format_number(run_metrics["filas_rechazadas"])),
                    card("Horas sin lectura válida", _format_number(quality["horas_sin_lectura_valida_total"])),
                    card("Facturas huérfanas", _format_number(quality["fronteras_huerfanas_facturacion"])),
                ]
            )
        except (ApiError, KeyError, TypeError) as exc:
            quality_row.controls.append(show_api_error("Calidad", exc))

        try:
            alerts = _get_json("/api/alertas", {"limit": "100"})
            for alert in alerts["data"]:
                alerts_table.rows.append(
                    ft.DataRow(
                        cells=[
                            ft.DataCell(ft.Text(str(alert["ts"]))),
                            ft.DataCell(ft.Text(str(alert["frontera_id"]))),
                            ft.DataCell(ft.Text(str(alert["tipo"]), color="#F6C177")),
                        ]
                    )
                )
            status_text.value = (
                f"API conectada · {alerts['total']} alertas · actualización manual"
            )
            status_text.color = "#7DD3A7"
        except (ApiError, KeyError, TypeError) as exc:
            status_text.value = f"No se pudieron cargar las alertas: {exc}"
            status_text.color = "#F6C177"

        page.update()

    def load_filters() -> None:
        try:
            catalogs = _get_json("/api/catalogos")
            month_filter.options = [ft.dropdown.Option(value) for value in catalogs["meses"]]
            border_filter.options = [ft.dropdown.Option(value) for value in catalogs["fronteras"]]
        except (ApiError, KeyError, TypeError) as exc:
            status_text.value = f"No se pudieron cargar los filtros: {exc}"
            status_text.color = "#FDB4A8"

    page.add(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Column(
                            [
                                ft.Text("BIA · Centro de datos", size=29, weight=ft.FontWeight.BOLD),
                                ft.Text("Consumo, calidad y alertas del pipeline", color="#93A4B8"),
                            ],
                            spacing=4,
                        ),
                        ft.Container(expand=True),
                        ft.Button(
                            content="Actualizar",
                            icon=ft.Icons.REFRESH,
                            on_click=refresh,
                        ),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                status_text,
                ft.Divider(color="#26364D"),
                ft.Row(
                    [month_filter, border_filter],
                    wrap=True,
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.END,
                ),
                kpi_row,
                ft.Text("Consumo mensual", size=19, weight=ft.FontWeight.BOLD),
                ft.Container(
                    content=ft.Column(
                        [
                            consumption_notice,
                            ft.Row([consumption_table], scroll=ft.ScrollMode.AUTO),
                        ]
                    ),
                    padding=8,
                    bgcolor="#111C2E",
                    border_radius=12,
                ),
                ft.Text("Calidad de datos", size=19, weight=ft.FontWeight.BOLD),
                quality_row,
                ft.Text("Alertas de streaming", size=19, weight=ft.FontWeight.BOLD),
                ft.Container(
                    content=ft.Row([alerts_table], scroll=ft.ScrollMode.AUTO),
                    padding=8,
                    bgcolor="#111C2E",
                    border_radius=12,
                ),
                ft.Text(
                    f"Fuente: API de solo lectura · {API_URL}",
                    size=11,
                    color="#708199",
                ),
            ],
            spacing=18,
        )
    )
    load_filters()
    month_filter.on_change = refresh
    border_filter.on_change = refresh
    refresh()


if __name__ == "__main__":
    ft.run(
        main,
        view=ft.AppView.WEB_BROWSER,
        host="127.0.0.1",
        port=8550,
    )
