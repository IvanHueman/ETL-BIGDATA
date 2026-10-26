"""Línea de comandos de DPDF-Engine.

    python -m dpdf init-db
    python -m dpdf robots
    python -m dpdf scrape --plataformas mercadolibre --productos mouse,teclado
    python -m dpdf agregar
    python -m dpdf simular --desde 2023-01-01
    python -m dpdf exportar
    python -m dpdf resumen
    python -m dpdf reprocesar --fecha 2026-10-09
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func, select

from . import config
from .db import Corrida, Observacion, PrecioCompetenciaDiario, Venta, get_engine, init_db, sesion


def _lista(valor: str | None) -> list[str] | None:
    return [v.strip() for v in valor.split(",") if v.strip()] if valor else None


def _validar(ids: list[str] | None, validos: list[str], tipo: str) -> None:
    for i in ids or []:
        if i not in validos:
            sys.exit(f"{tipo} desconocido: '{i}'. Opciones: {', '.join(validos)}")


def cmd_init_db(args) -> None:
    engine = get_engine()
    for aviso in init_db(engine):
        print("AVISO:", aviso)
    print(f"Base lista: {engine.url.render_as_string(hide_password=True)}")
    print(f"  {len(config.productos())} productos, {len(config.plataformas())} plataformas")


def cmd_robots(args) -> None:
    from .scraping.base import GuardiaRobots
    from .scraping.plataformas import crear_adaptador

    ua = config.user_agent()
    guardia = GuardiaRobots(ua)
    ejemplo = config.productos()[0]
    print(f"User-Agent evaluado: {ua}\n")
    print(f"{'plataforma':<14}{'consulta':<10}{'resultado':<11}{'crawl-delay':<13}detalle")
    for conf in config.plataformas().values():
        if args.plataformas and conf["id"] not in args.plataformas:
            continue
        adaptador = crear_adaptador(conf)
        for tipo in conf.get("consultas", ["modelo"]):
            url = adaptador.url_busqueda(ejemplo["consultas"][tipo])
            ok, motivo = guardia.permitido(url)
            delay = guardia.crawl_delay(url)
            print(f"{conf['id']:<14}{tipo:<10}{'PERMITIDO' if ok else 'PROHIBIDO':<11}"
                  f"{(str(delay) + ' s') if delay else '-':<13}{motivo}")
            print(f"{'':<14}{url}")


def cmd_scrape(args) -> None:
    from .processing.agregacion import agregar_diario
    from .scraping.base import LimitadorRitmo
    from .scraping.runner import ejecutar_scraping

    _validar(args.plataformas, list(config.plataformas()), "Plataforma")
    _validar(args.productos, [p["id"] for p in config.productos()], "Producto")
    engine = get_engine()
    init_db(engine)
    resumen = ejecutar_scraping(
        engine, plataformas_ids=args.plataformas, productos_ids=args.productos,
        max_items=args.max_items, limitador=LimitadorRitmo(activo=not args.sin_pausa),
    )
    df = pd.DataFrame(resumen)
    if not df.empty:
        print("\nResumen de la corrida:")
        print(df.groupby(["plataforma", "estado"]).agg(busquedas=("producto", "count"),
                                                     items=("n_items", "sum"),
                                                     coincidencias=("n_match", "sum")).to_string())
    n = agregar_diario(engine)
    print(f"\nSerie diaria de competencia actualizada ({n} filas).")


def cmd_reprocesar(args) -> None:
    from .processing.agregacion import agregar_diario
    from .scraping.runner import reprocesar

    engine = get_engine()
    total = reprocesar(engine, date.fromisoformat(args.fecha), plataforma_id=args.plataforma,
                       max_items=args.max_items)
    print(f"Observaciones reprocesadas: {total}. Filas diarias: {agregar_diario(engine)}")


def cmd_agregar(args) -> None:
    from .processing.agregacion import agregar_diario
    print(f"Filas diarias de precio de competencia: {agregar_diario(get_engine())}")


def cmd_simular(args) -> None:
    from .simulacion.ventas import guardar, simular

    engine = get_engine()
    init_db(engine)
    hasta = date.fromisoformat(args.hasta) if args.hasta else date.today() - timedelta(days=1)
    ventas, competencia, verdad = simular(config.productos(), date.fromisoformat(args.desde), hasta, args.semilla)
    guardar(engine, ventas, competencia, verdad, config.DIR_LIMPIO)
    print(f"Ventas simuladas: {len(ventas):,} filas ({args.desde} a {hasta}), "
          f"{ventas['unidades'].sum():,} unidades")
    print(f"Precios de competencia simulados: {len(competencia):,} filas")
    print(f"Parámetros reales de la simulación: {config.DIR_LIMPIO / 'verdad_simulacion.json'}")


def dataset_modelo(engine) -> pd.DataFrame:
    """Ventas diarias + precio de competencia por plataforma (columnas comp_<plataforma>).
    Si para una fecha hay precio scrapeado y simulado, gana el scrapeado."""
    with engine.connect() as con:
        ventas = pd.read_sql(select(Venta), con)
        comp = pd.read_sql(select(PrecioCompetenciaDiario), con)
    if ventas.empty:
        return ventas
    if not comp.empty:
        comp["prioridad"] = (comp["fuente"] == "scraping").astype(int)
        comp = (comp.sort_values("prioridad")
                .drop_duplicates(["fecha", "producto_id", "plataforma_id"], keep="last"))
        ancho = comp.pivot_table(index=["fecha", "producto_id"], columns="plataforma_id",
                                 values="precio_mediana_mxn").add_prefix("comp_").reset_index()
        ventas = ventas.merge(ancho, on=["fecha", "producto_id"], how="left")
    return ventas.sort_values(["producto_id", "fecha"]).reset_index(drop=True)


def cmd_exportar(args) -> None:
    engine = get_engine()
    config.asegurar_directorios()
    tablas = {"ventas": Venta, "precios_competencia_diario": PrecioCompetenciaDiario,
              "observaciones_precio": Observacion, "corridas_scraping": Corrida}
    with engine.connect() as con:
        for nombre, modelo in tablas.items():
            df = pd.read_sql(select(modelo), con)
            df.to_parquet(config.DIR_LIMPIO / f"{nombre}.parquet", index=False)
            df.to_csv(config.DIR_LIMPIO / f"{nombre}.csv", index=False)
            print(f"  {nombre:<28} {len(df):>8,} filas")
    ds = dataset_modelo(engine)
    ds.to_parquet(config.DIR_LIMPIO / "dataset_modelo.parquet", index=False)
    ds.to_csv(config.DIR_LIMPIO / "dataset_modelo.csv", index=False)
    print(f"  {'dataset_modelo':<28} {len(ds):>8,} filas  <- entrada para el modelado")
    print(f"Archivos en {config.DIR_LIMPIO}")


def cmd_resumen(args) -> None:
    engine = get_engine()
    init_db(engine)
    with sesion(engine) as s:
        print("Búsquedas de scraping por plataforma y estado:")
        filas = s.execute(select(Corrida.plataforma_id, Corrida.estado, func.count(),
                                 func.avg(Corrida.duracion_seg), func.max(Corrida.inicio))
                          .group_by(Corrida.plataforma_id, Corrida.estado)).all()
        for plat, estado, n, dur, ultima in filas:
            print(f"  {plat:<14}{estado:<18}{n:>5}  dur. media {dur or 0:5.1f}s  última {ultima}")
        if not filas:
            print("  (todavía no hay corridas)")

        print("\nObservaciones con coincidencia por plataforma:")
        filas = s.execute(select(Observacion.plataforma_id, Observacion.match_nivel, func.count())
                          .group_by(Observacion.plataforma_id, Observacion.match_nivel)).all()
        for plat, nivel, n in filas:
            print(f"  {plat:<14}{nivel or 'sin match':<14}{n:>6}")

        r = s.execute(select(func.min(Venta.fecha), func.max(Venta.fecha), func.sum(Venta.unidades))).one()
        print(f"\nVentas: {r[0]} a {r[1]}, {r[2] or 0:,} unidades")
        for fuente, n in s.execute(select(PrecioCompetenciaDiario.fuente, func.count())
                                   .group_by(PrecioCompetenciaDiario.fuente)).all():
            print(f"Precios de competencia diarios ({fuente}): {n:,} filas")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m dpdf", description="DPDF-Engine: plataforma de pronóstico de ventas")
    sub = ap.add_subparsers(dest="comando", required=True)

    sub.add_parser("init-db", help="crea las tablas y carga el catálogo").set_defaults(fn=cmd_init_db)

    p = sub.add_parser("robots", help="muestra qué permite el robots.txt de cada plataforma")
    p.add_argument("--plataformas", type=_lista)
    p.set_defaults(fn=cmd_robots)

    p = sub.add_parser("scrape", help="busca los productos en las plataformas")
    p.add_argument("--plataformas", type=_lista, help="ej. mercadolibre,amazon (por defecto todas)")
    p.add_argument("--productos", type=_lista, help="ej. mouse,teclado (por defecto todos)")
    p.add_argument("--max-items", type=int, default=40, help="publicaciones por búsqueda")
    p.add_argument("--sin-pausa", action="store_true", help="solo para pruebas locales")
    p.set_defaults(fn=cmd_scrape)

    p = sub.add_parser("reprocesar", help="vuelve a extraer desde el HTML crudo guardado")
    p.add_argument("--fecha", required=True, help="AAAA-MM-DD")
    p.add_argument("--plataforma")
    p.add_argument("--max-items", type=int, default=40)
    p.set_defaults(fn=cmd_reprocesar)

    sub.add_parser("agregar", help="recalcula la serie diaria de precios").set_defaults(fn=cmd_agregar)

    p = sub.add_parser("simular", help="genera el historial de ventas simulado")
    p.add_argument("--desde", default="2023-01-01")
    p.add_argument("--hasta", help="por defecto, ayer")
    p.add_argument("--semilla", type=int, default=42)
    p.set_defaults(fn=cmd_simular)

    sub.add_parser("exportar", help="exporta tablas y dataset_modelo a data/clean").set_defaults(fn=cmd_exportar)
    sub.add_parser("resumen", help="estado de la base").set_defaults(fn=cmd_resumen)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
