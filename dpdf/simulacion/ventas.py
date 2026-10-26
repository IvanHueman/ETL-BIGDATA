"""Generador de ventas sintéticas de una tienda en línea mexicana ("TechStore MX").

¿Por qué simular? No hay ventas reales de los 10 productos y el scraping solo empieza a
acumular historia desde hoy. La simulación da un historial diario realista para construir y
probar el pipeline de pronóstico, y como los parámetros se conocen (se guardan en
data/clean/verdad_simulacion.json) sirve para validar si el modelo de ML recupera, por ejemplo,
la elasticidad precio. Cuando haya ventas reales, se cargan en la tabla `ventas` con otra fuente.

Demanda esperada por día (modelo log-lineal):
  log λ = log(base) + crecimiento·años + día de la semana + eventos + quincena
          + ε_propia · log(precio_propio / precio_referencia)
          + ε_relativa · log(precio_propio / precio_competencia)
  unidades ~ Poisson(λ · Gamma(k, 1/k))            (sobredispersión, binomial negativa)

Eventos del calendario comercial mexicano: Hot Sale, El Buen Fin, Navidad, Reyes,
cuesta de enero, regreso a clases, Día del Padre y quincenas.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import delete, insert
from sqlalchemy.engine import Engine

from ..db import PrecioCompetenciaDiario, Venta, sesion

INFLACION_ANUAL = 0.04
DISPERSION_K = 10.0
SEMANAL = np.array([1.05, 1.06, 1.02, 1.00, 0.98, 0.92, 0.97])   # lunes..domingo
MULTIPLICADORES = {          # efecto de calendario (además del efecto del precio)
    "hot_sale": 1.6, "buen_fin": 1.9, "navidad": 1.7, "reyes": 1.2,
    "cuesta_enero": 0.88, "quincena": 1.08,
}
DESCUENTO_PROPIO = {"hot_sale": 0.85, "buen_fin": 0.82, "promo": 0.90}
DESCUENTO_COMPETENCIA = {"hot_sale": 0.88, "buen_fin": 0.86}
NIVEL_PLATAFORMA = {"mercadolibre": 1.00, "amazon": 0.97, "aliexpress": 0.78, "temu": 0.62}
PROMOS_POR_ANIO = 6


# ---------------------------------------------------------------- calendario

def _ultimo_lunes_mayo(anio: int) -> date:
    d = date(anio, 5, 31)
    return d - timedelta(days=d.weekday())


def _tercer_dia_semana(anio: int, mes: int, dia_semana: int) -> date:
    d = date(anio, mes, 1)
    return d + timedelta(days=(dia_semana - d.weekday()) % 7 + 14)


def calendario(fechas: pd.DatetimeIndex) -> pd.DataFrame:
    cal = pd.DataFrame(index=fechas)
    for col in ("hot_sale", "buen_fin", "navidad", "reyes", "cuesta_enero",
                "regreso_clases", "dia_padre", "quincena"):
        cal[col] = 0.0
    for anio in sorted(set(fechas.year)):
        hs = _ultimo_lunes_mayo(anio)                          # Hot Sale: 9 días desde el último lunes de mayo
        cal.loc[str(hs):str(hs + timedelta(days=8)), "hot_sale"] = 1
        lunes_revolucion = _tercer_dia_semana(anio, 11, 0)     # El Buen Fin: viernes a lunes del puente
        cal.loc[str(lunes_revolucion - timedelta(days=3)):str(lunes_revolucion), "buen_fin"] = 1
        dias = pd.date_range(f"{anio}-12-01", f"{anio}-12-24")
        dias = dias[dias.isin(fechas)]
        cal.loc[dias, "navidad"] = (dias.day - 1) / 23         # rampa de 0 a 1
        cal.loc[f"{anio}-01-01":f"{anio}-01-06", "reyes"] = 1
        cal.loc[f"{anio}-01-07":f"{anio}-01-31", "cuesta_enero"] = 1
        cal.loc[f"{anio}-08-01":f"{anio}-08-25", "regreso_clases"] = 1
        dp = _tercer_dia_semana(anio, 6, 6)                    # tercer domingo de junio
        cal.loc[str(dp - timedelta(days=7)):str(dp), "dia_padre"] = 1
    dia = fechas.day
    cal["quincena"] = ((dia >= 15) & (dia <= 17) | (dia <= 2) | fechas.is_month_end).astype(float)
    return cal


def _etiqueta(cal: pd.DataFrame, promo: np.ndarray) -> np.ndarray:
    etiqueta = np.full(len(cal), None, dtype=object)
    prioridad = ["cuesta_enero", "reyes", "regreso_clases", "dia_padre", "navidad", "hot_sale", "buen_fin"]
    etiqueta[promo > 0] = "promo"
    for col in prioridad:                                  # el último gana
        etiqueta[cal[col].to_numpy() > 0] = col
    return etiqueta


# ---------------------------------------------------------------- simulación

def _rng(semilla: int, *claves: int) -> np.random.Generator:
    return np.random.default_rng([semilla, *claves])


def simular(productos: list[dict], desde: date, hasta: date, semilla: int = 42):
    fechas = pd.date_range(desde, hasta, freq="D")
    cal = calendario(fechas)
    n = len(fechas)
    anios = np.asarray((fechas - pd.Timestamp(desde)).days, dtype=float) / 365.25
    ventas, competencia, verdad = [], [], {"semilla": semilla, "desde": str(desde), "hasta": str(hasta),
                                           "inflacion_anual": INFLACION_ANUAL, "productos": {}}

    for idx, p in enumerate(productos):
        sim = p["simulacion"]
        base = float(p["precio_base_mxn"])
        precio_ref = base * (1 + INFLACION_ANUAL) ** anios

        # promociones propias: 6 semanas al año en fechas aleatorias (por año, para que
        # alargar el rango de fechas no cambie la historia ya generada)
        promo = np.zeros(n)
        for anio in sorted(set(fechas.year)):
            inicios = _rng(semilla, idx, 100, anio).choice(365 - 7, size=PROMOS_POR_ANIO, replace=False)
            for ini in inicios:
                dias = pd.date_range(pd.Timestamp(anio, 1, 1) + pd.Timedelta(days=int(ini)), periods=7)
                promo[fechas.isin(dias)] = 1

        descuento = np.ones(n)
        descuento[promo > 0] = DESCUENTO_PROPIO["promo"]
        descuento[cal["hot_sale"].to_numpy() > 0] = DESCUENTO_PROPIO["hot_sale"]
        descuento[cal["buen_fin"].to_numpy() > 0] = DESCUENTO_PROPIO["buen_fin"]
        precio_propio = np.round(precio_ref * descuento / 10) * 10 - 1   # precios terminados en 9

        # índice de precio de la competencia: AR(1) en logaritmos + descuentos de temporada
        ruido = _rng(semilla, idx, 1).normal(0, 0.015, n)
        x = np.zeros(n)
        for t in range(1, n):
            x[t] = 0.97 * x[t - 1] + ruido[t]
        desc_comp = np.ones(n)
        desc_comp[cal["hot_sale"].to_numpy() > 0] = DESCUENTO_COMPETENCIA["hot_sale"]
        desc_comp[cal["buen_fin"].to_numpy() > 0] = DESCUENTO_COMPETENCIA["buen_fin"]
        precio_comp = base * 0.97 * (1 + INFLACION_ANUAL) ** anios * np.exp(x) * desc_comp

        log_lam = (
            np.log(sim["demanda_base"])
            + np.log1p(sim.get("crecimiento_anual", 0.05)) * anios
            + np.log(SEMANAL[np.asarray(fechas.dayofweek)])
            + 0.06 * np.cos(2 * np.pi * (np.asarray(fechas.dayofyear) - 350) / 365.25)
            + sum(np.log(m) * cal[c].to_numpy() for c, m in MULTIPLICADORES.items())
            + np.log(sim.get("regreso_clases", 1.0)) * cal["regreso_clases"].to_numpy()
            + np.log(sim.get("dia_padre", 1.0)) * cal["dia_padre"].to_numpy()
            + sim["elasticidad_propia"] * np.log(precio_propio / precio_ref)
            + sim["elasticidad_relativa"] * np.log(precio_propio / precio_comp)
        )
        lam = np.exp(log_lam) * _rng(semilla, idx, 2).gamma(DISPERSION_K, 1 / DISPERSION_K, n)
        unidades = _rng(semilla, idx, 3).poisson(lam)
        etiquetas = _etiqueta(cal, promo)

        ventas.append(pd.DataFrame({
            "fecha": fechas.date, "producto_id": p["id"], "unidades": unidades.astype(int),
            "precio_unitario_mxn": precio_propio.round(2),
            "ingresos_mxn": (unidades * precio_propio).round(2),
            "evento": etiquetas, "fuente": "simulado",
        }))

        for k, (plat, nivel) in enumerate(NIVEL_PLATAFORMA.items()):
            ruido_plat = np.exp(_rng(semilla, idx, 10 + k).normal(0, 0.02, n))
            mediana = precio_comp * nivel * ruido_plat
            competencia.append(pd.DataFrame({
                "fecha": fechas.date, "producto_id": p["id"], "plataforma_id": plat, "fuente": "simulado",
                "precio_mediana_mxn": mediana.round(2), "precio_min_mxn": (mediana * 0.93).round(2),
                "n_obs": 0, "nivel_match": "simulado",
            }))

        verdad["productos"][p["id"]] = {
            "precio_base_mxn": base, **sim,
            "efecto_calendario": MULTIPLICADORES,
            "nivel_precio_plataforma": NIVEL_PLATAFORMA,
        }

    return pd.concat(ventas, ignore_index=True), pd.concat(competencia, ignore_index=True), verdad


def guardar(engine: Engine, ventas: pd.DataFrame, competencia: pd.DataFrame,
            verdad: dict, dir_salida: Path) -> None:
    with sesion(engine) as s:
        s.execute(delete(Venta).where(Venta.fuente == "simulado"))
        s.execute(delete(PrecioCompetenciaDiario).where(PrecioCompetenciaDiario.fuente == "simulado"))
        for df, tabla in ((ventas, Venta), (competencia, PrecioCompetenciaDiario)):
            registros = df.astype(object).where(df.notna(), None).to_dict("records")
            for i in range(0, len(registros), 5000):
                s.execute(insert(tabla), registros[i:i + 5000])
        s.commit()
    dir_salida.mkdir(parents=True, exist_ok=True)
    with open(dir_salida / "verdad_simulacion.json", "w", encoding="utf-8") as f:
        json.dump(verdad, f, ensure_ascii=False, indent=2)
