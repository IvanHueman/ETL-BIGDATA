"""Orquesta una corrida de scraping: plataforma x producto x consulta.

Por cada búsqueda: robots.txt -> pausa -> descarga -> guarda HTML crudo -> extrae ->
normaliza precio -> matching -> guarda en la base. Cada búsqueda queda registrada en
`corridas_scraping` con su estado y su duración (evidencia para la hipótesis H3).
"""
from __future__ import annotations

import gzip
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.engine import Engine

from .. import config
from ..db import Corrida, Observacion, sesion
from ..processing.matching import evaluar
from ..processing.precios import parsear_precio
from ..processing.tipo_cambio import obtener_tipo_cambio
from .base import GuardiaRobots, LimitadorRitmo, Navegador, detectar_bloqueo
from .extract import ItemCrudo
from .plataformas import crear_adaptador

MAX_ERRORES_SEGUIDOS = 3


def ahora_local() -> datetime:
    return datetime.now(ZoneInfo(config.ZONA_HORARIA)).replace(tzinfo=None, microsecond=0)


def _corta(valor: str | None, n: int) -> str | None:
    return valor[:n] if isinstance(valor, str) else valor


def _guardar_crudo(dir_crudo: Path, plataforma: str, producto: str, tipo: str, momento: datetime, html: str) -> str:
    carpeta = dir_crudo / plataforma / momento.strftime("%Y-%m-%d")
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / f"{producto}__{tipo}__{momento.strftime('%H%M%S')}.html.gz"
    with gzip.open(ruta, "wt", encoding="utf-8") as f:
        f.write(html)
    return str(ruta)


def _a_observaciones(items: list[ItemCrudo], corrida: Corrida, conf: dict, producto: dict,
                     capturado_en: datetime, max_items: int) -> list[Observacion]:
    salida = []
    es_mayoreo = bool(conf.get("es_mayoreo"))
    for it in items[:max_items]:
        bruto = it.precio_num if it.precio_num is not None else it.precio_texto
        p = parsear_precio(bruto, moneda_defecto=it.moneda or conf["moneda"])
        if p is None:
            continue
        tc, fuente_tc = obtener_tipo_cambio(p.moneda)
        precio_mxn = round(p.valor * tc, 2)
        m = evaluar(it.titulo, precio_mxn, producto, es_mayoreo=es_mayoreo)
        salida.append(Observacion(
            corrida_id=corrida.id, plataforma_id=conf["id"], producto_id=producto["id"],
            capturado_en=capturado_en, titulo=it.titulo, url=it.url,
            id_externo=_corta(it.id_externo, 80), precio_texto=_corta(p.texto, 80),
            moneda=p.moneda, precio=p.valor, precio_max=p.valor_max, precio_mxn=precio_mxn,
            tipo_cambio=tc, fuente_tipo_cambio=fuente_tc,
            vendidos_texto=_corta(it.vendidos_texto, 80), envio_texto=_corta(it.envio_texto, 120),
            vendedor=_corta(it.vendedor, 120), notas=_corta(it.notas, 200),
            patrocinado=it.patrocinado, match_nivel=m.nivel, match_score=m.score,
        ))
    return salida


def ejecutar_scraping(
    engine: Engine,
    plataformas_ids: list[str] | None = None,
    productos_ids: list[str] | None = None,
    max_items: int = 40,
    navegador: Navegador | None = None,
    robots: GuardiaRobots | None = None,
    limitador: LimitadorRitmo | None = None,
    dir_crudo: Path | None = None,
    log: Callable[[str], None] = print,
) -> list[dict]:
    ua = config.user_agent()
    confs = config.plataformas()
    productos = [p for p in config.productos() if not productos_ids or p["id"] in productos_ids]
    plataformas = [confs[i] for i in (plataformas_ids or list(confs))]
    navegador = navegador or Navegador(ua)
    robots = robots or GuardiaRobots(ua)
    limitador = limitador or LimitadorRitmo()
    dir_crudo = dir_crudo or config.DIR_CRUDO
    resumen: list[dict] = []

    try:
        for conf in plataformas:
            adaptador = crear_adaptador(conf)
            errores_seguidos = 0
            detenida = False
            log(f"\n== {conf['nombre']} ==")
            for producto in productos:
                if detenida:
                    break
                for tipo in conf.get("consultas", ["modelo", "generica"]):
                    consulta = producto["consultas"][tipo]
                    url = adaptador.url_busqueda(consulta)
                    with sesion(engine) as s:
                        corrida = Corrida(plataforma_id=conf["id"], producto_id=producto["id"],
                                          tipo_consulta=tipo, url=url, inicio=ahora_local(), estado="en_curso")
                        s.add(corrida)
                        s.flush()

                        permitido, motivo = robots.permitido(url)
                        if not permitido:
                            corrida.estado, corrida.mensaje = "prohibido_robots", motivo
                            corrida.fin = ahora_local()
                            s.commit()
                            log(f"  {producto['id']:<12} {tipo:<9} PROHIBIDO  {motivo}")
                            resumen.append({"plataforma": conf["id"], "producto": producto["id"],
                                            "tipo": tipo, "estado": corrida.estado, "n_items": 0, "n_match": 0})
                            continue

                        limitador.esperar(url, tuple(conf.get("pausa_seg", (5, 10))), robots.crawl_delay(url))
                        t0 = time.monotonic()
                        pagina = navegador.obtener(url, render_js=bool(conf.get("requiere_js")))
                        corrida.http_status = pagina.http_status

                        if pagina.error or pagina.html is None:
                            corrida.estado, corrida.mensaje = "error", pagina.error
                            errores_seguidos += 1
                        else:
                            corrida.archivo_crudo = _guardar_crudo(dir_crudo, conf["id"], producto["id"],
                                                                   tipo, corrida.inicio, pagina.html)
                            items, estrategia = adaptador.extraer(pagina.html)
                            corrida.estrategia = estrategia
                            if items:
                                obs = _a_observaciones(items, corrida, conf, producto, corrida.inicio, max_items)
                                s.add_all(obs)
                                corrida.n_items = len(obs)
                                corrida.n_match = sum(1 for o in obs if o.match_nivel)
                                corrida.estado = "ok"
                                errores_seguidos = 0
                            else:
                                bloqueo = detectar_bloqueo(pagina.http_status, pagina.html)
                                corrida.estado = "bloqueado" if bloqueo else "sin_resultados"
                                corrida.mensaje = bloqueo
                                if pagina.renderizada_js is False and conf.get("requiere_js"):
                                    corrida.mensaje = (corrida.mensaje or "") + " | sin Playwright: solo HTML inicial"

                        corrida.duracion_seg = round(time.monotonic() - t0, 3)
                        corrida.fin = ahora_local()
                        s.commit()
                        log(f"  {producto['id']:<12} {tipo:<9} {corrida.estado.upper():<15}"
                            f" items={corrida.n_items:<3} match={corrida.n_match:<3}"
                            f" {corrida.duracion_seg:.1f}s {corrida.mensaje or ''}")
                        resumen.append({"plataforma": conf["id"], "producto": producto["id"], "tipo": tipo,
                                        "estado": corrida.estado, "n_items": corrida.n_items,
                                        "n_match": corrida.n_match})

                        if corrida.estado == "bloqueado":
                            log(f"  -> {conf['nombre']} bloqueó la petición. Se detiene esta plataforma "
                                "en esta corrida (no se intenta evadir el bloqueo).")
                            detenida = True
                            break
                        if errores_seguidos >= MAX_ERRORES_SEGUIDOS:
                            log(f"  -> {MAX_ERRORES_SEGUIDOS} errores seguidos; se detiene {conf['nombre']}.")
                            detenida = True
                            break
    finally:
        navegador.cerrar()
    return resumen


def reprocesar(engine: Engine, fecha: date, plataforma_id: str | None = None,
               max_items: int = 40, log: Callable[[str], None] = print) -> int:
    """Vuelve a extraer desde el HTML crudo guardado (tras ajustar selectores), sin pedir
    nada a los sitios. Reemplaza las observaciones de esas corridas."""
    confs = config.plataformas()
    inicio, fin = datetime.combine(fecha, datetime.min.time()), datetime.combine(fecha + timedelta(days=1), datetime.min.time())
    total = 0
    with sesion(engine) as s:
        consulta = select(Corrida).where(Corrida.inicio >= inicio, Corrida.inicio < fin,
                                         Corrida.archivo_crudo.is_not(None))
        if plataforma_id:
            consulta = consulta.where(Corrida.plataforma_id == plataforma_id)
        for corrida in s.scalars(consulta).all():
            ruta = Path(corrida.archivo_crudo)
            if not ruta.exists():
                log(f"  falta el archivo crudo: {ruta}")
                continue
            with gzip.open(ruta, "rt", encoding="utf-8") as f:
                html = f.read()
            conf = confs[corrida.plataforma_id]
            producto = config.producto(corrida.producto_id)
            items, estrategia = crear_adaptador(conf).extraer(html)
            s.execute(delete(Observacion).where(Observacion.corrida_id == corrida.id))
            obs = _a_observaciones(items, corrida, conf, producto, corrida.inicio, max_items)
            s.add_all(obs)
            corrida.estrategia = estrategia
            corrida.n_items = len(obs)
            corrida.n_match = sum(1 for o in obs if o.match_nivel)
            if obs:
                corrida.estado = "ok"
            total += len(obs)
            log(f"  {corrida.plataforma_id:<12} {corrida.producto_id:<12} items={len(obs)} match={corrida.n_match}")
        s.commit()
    return total
