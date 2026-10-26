"""Esquema de la base de datos (SQLAlchemy 2.0).

Funciona igual en SQLite (por defecto, sin instalar nada) y en PostgreSQL + TimescaleDB
(docker compose up). En Postgres, `ventas` y `precios_competencia_diario` se convierten en
hypertables de TimescaleDB.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    create_engine, text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from . import config


class Base(DeclarativeBase):
    pass


class Producto(Base):
    __tablename__ = "productos"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120))
    categoria: Mapped[str] = mapped_column(String(60))
    marca: Mapped[str] = mapped_column(String(60))
    modelo_referencia: Mapped[str] = mapped_column(String(160))
    precio_base_mxn: Mapped[float] = mapped_column(Float)


class Plataforma(Base):
    __tablename__ = "plataformas"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120))
    url_base: Mapped[str] = mapped_column(String(200))
    moneda: Mapped[str] = mapped_column(String(3))
    es_mayoreo: Mapped[bool] = mapped_column(Boolean, default=False)


class Corrida(Base):
    """Una petición de búsqueda (plataforma x producto x tipo de consulta)."""
    __tablename__ = "corridas_scraping"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plataforma_id: Mapped[str] = mapped_column(ForeignKey("plataformas.id"))
    producto_id: Mapped[str] = mapped_column(ForeignKey("productos.id"))
    tipo_consulta: Mapped[str] = mapped_column(String(20))           # modelo | generica
    url: Mapped[str] = mapped_column(Text)
    inicio: Mapped[datetime] = mapped_column(DateTime)
    fin: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duracion_seg: Mapped[float | None] = mapped_column(Float, nullable=True)
    # ok | sin_resultados | bloqueado | prohibido_robots | error | omitido
    estado: Mapped[str] = mapped_column(String(20), default="en_curso")
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estrategia: Mapped[str | None] = mapped_column(String(30), nullable=True)
    n_items: Mapped[int] = mapped_column(Integer, default=0)
    n_match: Mapped[int] = mapped_column(Integer, default=0)
    archivo_crudo: Mapped[str | None] = mapped_column(Text, nullable=True)
    mensaje: Mapped[str | None] = mapped_column(Text, nullable=True)


class Observacion(Base):
    """Una publicación encontrada en una búsqueda, con su precio normalizado a MXN."""
    __tablename__ = "observaciones_precio"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    corrida_id: Mapped[int] = mapped_column(ForeignKey("corridas_scraping.id"))
    plataforma_id: Mapped[str] = mapped_column(ForeignKey("plataformas.id"))
    producto_id: Mapped[str] = mapped_column(ForeignKey("productos.id"))
    capturado_en: Mapped[datetime] = mapped_column(DateTime)   # hora local de CDMX
    titulo: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    id_externo: Mapped[str | None] = mapped_column(String(80), nullable=True)
    precio_texto: Mapped[str | None] = mapped_column(String(80), nullable=True)
    moneda: Mapped[str] = mapped_column(String(3))
    precio: Mapped[float] = mapped_column(Float)                 # mínimo si es rango
    precio_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    precio_mxn: Mapped[float] = mapped_column(Float)
    tipo_cambio: Mapped[float] = mapped_column(Float)
    fuente_tipo_cambio: Mapped[str] = mapped_column(String(30))
    vendidos_texto: Mapped[str | None] = mapped_column(String(80), nullable=True)
    envio_texto: Mapped[str | None] = mapped_column(String(120), nullable=True)
    vendedor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notas: Mapped[str | None] = mapped_column(String(200), nullable=True)
    patrocinado: Mapped[bool] = mapped_column(Boolean, default=False)
    match_nivel: Mapped[str | None] = mapped_column(String(15), nullable=True)  # exacto | equivalente
    match_score: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (
        Index("ix_obs_prod_plat_fecha", "producto_id", "plataforma_id", "capturado_en"),
    )


class PrecioCompetenciaDiario(Base):
    """Precio de competencia agregado por día (serie de tiempo para el modelo)."""
    __tablename__ = "precios_competencia_diario"
    fecha: Mapped[date] = mapped_column(Date, primary_key=True)
    producto_id: Mapped[str] = mapped_column(ForeignKey("productos.id"), primary_key=True)
    plataforma_id: Mapped[str] = mapped_column(ForeignKey("plataformas.id"), primary_key=True)
    fuente: Mapped[str] = mapped_column(String(15), primary_key=True)   # scraping | simulado
    precio_mediana_mxn: Mapped[float] = mapped_column(Float)
    precio_min_mxn: Mapped[float] = mapped_column(Float)
    n_obs: Mapped[int] = mapped_column(Integer)
    nivel_match: Mapped[str] = mapped_column(String(15))


class Venta(Base):
    """Ventas diarias propias. En el prototipo vienen del simulador (fuente='simulado')."""
    __tablename__ = "ventas"
    fecha: Mapped[date] = mapped_column(Date, primary_key=True)
    producto_id: Mapped[str] = mapped_column(ForeignKey("productos.id"), primary_key=True)
    unidades: Mapped[int] = mapped_column(Integer)
    precio_unitario_mxn: Mapped[float] = mapped_column(Float)
    ingresos_mxn: Mapped[float] = mapped_column(Float)
    evento: Mapped[str | None] = mapped_column(String(30), nullable=True)
    fuente: Mapped[str] = mapped_column(String(15), default="simulado")


# --------------------------------------------------------------------------------------

def get_engine(url: str | None = None) -> Engine:
    url = url or config.database_url()
    if url.startswith("sqlite"):
        config.asegurar_directorios()
    return create_engine(url, future=True)


def sesion(engine: Engine) -> Session:
    return sessionmaker(bind=engine, future=True)()


def init_db(engine: Engine) -> list[str]:
    """Crea las tablas, carga el catálogo y, en Postgres, activa TimescaleDB."""
    avisos: list[str] = []
    Base.metadata.create_all(engine)

    with sesion(engine) as s:
        for p in config.productos():
            s.merge(Producto(
                id=p["id"], nombre=p["nombre"], categoria=p["categoria"], marca=p["marca"],
                modelo_referencia=p["modelo_referencia"], precio_base_mxn=p["precio_base_mxn"],
            ))
        for pl in config.plataformas().values():
            s.merge(Plataforma(
                id=pl["id"], nombre=pl["nombre"], url_base=pl["url_base"],
                moneda=pl["moneda"], es_mayoreo=bool(pl.get("es_mayoreo", False)),
            ))
        s.commit()

    if engine.dialect.name == "postgresql":
        try:
            with engine.begin() as con:
                con.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
                for tabla in ("ventas", "precios_competencia_diario"):
                    con.execute(text(
                        f"SELECT create_hypertable('{tabla}', 'fecha', "
                        "if_not_exists => TRUE, migrate_data => TRUE)"
                    ))
        except Exception as e:  # la extensión no está disponible: se sigue con Postgres normal
            avisos.append(f"TimescaleDB no se activó ({e.__class__.__name__}); se usan tablas normales.")
    return avisos
