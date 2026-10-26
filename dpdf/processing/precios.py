"""Homologación de precios: texto de la página -> número + moneda.

Casos que maneja:
    "$1,299.00"          -> 1299.00 MXN
    "MX$1.299,00"        -> 1299.00 MXN   (formato europeo)
    "US $12.34"          -> 12.34 USD
    "US$2.50 - 3.10"     -> 2.50 a 3.10 USD (rango, típico de Alibaba)
    1299 (número)        -> 1299.00
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_RE_NUMERO = re.compile(
    r"\d{1,3}(?:[.,\s  ]\d{3})+(?:[.,]\d{1,2})?"   # con separador de miles
    r"|\d+(?:[.,]\d{1,2})?"                                  # sin separador de miles
)
_RE_RANGO = re.compile(r"\d\s*(?:-|–|—|~|a)\s*\D{0,5}\d")


@dataclass
class Precio:
    valor: float              # precio mínimo (o único)
    valor_max: float | None   # solo si era un rango
    moneda: str
    texto: str


def detectar_moneda(texto: str, por_defecto: str = "MXN") -> str:
    t = texto.upper().replace(" ", "")
    if "US$" in t or "USD" in t:
        return "USD"
    if "MX$" in t or "MXN" in t or "MN$" in t:
        return "MXN"
    if "€" in t or "EUR" in t:
        return "EUR"
    return por_defecto


def _a_float(token: str) -> float | None:
    t = re.sub(r"[\s  ]", "", token)
    if not t:
        return None
    if "," in t and "." in t:
        # el último separador es el decimal
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        partes = t.split(",")
        if len(partes) == 2 and len(partes[1]) in (1, 2):
            t = t.replace(",", ".")          # 12,50 -> decimal
        else:
            t = t.replace(",", "")           # 1,299 -> miles
    elif "." in t:
        partes = t.split(".")
        if len(partes) > 2 or (len(partes) == 2 and len(partes[1]) == 3):
            t = t.replace(".", "")           # 1.299 -> miles
    try:
        return float(t)
    except ValueError:
        return None


def parsear_precio(valor: str | int | float | None, moneda_defecto: str = "MXN") -> Precio | None:
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return Precio(float(valor), None, moneda_defecto, str(valor)) if valor > 0 else None

    texto = str(valor).strip()
    if not texto:
        return None
    numeros = [n for n in (_a_float(m.group()) for m in _RE_NUMERO.finditer(texto)) if n and n > 0]
    if not numeros:
        return None
    moneda = detectar_moneda(texto, moneda_defecto)
    if len(numeros) >= 2 and _RE_RANGO.search(texto):
        a, b = numeros[0], numeros[1]
        return Precio(min(a, b), max(a, b), moneda, texto)
    return Precio(numeros[0], None, moneda, texto)
