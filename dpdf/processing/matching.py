"""Relaciona cada publicación encontrada con un producto del catálogo.

Dos niveles (ver config/productos.yaml):
    exacto       la publicación es el modelo de referencia
    equivalente  misma categoría y especificación, otra marca o genérico
El score (0-100, rapidfuzz.token_set_ratio contra el modelo de referencia) sirve para
ordenar y auditar; la decisión la toman las reglas, que son más predecibles que un umbral.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9.]+", " ", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)                 # puntos solo entre dígitos ("5.3")
    t = re.sub(r"(?<=\d)(?=[a-z])|(?<=[a-z])(?=\d)", " ", t)  # "10000mah" -> "10000 mah"
    return re.sub(r"\s+", " ", t).strip()


def contiene(texto_norm: str, termino: str) -> bool:
    term = normalizar(termino)
    if not term:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", texto_norm) is not None


def _cumple_grupos(texto_norm: str, grupos: list[list[str]]) -> bool:
    return all(any(contiene(texto_norm, alt) for alt in grupo) for grupo in grupos or [])


@dataclass
class ResultadoMatch:
    nivel: str | None     # exacto | equivalente | None
    score: float
    motivo: str


def evaluar(titulo: str, precio_mxn: float | None, producto: dict, es_mayoreo: bool = False) -> ResultadoMatch:
    t = normalizar(titulo)
    reglas = producto.get("match", {})
    score = float(fuzz.token_set_ratio(t, normalizar(producto["modelo_referencia"])))

    for palabra in reglas.get("excluir", []):
        if contiene(t, palabra):
            return ResultadoMatch(None, score, f"excluido por '{palabra}'")

    def en_rango(clave: str) -> bool:
        if es_mayoreo or precio_mxn is None:   # mayoreo: precios por pieza, no comparables
            return True
        lo, hi = reglas.get(clave, [0, float("inf")])
        return lo <= precio_mxn <= hi

    marcas = reglas.get("marca", [])
    marca_ok = not marcas or any(contiene(t, m) for m in marcas)
    modelo_ok = bool(reglas.get("modelo")) and _cumple_grupos(t, reglas["modelo"])
    excluye_modelo = any(contiene(t, p) for p in reglas.get("excluir_modelo", []))

    if marca_ok and modelo_ok and not excluye_modelo:
        if en_rango("rango_exacto_mxn"):
            return ResultadoMatch("exacto", score, "marca y modelo")
        # precio fuera de rango con el modelo correcto: suele ser accesorio, lote o error
        return ResultadoMatch(None, score, "modelo correcto pero precio fuera de rango")

    if _cumple_grupos(t, reglas.get("categoria", [])):
        if en_rango("rango_equivalente_mxn"):
            return ResultadoMatch("equivalente", score, "categoría y especificación")
        return ResultadoMatch(None, score, "categoría correcta pero precio fuera de rango")

    return ResultadoMatch(None, score, "no corresponde al producto")
