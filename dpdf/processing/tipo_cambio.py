"""Conversión a MXN.

USD -> MXN usa el tipo de cambio FIX de Banxico (serie SF43718) si hay BANXICO_TOKEN en .env
(el token es gratuito: https://www.banxico.org.mx/SieAPIRest/). Si no hay token o la API no
responde, usa TIPO_CAMBIO_USD_RESPALDO. Cada observación guarda qué tipo de cambio se usó y de
dónde salió, para que el dato sea trazable.
"""
from __future__ import annotations

import os
from functools import lru_cache

import httpx

URL_BANXICO_FIX = "https://www.banxico.org.mx/SieAPIRest/service/v1/series/SF43718/datos/oportuno"


@lru_cache(maxsize=4)
def _fix_banxico(token: str) -> float | None:
    try:
        r = httpx.get(URL_BANXICO_FIX, headers={"Bmx-Token": token}, timeout=15)
        r.raise_for_status()
        dato = r.json()["bmx"]["series"][0]["datos"][-1]["dato"]
        return float(dato.replace(",", ""))
    except Exception:
        return None


def obtener_tipo_cambio(moneda: str) -> tuple[float, str]:
    """Regresa (pesos por unidad de `moneda`, fuente)."""
    moneda = (moneda or "MXN").upper()
    if moneda == "MXN":
        return 1.0, "mxn"
    if moneda == "USD":
        token = os.getenv("BANXICO_TOKEN", "").strip()
        if token:
            valor = _fix_banxico(token)
            if valor:
                return valor, "banxico_fix"
        return float(os.getenv("TIPO_CAMBIO_USD_RESPALDO", "18.50")), "respaldo_env"
    if moneda == "EUR":
        return float(os.getenv("TIPO_CAMBIO_EUR_RESPALDO", "21.00")), "respaldo_env"
    raise ValueError(f"Moneda no soportada: {moneda}")
