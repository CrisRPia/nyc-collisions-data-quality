"""Spanish presentation helpers; source frames and exported data stay unchanged."""

from html import escape
from math import isnan
import re
from typing import TypedDict

import pandas as pd
from IPython.display import HTML
from pydantic import TypeAdapter


class CountPair(TypedDict):
    eligible: float | None
    exceptions: float | None


COUNT_PAIRS_ADAPTER = TypeAdapter(list[CountPair])

DIMENSIONS_ES = {
    "Completeness": "Completitud", "Consistency": "Consistencia",
    "Accuracy": "Exactitud", "Uniqueness": "Unicidad",
    "Validity": "Validez", "Timeliness": "Oportunidad",
}
STATES_ES = {"Cleaned": "Limpio", "Raw": "Original"}
FLAGS_ES = {
    "person_role_total_difference_injured": "Diferencia en lesionados por rol",
    "person_role_total_difference_killed": "Diferencia en fallecidos por rol",
    "coordinates_unknown": "Coordenadas desconocidas",
    "location_coordinate_conflict": "Conflicto en las coordenadas de location",
    "coordinates_outside_nyc": "Coordenadas fuera del dominio NYC",
    "coordinates_invalid": "Coordenadas inválidas",
    "coordinates_partial": "Coordenadas incompletas",
    "id_missing": "ID faltante", "id_duplicate": "ID repetido",
    "crash_date_invalid": "Fecha fuera del dominio", "crash_time_invalid": "Hora inválida",
    "count_invalid": "Conteo inválido", "borough_invalid": "Distrito inválido", "zip_invalid": "Código postal inválido",
    "role_subtotal_exceeds_total_injured": "Subtotal de lesionados mayor al total",
    "role_subtotal_exceeds_total_killed": "Subtotal de fallecidos mayor al total",
    "borough_unknown": "Distrito desconocido",
    "borough_coordinate_conflict": "Conflicto de distrito y coordenadas",
    "possible_duplicate_event": "Posible hecho duplicado",
}
RULES_ES = {
    "C01": "Presencia de ID, fecha, hora y totales de personas",
    "C02": "Distrito disponible para el análisis geográfico",
    "C03": "Presencia de ambas coordenadas",
    "C04": "Cobertura de todos los siniestros reales dentro del alcance",
    "C05": "Coordenadas presentes dentro del dominio NYC",
    "V01": "Fechas interpretables dentro del año 2025",
    "V02": "Horas interpretables como hora local",
    "V03": "Ocho conteos de lesiones y fallecimientos enteros y no negativos",
    "V04": "Coordenadas informadas finitas y dentro de los rangos globales",
    "V05": "Coordenadas informadas distintas de (0,0)",
    "V06": "Distrito informado dentro del dominio de cinco nombres",
    "V07": "Código postal informado como texto de cinco dígitos",
    "V08": "Coordenadas dentro del dominio NYC más el margen configurado",
    "S01": "Latitud y longitud informadas conjuntamente",
    "S02": "Coincidencia entre las coordenadas y el objeto location",
    "S03": "Etiquetas de vehículos según la forma predominante de cada grupo",
    "S04_injured": "Ningún subtotal de lesionados por rol supera el total de personas",
    "S04_killed": "Ningún subtotal de fallecidos por rol supera el total de personas",
    "R01_injured": "Comparación del total de lesionados con la suma de los tres roles",
    "R01_killed": "Comparación del total de fallecidos con la suma de los tres roles",
    "U01": "Unicidad de los IDs no vacíos dentro de la copia",
    "U02": "Coincidencias excedentes de valores distintos del ID",
    "A01": "Correspondencia de hechos, conteos y posiciones con la realidad verificada",
    "S05": "Coincidencia del distrito suministrado con el polígono oficial elegible",
    "R02": "Coordenadas válidas y no nulas dentro de la extensión de referencia",
    "T01": "Demora hasta la primera publicación dentro de un plazo acordado",
    "T02": "Adecuación de la copia al análisis histórico anual de 2025",
}
INTERPRETATIONS_ES = {
    "conformance": "Conformidad", "analytical_gap": "Faltante para el uso analítico",
    "coverage": "Cobertura", "not_verified": "Sin verificación independiente",
    "proposed_standardization": "Estandarización propuesta",
    "unconfirmed_review_signal": "Indicio pendiente de revisión",
    "candidate_screen": "Detección de candidatos", "reference_comparison": "Comparación con referencia",
    "geographic_review_signal": "Indicio geográfico de revisión",
    "not_measurable": "Sin evidencia suficiente para medir",
    "historical_scope": "Alcance histórico", "not_applicable": "No aplicable",
    "usable_coordinate_coverage": "Cobertura de coordenadas utilizables",
}
TEXT_ES = {
    **DIMENSIONS_ES, **INTERPRETATIONS_ES,
    "raw": "Original", "cleaned": "Limpio", "error": "Infracción", "review": "Revisión",
    "records": "registros", "cells": "celdas",
    "redundant_location_objects_removed": "Objetos location redundantes eliminados",
    "missing_fields_made_explicit_null": "Campos ausentes representados como nulos",
    "blank_strings_to_null": "Cadenas vacías convertidas en nulos",
    "text_values_trimmed": "Textos sin espacios en los extremos",
    "count_values_typed": "Conteos convertidos a enteros",
    "coordinate_values_typed": "Coordenadas convertidas a números",
    "date_formats_normalized": "Fechas normalizadas",
    "time_formats_normalized": "Horas normalizadas",
    "zero_coordinates_to_null": "Pares (0,0) convertidos en nulos",
    "borough_derived": "Distritos derivados de coordenadas",
    "vehicle_case_normalized": "Etiquetas de vehículos estandarizadas",
    "Coordinates outside NYC plus the configured margin": "Coordenadas fuera del dominio NYC más el margen configurado",
    "Missing borough for the selected geographic analysis": "Falta el distrito requerido para el análisis geográfico elegido",
    "(0,0) is not a usable NYC crash location; retain event and flag location as unknown": "(0,0) no permite ubicar el siniestro en Nueva York; se conserva el hecho y se marca la ubicación como desconocida",
    "Nonmodal spelling within an exact trim/casefold group; proposed representation standard": "Etiqueta distinta de la forma predominante del mismo grupo trim/casefold; estandarización propuesta",
    "Person total differs from the three-role sum; exhaustiveness unconfirmed": "Diferencia entre el total de personas y la suma de los tres roles; revisar su cobertura",
    "(0,0) does not identify a NYC crash location": "(0,0) no identifica una ubicación de siniestro en Nueva York",
    "Unique official polygon interior, away from the approximate boundary band; assumes correct source coordinates": "Interior único de un polígono oficial, fuera de la franja aproximada de borde; se presuponen coordenadas correctas",
    "Most frequent spelling within the same casefold group": "Forma más frecuente dentro del mismo grupo casefold",
}
HEADERS_ES = {
    "source_row": "Fila de origen (base 0)", "stage": "Estado", "severity": "Tipo",
    "expected": "Regla esperada", "observed_json": "Valor observado (JSON)",
    "south": "Latitud mínima", "north": "Latitud máxima", "west": "Longitud mínima", "east": "Longitud máxima", "margin_km": "Margen aprox. (km)",
    "rule_id": "Regla", "dimension": "Dimensión", "rule": "Descripción de la regla",
    "eligible": "Elegibles", "exceptions": "Excepciones", "conforming_pct": "Conformidad (%)",
    "interpretation": "Interpretación", "note": "Alcance y limitaciones", "unit": "Unidad",
    "field": "Campo", "python_types": "Tipos observados", "missing": "Faltantes",
    "collision_id": "collision_id", "classification": "Clasificación", "observed_value": "Valor observado",
    "finding": "Hallazgo", "operation": "Operación", "cells_or_records": "Celdas o registros",
    "changed_cells_or_records": "Celdas o registros modificados", "before_json": "Antes (JSON)",
    "after_json": "Después (JSON)", "reason": "Motivo", "before": "Antes", "after": "Después",
    "before_type": "Tipo anterior", "after_type": "Tipo posterior",
    "before_conforming_pct": "Antes: conformidad (%)", "after_conforming_pct": "Después: conformidad (%)",
    "change_percentage_points": "Variación (puntos porcentuales)",
    "file": "Archivo", "observed_at_utc": "Adquisición (UTC)", "url": "Fuente", "sha256": "SHA-256",
}
STYLE = """
<style>
.dq-table-wrap {max-width:100%; overflow-x:auto; margin:12px 0 20px;}
.dq-table {width:100%; border-collapse:collapse; table-layout:fixed; font-size:13px; line-height:1.5;}
.dq-table th,.dq-table td {text-align:left!important; vertical-align:top; padding:9px 10px!important;
  white-space:normal!important; overflow-wrap:anywhere; border-bottom:1px solid #dce3e6;}
.dq-table th {background:#edf4f5; color:#203b44; font-weight:600;}
.dq-table tbody tr:nth-child(even) {background:rgba(128,151,164,.07);}
.dq-table caption {text-align:left; font-weight:600; margin-bottom:8px; color:inherit;}
.dq-details {margin:12px 0; padding:8px 12px; border-left:3px solid #16887b;}
.dq-details summary {cursor:pointer; font-weight:600;}
.dq-details p {line-height:1.6;}
</style>
"""


def translated_text(value: object) -> object:
    if not isinstance(value, str):
        return value
    if value in TEXT_ES:
        return TEXT_ES[value]
    duplicate = re.fullmatch(r"All non-ID values match (\d+) other record\(s\), group (\w+); review event identity", value)
    if duplicate:
        return f"Coincidencia de todos los valores distintos del ID con otros {duplicate[1]} registros; grupo {duplicate[2]}. Revisar la identidad del hecho."
    conflict = re.fullmatch(r"Reported borough differs from polygon-derived (.+); coordinate accuracy unverified", value)
    if conflict:
        return f"El distrito informado difiere de {conflict[1]}, obtenido del polígono; revisar las coordenadas"
    return value


def number(value: float) -> str:
    """Spanish separators without changing the process-wide locale."""
    formatted = f"{value:,.0f}" if value.is_integer() else f"{value:,.3f}"
    return formatted.translate(str.maketrans({",": ".", ".": ","}))


def table(frame: pd.DataFrame, *, caption: str = "", missing: str = "nulo") -> HTML:
    view = frame.copy()
    for column in ("dimension", "interpretation", "classification", "operation", "reason", "finding", "unit", "stage", "severity"):
        if column in view:
            view[column] = view[column].map(translated_text)
    if "rule_id" in view and "rule" in view:
        view["rule"] = view["rule_id"].map(RULES_ES)
    elif "rule" in view:
        view["rule"] = view["rule"].map(translated_text)
    view = view.rename(columns=HEADERS_ES)
    markup = view.to_html(index=False, escape=True, na_rep=missing, float_format=number,
                          max_rows=None, max_cols=None, border=0, classes="dq-table")
    if caption:
        markup = markup.replace("<thead>", f"<caption>{escape(caption)}</caption>\n<thead>", 1)
    # Only full, exceptionally wide data views need a horizontal scrollbar.
    min_width = max(0, len(view.columns) - 8) * 110 + 760 if len(view.columns) > 8 else 0
    if min_width:
        markup = markup.replace('<table ', f'<table style="min-width:{min_width}px" ', 1)
    return HTML(STYLE + '<div class="dq-table-wrap">' + markup + "</div>")


def record_preview(frame: pd.DataFrame, *, caption: str = "Vista de registros") -> HTML:
    fields = [field for field in ("collision_id", "crash_date", "borough", "number_of_persons_injured",
                                  "number_of_persons_killed", "latitude", "longitude") if field in frame]
    compact = table(frame[fields], caption=caption).data
    full = table(frame, caption="Todos los campos de la selección").data
    return HTML(compact + '<details class="dq-details"><summary>Ver todos los campos de estos registros</summary>'
                + full + "</details>")


def comparison_table(frame: pd.DataFrame) -> HTML:
    view = frame[["rule_id", "dimension", "unit"]].copy()
    view.insert(1, "rule", frame["rule_id"].map(RULES_ES))
    for state, label in (("before", "Antes"), ("after", "Después")):
        # Both the exception count and its eligible denominator remain visible.
        rows = COUNT_PAIRS_ADAPTER.validate_python(
            frame[[f"{state}_eligible", f"{state}_exceptions"]].rename(columns={
                f"{state}_eligible": "eligible", f"{state}_exceptions": "exceptions",
            }).to_dict(orient="records"), strict=True)
        counts: list[str] = []
        for row in rows:
            eligible, exceptions = row["eligible"], row["exceptions"]
            if eligible is None or exceptions is None or isnan(eligible) or isnan(exceptions):
                counts.append("Sin medición / no aplicable")
            else:
                counts.append(f"{number(exceptions)} / {number(eligible)}")
        view[f"{label}: excepciones / elegibles"] = counts
        view[f"{state}_conforming_pct"] = frame[f"{state}_conforming_pct"]
    view["change_percentage_points"] = frame["change_percentage_points"]
    markup = table(view, caption="Comparación por regla", missing="Sin medición / no aplicable").data
    widths = (6, 28, 10, 6, 11, 9, 11, 9, 10)
    columns = "<colgroup>" + "".join(f'<col style="width:{width}%">' for width in widths) + "</colgroup>"
    return HTML(markup.replace("<thead>", columns + "\n<thead>", 1))
