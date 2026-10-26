from pathlib import Path

from protego import Protego

from dpdf import config
from dpdf.scraping.base import GuardiaRobots, detectar_bloqueo
from dpdf.scraping.plataformas import crear_adaptador

FIX = Path(__file__).parent / "fixtures"


def _adaptador(pid):
    return crear_adaptador(config.plataformas()[pid])


def _html(nombre):
    return (FIX / nombre).read_text(encoding="utf-8")


def test_url_busqueda():
    assert _adaptador("mercadolibre").url_busqueda("Logitech  M170") == "https://listado.mercadolibre.com.mx/logitech-m170"
    assert _adaptador("amazon").url_busqueda("logitech m170") == "https://www.amazon.com.mx/s?k=logitech+m170"
    assert _adaptador("aliexpress").url_busqueda("jbl go 4") == "https://es.aliexpress.com/w/wholesale-jbl-go-4.html"
    assert _adaptador("temu").url_busqueda("mouse inalambrico").endswith("search_key=mouse%20inalambrico")


def test_mercadolibre():
    items, estrategia = _adaptador("mercadolibre").extraer(_html("mercadolibre_busqueda.html"))
    assert estrategia == "selectores"
    assert len(items) == 4
    assert items[0].titulo == "Mouse Inalámbrico Logitech M170 Gris"
    assert items[0].precio_texto == "$229"                 # el precio tachado ($349) se ignora
    assert items[0].id_externo == "MLM1234567890"
    assert items[0].envio_texto == "Envío gratis"
    assert items[2].precio_texto == "$1,099.50" and items[2].patrocinado


def test_amazon():
    items, estrategia = _adaptador("amazon").extraer(_html("amazon_busqueda.html"))
    assert estrategia == "selectores"
    assert [i.id_externo for i in items] == ["B0TEST0001", "B0TEST0002"]   # sin precio o sin ASIN se descartan
    assert items[0].precio_texto == "$219.00"
    assert "comprados" in items[0].vendidos_texto
    assert items[1].patrocinado and not items[0].patrocinado
    assert items[0].url == "https://www.amazon.com.mx/dp/B0TEST0001"


def test_aliexpress_json_incrustado():
    items, estrategia = _adaptador("aliexpress").extraer(_html("aliexpress_busqueda.html"))
    assert estrategia == "json_embebido"
    assert len(items) == 3
    assert items[0].titulo.startswith("Logitech M170")
    assert items[0].precio_texto == "MX$189.50"
    assert items[0].vendidos_texto == "1.000+ vendidos"
    assert items[0].url == "https://es.aliexpress.com/item/1005001111111111.html"


def test_alibaba_por_anclas():
    items, estrategia = _adaptador("alibaba").extraer(_html("alibaba_busqueda.html"))
    assert estrategia == "selectores"
    assert len(items) == 2
    assert items[0].precio_texto == "US$2.10-3.40"
    assert items[0].id_externo == "1600123456789"
    assert "100 pieces" in items[0].notas


def test_jsonld_generico():
    items, estrategia = _adaptador("mercadolibre").extraer(_html("jsonld_lista.html"))
    assert estrategia == "jsonld"
    assert items[0].titulo.startswith("JBL Go 4") and items[0].precio_texto == "849.00"
    assert items[1].precio_texto == "199 - 259"


def test_bloqueo():
    html = _html("bloqueo_captcha.html")
    items, _ = _adaptador("amazon").extraer(html)
    assert items == []
    assert detectar_bloqueo(200, html) is not None
    assert detectar_bloqueo(429, "") == "HTTP 429"
    assert detectar_bloqueo(200, "<html>sin resultados</html>") is None


ROBOTS = """
User-agent: Googlebot
Disallow: /w/

User-agent: *
Disallow: /search/*
Disallow: /*?*top_goods=*
Crawl-delay: 7
"""


def test_robots_reglas_y_comodines():
    def obtener(url):
        return (200, ROBOTS) if "ejemplo.com" in url else (503, "")
    g = GuardiaRobots("DPDF-Engine/0.1", obtener=obtener)
    assert g.permitido("https://ejemplo.com/w/wholesale-mouse.html")[0]           # /w/ solo aplica a Googlebot
    assert not g.permitido("https://ejemplo.com/search/mouse")[0]
    assert not g.permitido("https://ejemplo.com/lista?x=1&top_goods=9")[0]       # comodín
    assert g.crawl_delay("https://ejemplo.com/") == 7
    ok, motivo = g.permitido("https://caido.com/s?k=mouse")                       # 5xx -> prohibido
    assert not ok and "inaccesible" in motivo


def test_robots_404_permite():
    g = GuardiaRobots("DPDF-Engine/0.1", obtener=lambda url: (404, ""))
    assert g.permitido("https://sinrobots.com/cualquier/cosa")[0]


def test_protego_temu_grupo_generico():
    # Fragmento real del robots.txt de Temu (oct-2026): /w/ y /s/ están prohibidos para
    # bots con nombre, pero el grupo '*' solo tiene un Allow.
    robots = "User-agent: Googlebot\nDisallow: /w/\nDisallow: /s/\n\nUser-agent: *\nAllow: /.well-known/\n"
    rp = Protego.parse(robots)
    assert rp.can_fetch("https://www.temu.com/mx/search_result.html?search_key=mouse", "DPDF-Engine/0.1")
    assert not rp.can_fetch("https://www.temu.com/w/algo", "Googlebot")
