"""Convierte las observaciones sueltas en una serie diaria de precio de competencia.

Por día, producto y plataforma:
  - si hubo coincidencias exactas se usan solo esas; si no, las equivalentes
  - con 5 o más precios se descartan atípicos por rango intercuartílico (1.5 x IQR)
  - se guarda la mediana (robusta a publicaciones raras), el mínimo y cuántos precios hubo
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine

from ..db import Observacion, PrecioCompetenciaDiario, sesion


def _sin_atipicos(precios: pd.Series) -> pd.Series:
    if len(precios) < 5:
        return precios
    q1, q3 = precios.quantile([0.25, 0.75])
    iqr = q3 - q1
    return precios[(precios >= q1 - 1.5 * iqr) & (precios <= q3 + 1.5 * iqr)]


def agregar_diario(engine: Engine) -> int:
    consulta = select(
        Observacion.capturado_en, Observacion.producto_id, Observacion.plataforma_id,
        Observacion.precio_mxn, Observacion.match_nivel,
    ).where(Observacion.match_nivel.is_not(None))
    with engine.connect() as con:
        df = pd.read_sql(consulta, con)
    if df.empty:
        return 0

    df["fecha"] = pd.to_datetime(df["capturado_en"]).dt.date
    filas = []
    for (fecha, producto, plataforma), g in df.groupby(["fecha", "producto_id", "plataforma_id"]):
        nivel = "exacto" if (g["match_nivel"] == "exacto").any() else "equivalente"
        precios = _sin_atipicos(g.loc[g["match_nivel"] == nivel, "precio_mxn"])
        if precios.empty:
            continue
        filas.append(PrecioCompetenciaDiario(
            fecha=fecha, producto_id=producto, plataforma_id=plataforma, fuente="scraping",
            precio_mediana_mxn=round(float(precios.median()), 2),
            precio_min_mxn=round(float(precios.min()), 2),
            n_obs=int(len(precios)), nivel_match=nivel,
        ))

    fechas = sorted({f.fecha for f in filas})
    with sesion(engine) as s:
        s.execute(delete(PrecioCompetenciaDiario).where(
            PrecioCompetenciaDiario.fuente == "scraping",
            PrecioCompetenciaDiario.fecha.in_(fechas),
        ))
        s.add_all(filas)
        s.commit()
    return len(filas)
