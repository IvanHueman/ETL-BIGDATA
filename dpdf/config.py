"""Configuración central: rutas, base de datos y catálogos YAML."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

DIR_CONFIG = Path(os.getenv("DPDF_DIR_CONFIG", RAIZ / "config"))
DIR_DATOS = Path(os.getenv("DPDF_DIR_DATOS", RAIZ / "data"))
DIR_CRUDO = DIR_DATOS / "raw"      # capa bronce: HTML tal como llegó
DIR_LIMPIO = DIR_DATOS / "clean"   # capa plata/oro: tablas exportadas

ZONA_HORARIA = "America/Mexico_City"


def database_url() -> str:
    """SQLite local por defecto; Postgres/TimescaleDB si se define DATABASE_URL."""
    por_defecto = f"sqlite:///{(DIR_DATOS / 'dpdf.db').as_posix()}"
    return os.getenv("DATABASE_URL", por_defecto)


def asegurar_directorios() -> None:
    for d in (DIR_DATOS, DIR_CRUDO, DIR_LIMPIO):
        d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=None)
def _cargar_yaml(nombre: str) -> dict:
    with open(DIR_CONFIG / nombre, encoding="utf-8") as f:
        return yaml.safe_load(f)


def productos() -> list[dict]:
    return _cargar_yaml("productos.yaml")["productos"]


def producto(producto_id: str) -> dict:
    for p in productos():
        if p["id"] == producto_id:
            return p
    raise KeyError(f"Producto desconocido: {producto_id}")


def plataformas() -> dict[str, dict]:
    datos = _cargar_yaml("plataformas.yaml")["plataformas"]
    return {pid: {"id": pid, **conf} for pid, conf in datos.items()}


def user_agent() -> str:
    return os.getenv("DPDF_USER_AGENT") or _cargar_yaml("plataformas.yaml")["user_agent"]
