# ETL-BIGDATA

Repositorio para el proyecto ETL de la materia Big Data.

## DPDF-Engine: módulo de datos (sprint 1)

Prototipo de plataforma de Big Data para el pronóstico de ventas. IPN UPIICSA, Equipo 7.

Este primer sprint cubre la **adquisición y preparación de datos**:

```
plataformas ─► robots.txt ─► descarga con pausas ─► HTML crudo (data/raw)
            ─► extracción ─► precio a MXN ─► matching ─► base de datos ─► serie diaria de precios ─┐
simulador de ventas (10 productos, 2023 → ayer) ──────────────────────────────────────────────────┴─► dataset_modelo
```

El modelado (baseline vs Random Forest/XGBoost), la simulación de inventario y el dashboard son los siguientes sprints.

## Instalación

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows   (Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env          # Linux/Mac: cp .env.example .env
python -m pytest                # 49 pruebas, no hacen peticiones a internet
```

Opcional:
- **Páginas con JavaScript** (AliExpress, Temu): `pip install playwright` y `playwright install chromium`.
- **Postgres + TimescaleDB**: `docker compose up -d` y descomentar `DATABASE_URL` en `.env`. Sin esto se usa SQLite (`data/dpdf.db`).

## Uso

```bash
python -m dpdf init-db                     # crea tablas y carga el catálogo
python -m dpdf robots                      # qué permite el robots.txt de cada plataforma (córranlo primero)
python -m dpdf simular                     # historial de ventas simulado 2023-01-01 → ayer
python -m dpdf scrape --plataformas mercadolibre --productos mouse   # prueba chica
python -m dpdf scrape                      # todas las plataformas y productos (~15-20 min por las pausas)
python -m dpdf resumen                     # estado de la base
python -m dpdf exportar                    # tablas y dataset_modelo a data/clean (CSV y Parquet)
```

Para acumular historia, programen `python -m dpdf scrape` **una vez al día** (Programador de tareas de Windows o `cron`). La serie de precios solo crece con el calendario: entre antes empiecen, más datos tendrán.

## Catálogo (config/productos.yaml)

| id | Producto | Modelo de referencia |
|---|---|---|
| mouse | Mouse inalámbrico | Logitech M170 |
| audifonos | Audífonos Bluetooth TWS | Xiaomi Redmi Buds 6 Active |
| teclado | Teclado mecánico | Redragon Kumara K552 |
| cargador | Cargador USB-C 20 W | Ugreen 20 W PD |
| power_bank | Power bank 10000 mAh | Xiaomi Power Bank 10000 mAh |
| microsd | Memoria microSD 128 GB | SanDisk Ultra 128 GB |
| hub_usbc | Hub USB-C 5 en 1 | Ugreen 5 en 1 HDMI 4K |
| smartband | Pulsera inteligente | Xiaomi Smart Band 9 |
| bocina | Bocina Bluetooth portátil | JBL Go 4 |
| cable_usbc | Cable USB-C a USB-C 60 W | Ugreen 60 W 1 m |

Verifiquen que los modelos de referencia sigan a la venta; cambiarlos es editar el YAML.

**Matching.** Comparar precios solo sirve si es el mismo artículo, así que cada publicación se clasifica como:
- `exacto`: es el modelo de referencia (marca + modelo + precio razonable).
- `equivalente`: misma categoría y especificación, otra marca o genérico. Es lo que más aparece en Temu, AliExpress y Alibaba.

Para la serie diaria se usa la mediana de los `exacto` y, si no hubo ninguno ese día, la de los `equivalente` (queda anotado en `nivel_match`).

## Plataformas

| Plataforma | Cómo se extrae | Qué esperar |
|---|---|---|
| Mercado Libre | Selectores de tarjetas `poly-card` | Debería funcionar sin JavaScript |
| Amazon MX | Selectores de resultados (`data-asin`) | Es frecuente que responda con CAPTCHA a crawlers |
| AliExpress | JSON incrustado en el HTML; con Playwright, tarjetas | Sin Playwright solo lee el HTML inicial |
| Temu | Tarjetas con Playwright | Experimental: anti-bot muy fuerte, lo normal es `bloqueado` |
| Alibaba | Tarjetas por enlace | Precios de **mayoreo** en USD por pieza, con pedido mínimo en `notas`; se analizan aparte |

Lo que vimos en los robots.txt el 9-oct-2026:
- **AliExpress:** el grupo `*` no prohíbe `/w/` (búsquedas).
- **Temu:** el grupo `*` solo tiene un `Allow`. A bots con nombre (Googlebot, etc.) les prohíbe `/w/` y `/s/`.
- **Mercado Libre, Amazon y Alibaba:** no los pudimos leer desde aquí.

Los sitios cambian estas reglas: `python -m dpdf robots` las revisa en el momento, y el scraper las vuelve a revisar antes de cada búsqueda.

## Controles éticos (los del documento, implementados)

- **robots.txt antes de cada petición**, con comodines (RFC 9309). Si no se puede leer el robots.txt (error 5xx o sin conexión), se asume que está prohibido.
- **Pausas aleatorias por dominio** (`pausa_seg` en `config/plataformas.yaml`) y respeto de `Crawl-delay`.
- **User-Agent que identifica al proyecto.** No se finge ser un navegador.
- **Sin rotación de IPs ni evasión de CAPTCHA.** Si una plataforma bloquea, la corrida se detiene en esa plataforma y lo registra como `bloqueado`.
- **Solo datos públicos de oferta:** título, precio, envío y vendedor-tienda. No se capturan datos personales.

Ojo: que robots.txt permita una ruta **no** significa que los términos de uso del sitio permitan la extracción automatizada; varios la restringen. Para el reporte, documenten esto. Donde exista, prefieran la API oficial: Mercado Libre tiene API con token OAuth, y AliExpress y Amazon tienen programas de afiliados con API.

## Si una plataforma deja de dar resultados

Cada página descargada se guarda comprimida en `data/raw/<plataforma>/<fecha>/`. Si una plataforma pasa a `sin_resultados`:
1. Abran el HTML crudo.
2. Ajusten los selectores en `dpdf/scraping/plataformas.py`.
3. Ejecuten `python -m dpdf reprocesar --fecha AAAA-MM-DD` para volver a extraer, sin repetir peticiones.

## Ventas simuladas

No hay ventas reales de estos productos, así que `python -m dpdf simular` genera el historial diario de una tienda en línea ficticia (`dpdf/simulacion/ventas.py`). Incluye:
- **Calendario comercial mexicano:** Hot Sale, El Buen Fin, Navidad, Reyes, cuesta de enero, regreso a clases, Día del Padre y quincenas.
- **Promociones propias.**
- **Efecto del precio propio y del precio de la competencia.**

Los parámetros reales quedan en `data/clean/verdad_simulacion.json`, así que pueden comprobar si el modelo recupera, por ejemplo, la elasticidad precio. En el reporte digan claramente que las ventas son simuladas. Cuando haya ventas reales, se cargan en la tabla `ventas` con otra `fuente`.

## Tablas

| Tabla | Contenido |
|---|---|
| `productos`, `plataformas` | Catálogo (se carga desde los YAML) |
| `corridas_scraping` | Una fila por búsqueda: estado, HTTP, duración, estrategia, archivo crudo. La duración es la evidencia para **H3** |
| `observaciones_precio` | Cada publicación encontrada: precio original, moneda, tipo de cambio usado, precio MXN, nivel de match |
| `precios_competencia_diario` | Serie diaria por producto y plataforma (`fuente` = `scraping` o `simulado`) |
| `ventas` | Ventas diarias por producto |

`dataset_modelo` (en `exportar`) junta las ventas con el precio de competencia de cada plataforma (`comp_mercadolibre`, `comp_amazon`, …). Si para una fecha hay precio scrapeado y simulado, gana el scrapeado. Es la entrada para el siguiente sprint.

## Estructura

```
config/             catálogo de productos y plataformas (YAML)
dpdf/scraping/      robots, ritmo, descarga, extractores, adaptadores, orquestador
dpdf/processing/    precios, tipo de cambio, matching, agregado diario
dpdf/simulacion/    generador de ventas
dpdf/db.py          esquema de la base
dpdf/cli.py         comandos
tests/              pruebas con páginas de ejemplo (sin internet)
```
