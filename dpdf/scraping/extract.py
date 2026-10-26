"""Extractores genéricos que sirven para cualquier plataforma.

Orden en que los usa cada adaptador (ver base.py):
  1. selectores propios de la plataforma        (más precisos, pero se rompen si cambia el HTML)
  2. JSON-LD de Schema.org (Product / ItemList)  (marcado estructurado, estable)
  3. JSON incrustado en <script>                (window.runParams, __NEXT_DATA__, rawData...)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup


@dataclass
class ItemCrudo:
    titulo: str
    precio_texto: str | None = None
    precio_num: float | None = None
    moneda: str | None = None
    url: str | None = None
    id_externo: str | None = None
    vendidos_texto: str | None = None
    envio_texto: str | None = None
    vendedor: str | None = None
    notas: str | None = None
    patrocinado: bool = False
    extra: dict = field(default_factory=dict)


def texto(el) -> str | None:
    if el is None:
        return None
    t = " ".join(el.get_text(" ", strip=True).split())
    return t or None


def deduplicar(items: list[ItemCrudo]) -> list[ItemCrudo]:
    vistos, salida = set(), []
    for it in items:
        clave = it.id_externo or (it.titulo.lower(), it.precio_texto or it.precio_num)
        if clave in vistos:
            continue
        vistos.add(clave)
        salida.append(it)
    return salida


# ---------------------------------------------------------------- JSON-LD (Schema.org)

def _tipos(nodo: dict) -> set[str]:
    t = nodo.get("@type", [])
    return {t} if isinstance(t, str) else set(t or [])


def _recorrer(nodo: Any) -> Iterator[dict]:
    if isinstance(nodo, dict):
        yield nodo
        for v in nodo.values():
            yield from _recorrer(v)
    elif isinstance(nodo, list):
        for v in nodo:
            yield from _recorrer(v)


def desde_jsonld(soup: BeautifulSoup, url_base: str) -> list[ItemCrudo]:
    items: list[ItemCrudo] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            datos = json.loads(script.string or script.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for nodo in _recorrer(datos):
            if "Product" not in _tipos(nodo) or not nodo.get("name"):
                continue
            ofertas = nodo.get("offers") or {}
            if isinstance(ofertas, list):
                ofertas = ofertas[0] if ofertas else {}
            precio = ofertas.get("price") or ofertas.get("lowPrice")
            if precio is None:
                continue
            alto = ofertas.get("highPrice")
            items.append(ItemCrudo(
                titulo=str(nodo["name"]).strip(),
                precio_texto=f"{precio} - {alto}" if alto and alto != precio else str(precio),
                moneda=ofertas.get("priceCurrency"),
                url=urljoin(url_base, nodo.get("url") or ofertas.get("url") or "") or None,
                id_externo=str(nodo.get("sku") or nodo.get("productID") or "") or None,
            ))
    return deduplicar(items)


# ---------------------------------------------------------------- JSON incrustado

_CLAVES_TITULO = ("displayTitle", "title", "goods_name", "goodsName", "subject", "productTitle", "name")
_CLAVES_ID = ("productId", "goods_id", "goodsId", "itemId", "offerId", "item_id", "id")
_CLAVES_URL = ("productDetailUrl", "detailUrl", "link_url", "linkUrl", "url", "link", "seo_link_url")
_RE_ASIGNACION = re.compile(r"(?<![=!<>])=\s*(?=[\[{])")   # `x = {` (no `==`, `>=`, etc.)


def _bloques_json(script_txt: str) -> Iterator[Any]:
    """Encuentra objetos JSON dentro de código JS (p. ej. `window.runParams = {...};`)."""
    s = script_txt.strip()
    if s[:1] in "{[":
        try:
            yield json.loads(s)
            return
        except json.JSONDecodeError:
            pass
    for m in _RE_ASIGNACION.finditer(script_txt):
        inicio = m.end()
        fin = _fin_balanceado(script_txt, inicio)
        if fin is None or fin - inicio < 50:
            continue
        try:
            yield json.loads(script_txt[inicio:fin])
        except json.JSONDecodeError:
            continue


def _fin_balanceado(s: str, i: int) -> int | None:
    apertura = s[i]
    cierre = "}" if apertura == "{" else "]"
    nivel, en_cadena, escape = 0, False, False
    for j in range(i, min(len(s), i + 5_000_000)):
        c = s[j]
        if en_cadena:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                en_cadena = False
            continue
        if c == '"':
            en_cadena = True
        elif c in "{[":
            nivel += 1
        elif c in "}]":
            nivel -= 1
            if nivel == 0:
                return j + 1 if c == cierre else None
    return None


def _valor_titulo(d: dict) -> str | None:
    for k in _CLAVES_TITULO:
        v = d.get(k)
        if isinstance(v, dict):
            v = v.get("displayTitle") or v.get("title") or v.get("text")
        if isinstance(v, str) and len(v.strip()) >= 10:
            return v.strip()
    return None


def _buscar_precio(d: Any, profundidad: int = 0) -> str | float | None:
    """Busca un campo de precio dentro del dict (hasta 3 niveles)."""
    if profundidad > 3 or not isinstance(d, dict):
        return None
    preferidas = ("formattedPrice", "price_str", "priceStr", "salePrice", "minPrice",
                  "promotionPrice", "displayPrice", "price", "value")
    for k in preferidas:
        if k in d:
            v = d[k]
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                return v
            if isinstance(v, str) and re.search(r"\d", v):
                return v
            r = _buscar_precio(v, profundidad + 1)
            if r is not None:
                return r
    for k, v in d.items():
        if "price" in k.lower() and isinstance(v, dict):
            r = _buscar_precio(v, profundidad + 1)
            if r is not None:
                return r
    return None


def _buscar_texto_ventas(d: dict) -> str | None:
    for k, v in d.items():
        kl = k.lower()
        if any(p in kl for p in ("sold", "trade", "sales")):
            if isinstance(v, str) and re.search(r"\d", v):
                return v
            if isinstance(v, dict):
                for vv in v.values():
                    if isinstance(vv, str) and re.search(r"\d", vv):
                        return vv
    return None


def desde_json_embebido(soup: BeautifulSoup, url_base: str) -> list[ItemCrudo]:
    items: list[ItemCrudo] = []
    for script in soup.find_all("script"):
        if script.get("type") in ("application/ld+json",):
            continue
        contenido = script.string or script.get_text() or ""
        if len(contenido) < 200:
            continue
        for bloque in _bloques_json(contenido):
            for d in _recorrer(bloque):
                titulo = _valor_titulo(d)
                if not titulo:
                    continue
                precio = _buscar_precio(d)
                if precio is None:
                    continue
                id_ext = next((str(d[k]) for k in _CLAVES_ID if d.get(k) not in (None, "")), None)
                url = next((d[k] for k in _CLAVES_URL if isinstance(d.get(k), str)), None)
                items.append(ItemCrudo(
                    titulo=titulo,
                    precio_texto=precio if isinstance(precio, str) else None,
                    precio_num=float(precio) if isinstance(precio, (int, float)) else None,
                    url=urljoin(url_base, url) if url else None,
                    id_externo=id_ext,
                    vendidos_texto=_buscar_texto_ventas(d),
                ))
    return deduplicar(items)


# ---------------------------------------------------------------- tarjetas por enlace

def por_anclas(
    soup: BeautifulSoup,
    url_base: str,
    patron_href: str,
    regex_precio: str,
    regex_extra: dict[str, str] | None = None,
) -> list[ItemCrudo]:
    """Para páginas con clases CSS ofuscadas: toma cada enlace a una ficha de producto y busca
    el título y el precio dentro de la tarjeta que lo contiene."""
    re_href = re.compile(patron_href)
    re_precio = re.compile(regex_precio)
    items: list[ItemCrudo] = []
    for a in soup.find_all("a", href=True):
        m = re_href.search(a["href"])
        if not m:
            continue
        tarjeta = a
        # sube hasta un contenedor que también incluya el precio
        for _ in range(4):
            if re_precio.search(tarjeta.get_text("", strip=True)):
                break
            if tarjeta.parent is None:
                break
            tarjeta = tarjeta.parent
        texto_plano = tarjeta.get_text("", strip=True)
        mp = re_precio.search(texto_plano)
        if not mp:
            continue
        titulo_el = tarjeta.find(["h1", "h2", "h3"]) or a
        titulo = a.get("title") or a.get("aria-label") or texto(titulo_el) or ""
        titulo = titulo.strip()
        if len(titulo) < 10:
            continue
        item = ItemCrudo(
            titulo=titulo,
            precio_texto=mp.group(0),
            url=urljoin(url_base, a["href"]),
            id_externo=m.group(1) if m.groups() else None,
        )
        for campo, rx in (regex_extra or {}).items():
            me = re.search(rx, tarjeta.get_text(" ", strip=True), re.I)
            if me:
                setattr(item, campo, me.group(0))
        items.append(item)
    return deduplicar(items)
