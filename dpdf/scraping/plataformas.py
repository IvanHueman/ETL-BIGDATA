"""Adaptadores por plataforma.

Los selectores CSS se escribieron según la estructura conocida de cada sitio. Los sitios
cambian su HTML seguido: si una plataforma empieza a devolver `sin_resultados`, abre el HTML
crudo que se guardó en data/raw/<plataforma>/<fecha>/, ajusta los selectores aquí y corre
`python -m dpdf reprocesar --fecha AAAA-MM-DD` para volver a extraer sin repetir peticiones.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .base import AdaptadorBase
from .extract import ItemCrudo, deduplicar, por_anclas, texto


class MercadoLibre(AdaptadorBase):
    id = "mercadolibre"

    def extraer_especifico(self, soup: BeautifulSoup, html: str) -> list[ItemCrudo]:
        tarjetas = soup.select("li.ui-search-layout__item") or soup.select("div.poly-card")
        items = []
        for t in tarjetas:
            enlace = t.select_one("a.poly-component__title, h3.poly-component__title-wrapper a, "
                                  "a.ui-search-item__group__element, a.ui-search-link")
            titulo = texto(t.select_one(".poly-component__title, .ui-search-item__title")) or texto(enlace)
            if not titulo:
                continue
            monto = (t.select_one(".poly-price__current .andes-money-amount")
                     or t.select_one(".ui-search-price__second-line .andes-money-amount")
                     or t.select_one(".andes-money-amount:not(.andes-money-amount--previous)"))
            if monto is None:
                continue
            fraccion = texto(monto.select_one(".andes-money-amount__fraction"))
            if not fraccion:
                continue
            centavos = texto(monto.select_one(".andes-money-amount__cents"))
            simbolo = texto(monto.select_one(".andes-money-amount__currency-symbol")) or "$"
            precio_txt = f"{simbolo}{fraccion}" + (f".{centavos}" if centavos else "")
            url = enlace.get("href") if enlace else None
            m_id = re.search(r"(MLM-?\d+)", url or "")
            tarjeta_txt = (texto(t) or "").lower()
            items.append(ItemCrudo(
                titulo=titulo,
                precio_texto=precio_txt,
                moneda="USD" if "US$" in simbolo else "MXN",
                url=url,
                id_externo=m_id.group(1).replace("-", "") if m_id else None,
                envio_texto=texto(t.select_one(".poly-component__shipping, .ui-search-item__shipping")),
                vendedor=texto(t.select_one(".poly-component__seller")),
                patrocinado="patrocinado" in tarjeta_txt or "promocionado" in tarjeta_txt,
            ))
        return deduplicar(items)


class Amazon(AdaptadorBase):
    id = "amazon"

    def extraer_especifico(self, soup: BeautifulSoup, html: str) -> list[ItemCrudo]:
        items = []
        for c in soup.select('div[data-component-type="s-search-result"]'):
            asin = c.get("data-asin")
            if not asin:
                continue
            h2 = c.select_one("h2")
            titulo = (h2.get("aria-label") if h2 else None) or texto(c.select_one("h2 span"))
            precio = c.select_one("span.a-price:not(.a-text-price) span.a-offscreen")
            if not titulo or precio is None:
                continue
            vendidos = c.find(string=re.compile(r"comprad|vendid", re.I))
            calificacion = texto(c.select_one("span.a-icon-alt"))
            items.append(ItemCrudo(
                titulo=titulo.strip(),
                precio_texto=texto(precio),
                moneda="MXN",
                url=f"{self.url_base}/dp/{asin}",
                id_externo=asin,
                vendidos_texto=" ".join(vendidos.split()) if vendidos else None,
                notas=calificacion,
                patrocinado=bool(c.select_one(".puis-sponsored-label-text, .s-sponsored-label-text"))
                or "patrocinado" in (texto(c) or "").lower(),
            ))
        return deduplicar(items)


class AliExpress(AdaptadorBase):
    """Sin JavaScript, los productos vienen en el JSON incrustado (estrategia json_embebido).
    Con Playwright se leen las tarjetas ya dibujadas."""
    id = "aliexpress"

    def extraer_especifico(self, soup: BeautifulSoup, html: str) -> list[ItemCrudo]:
        return por_anclas(
            soup, self.url_base,
            patron_href=r"/item/(\d+)\.html",
            regex_precio=r"(?:MX\$|US\s?\$|\$)\s?\d[\d.,]*",
            regex_extra={"vendidos_texto": r"[\d.,]+\+?\s*(?:vendidos|sold)"},
        )


class Temu(AdaptadorBase):
    """Experimental: Temu arma casi todo con JavaScript y tiene controles anti-bot fuertes.
    Lo más probable es que termine en `bloqueado`; el sistema lo registra y sigue."""
    id = "temu"

    def extraer_especifico(self, soup: BeautifulSoup, html: str) -> list[ItemCrudo]:
        return por_anclas(
            soup, self.url_base,
            patron_href=r"-g-(\d+)\.html",
            regex_precio=r"(?:MX\$|\$)\s?\d[\d.,]*",
            regex_extra={"vendidos_texto": r"[\d.,]+\s*(?:mil\s*)?\+?\s*vendidos"},
        )


class Alibaba(AdaptadorBase):
    """Precios de MAYOREO en USD (por pieza, con pedido mínimo). Se guardan con el pedido
    mínimo en `notas` y no se mezclan con los precios al menudeo."""
    id = "alibaba"

    def extraer_especifico(self, soup: BeautifulSoup, html: str) -> list[ItemCrudo]:
        return por_anclas(
            soup, self.url_base,
            patron_href=r"/product-detail/[^\"']*?_(\d+)\.html",
            regex_precio=r"(?:US\s?\$|\$)\s?\d[\d.,]*(?:\s?-\s?(?:US\s?\$|\$)?\s?\d[\d.,]*)?",
            regex_extra={"notas": r"(?:min\.?\s*order|pedido m[ií]n(?:imo)?\.?)[^\d]{0,15}[\d,.]+\s*\w+"},
        )


ADAPTADORES = {cls.id: cls for cls in (MercadoLibre, Amazon, AliExpress, Temu, Alibaba)}


def crear_adaptador(conf: dict) -> AdaptadorBase:
    return ADAPTADORES[conf["id"]](conf)
