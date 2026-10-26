"""Corrida de punta a punta con páginas de prueba: no se hace ninguna petición real."""
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import func, select

from dpdf import config
from dpdf.cli import dataset_modelo
from dpdf.db import Corrida, Observacion, PrecioCompetenciaDiario, Venta, get_engine, init_db, sesion
from dpdf.processing.agregacion import agregar_diario
from dpdf.scraping.base import GuardiaRobots, LimitadorRitmo, Pagina
from dpdf.scraping.runner import ejecutar_scraping, reprocesar
from dpdf.simulacion.ventas import calendario, guardar, simular

FIX = Path(__file__).parent / "fixtures"
PAGINAS = {
    "listado.mercadolibre.com.mx": "mercadolibre_busqueda.html",
    "www.amazon.com.mx": "bloqueo_captcha.html",
    "es.aliexpress.com": "aliexpress_busqueda.html",
    "www.alibaba.com": "alibaba_busqueda.html",
}


class NavegadorFalso:
    def __init__(self):
        self.peticiones = []

    def obtener(self, url, render_js=False):
        self.peticiones.append(url)
        host = url.split("/")[2]
        if host not in PAGINAS:
            return Pagina(url=url, http_status=None, html=None, error="ConnectError")
        html = (FIX / PAGINAS[host]).read_text(encoding="utf-8")
        return Pagina(url=url, http_status=200, html=html)

    def cerrar(self):
        pass


def robots_falso(url):
    if "temu.com" in url:
        return 200, "User-agent: *\nDisallow: /mx/search_result.html\n"
    return 404, ""


@pytest.fixture
def engine(tmp_path):
    eng = get_engine(f"sqlite:///{tmp_path / 'prueba.db'}")
    init_db(eng)
    return eng


def test_scraping_punta_a_punta(engine, tmp_path):
    nav = NavegadorFalso()
    resumen = ejecutar_scraping(
        engine, productos_ids=["mouse", "teclado"], max_items=40, navegador=nav,
        robots=GuardiaRobots("prueba", obtener=robots_falso), limitador=LimitadorRitmo(activo=False),
        dir_crudo=tmp_path / "raw", log=lambda *_: None,
    )
    estados = {(r["plataforma"], r["producto"], r["tipo"]): r["estado"] for r in resumen}

    assert estados[("mercadolibre", "mouse", "modelo")] == "ok"
    assert estados[("amazon", "mouse", "modelo")] == "bloqueado"
    assert ("amazon", "teclado", "modelo") not in estados          # tras el bloqueo, Amazon se detiene
    assert estados[("temu", "mouse", "generica")] == "prohibido_robots"
    assert not any("temu.com" in u for u in nav.peticiones)        # nunca se pidió la página prohibida
    assert estados[("aliexpress", "mouse", "modelo")] == "ok"

    with sesion(engine) as s:
        obs = s.scalars(select(Observacion).where(Observacion.producto_id == "mouse",
                                                  Observacion.plataforma_id == "mercadolibre")).all()
        niveles = {o.titulo: o.match_nivel for o in obs}
        assert niveles["Mouse Inalámbrico Logitech M170 Gris"] == "exacto"
        assert niveles["Mouse Pad Logitech Antiderrapante"] is None
        assert niveles["Ratón Inalámbrico Ergonómico Silencioso Usb"] == "equivalente"

        ali = s.scalars(select(Observacion).where(Observacion.plataforma_id == "alibaba")).all()
        assert ali and all(o.moneda == "USD" and o.precio_mxn > o.precio for o in ali)

        crudos = s.scalars(select(Corrida.archivo_crudo).where(Corrida.archivo_crudo.is_not(None))).all()
        assert crudos and all(Path(c).exists() for c in crudos)
        assert all(d is not None for d in s.scalars(select(Corrida.duracion_seg)
                                                    .where(Corrida.estado == "ok")).all())

    n = agregar_diario(engine)
    assert n > 0
    with sesion(engine) as s:
        fila = s.scalars(select(PrecioCompetenciaDiario).where(
            PrecioCompetenciaDiario.producto_id == "mouse",
            PrecioCompetenciaDiario.plataforma_id == "mercadolibre")).one()
        assert fila.nivel_match == "exacto" and fila.precio_mediana_mxn == 229.0

    # reprocesar desde el HTML crudo da el mismo resultado sin nuevas peticiones
    peticiones_antes = len(nav.peticiones)
    with sesion(engine) as s:
        antes = s.scalar(select(func.count()).select_from(Observacion))
        dia = s.scalar(select(func.min(Corrida.inicio))).date()
    reprocesar(engine, dia, log=lambda *_: None)
    with sesion(engine) as s:
        assert s.scalar(select(func.count()).select_from(Observacion)) == antes
    assert len(nav.peticiones) == peticiones_antes


def test_calendario_eventos():
    fechas = __import__("pandas").date_range("2024-01-01", "2025-12-31")
    cal = calendario(fechas)
    assert cal.loc["2024-11-15":"2024-11-18", "buen_fin"].eq(1).all()      # Buen Fin 2024
    assert cal.loc["2025-11-14":"2025-11-17", "buen_fin"].eq(1).all()      # Buen Fin 2025
    assert cal.loc["2025-05-26":"2025-06-03", "hot_sale"].eq(1).all()      # Hot Sale 2025
    assert cal.loc["2025-06-15", "dia_padre"] == 1
    assert cal.loc["2024-12-24", "navidad"] == 1


def test_simulacion(engine, tmp_path):
    productos = config.productos()
    ventas, comp, verdad = simular(productos, date(2023, 1, 1), date(2025, 12, 31), semilla=7)
    assert len(ventas) == 1096 * len(productos)
    assert (ventas["unidades"] >= 0).all()
    assert set(comp["plataforma_id"]) == {"mercadolibre", "amazon", "aliexpress", "temu"}
    normal = ventas.loc[ventas["evento"].isna(), "unidades"].mean()
    media = ventas.groupby("evento")["unidades"].mean()
    assert media["buen_fin"] > 2 * normal
    assert media["hot_sale"] > 1.5 * normal
    assert media["cuesta_enero"] < normal

    # misma semilla -> mismos datos; alargar el rango no cambia la historia ya generada
    v2, _, _ = simular(productos, date(2023, 1, 1), date(2026, 6, 30), semilla=7)
    corto = ventas[ventas["producto_id"] == "mouse"]["unidades"].to_numpy()
    largo = v2[v2["producto_id"] == "mouse"]["unidades"].to_numpy()[: len(corto)]
    assert (corto == largo).all()

    guardar(engine, ventas, comp, verdad, tmp_path)
    assert (tmp_path / "verdad_simulacion.json").exists()
    with sesion(engine) as s:
        assert s.scalar(select(func.count()).select_from(Venta)) == len(ventas)

    ds = dataset_modelo(engine)
    assert {"comp_mercadolibre", "comp_amazon", "comp_aliexpress", "comp_temu"} <= set(ds.columns)
    assert ds["comp_mercadolibre"].notna().all()
