"""Piezas comunes del instrumento de captura.

- GuardiaRobots: revisa robots.txt ANTES de cada petición (RFC 9309, con comodines vía protego).
- LimitadorRitmo: pausas aleatorias por dominio y respeto de Crawl-delay.
- Navegador: descarga páginas con httpx o, si hace falta JavaScript, con Playwright.
- detectar_bloqueo: reconoce CAPTCHA/403/429. Si un sitio bloquea, el sistema SE DETIENE
  en esa plataforma; no rota IPs ni intenta evadir el bloqueo.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote, quote_plus, urlparse

import httpx
from bs4 import BeautifulSoup
from protego import Protego

from .extract import ItemCrudo, desde_json_embebido, desde_jsonld


@dataclass
class Pagina:
    url: str
    http_status: int | None
    html: str | None
    error: str | None = None
    renderizada_js: bool = False


# ---------------------------------------------------------------- robots.txt

class GuardiaRobots:
    """Cachea un robots.txt por host. Semántica RFC 9309:
    200 -> se aplican las reglas | 4xx -> todo permitido | 5xx o sin conexión -> nada permitido."""

    def __init__(self, user_agent: str, obtener: Callable[[str], tuple[int | None, str]] | None = None):
        self.user_agent = user_agent
        self._obtener = obtener or self._obtener_httpx
        self._cache: dict[str, tuple[str, Protego | None]] = {}

    def _obtener_httpx(self, url: str) -> tuple[int | None, str]:
        try:
            r = httpx.get(url, headers={"User-Agent": self.user_agent}, timeout=20, follow_redirects=True)
            return r.status_code, r.text
        except httpx.HTTPError:
            return None, ""

    def _reglas(self, url: str) -> tuple[str, Protego | None]:
        p = urlparse(url)
        host = f"{p.scheme}://{p.netloc}"
        if host not in self._cache:
            status, cuerpo = self._obtener(f"{host}/robots.txt")
            if status == 200:
                self._cache[host] = ("reglas", Protego.parse(cuerpo))
            elif status is not None and 400 <= status < 500:
                self._cache[host] = ("sin_robots", None)
            else:
                self._cache[host] = ("inaccesible", None)
        return self._cache[host]

    def permitido(self, url: str) -> tuple[bool, str]:
        tipo, reglas = self._reglas(url)
        if tipo == "sin_robots":
            return True, "sin robots.txt (4xx): permitido"
        if tipo == "inaccesible":
            return False, "robots.txt inaccesible (5xx o sin conexión): se asume prohibido"
        ok = reglas.can_fetch(url, self.user_agent)
        return ok, "permitido por robots.txt" if ok else "prohibido por robots.txt"

    def crawl_delay(self, url: str) -> float | None:
        tipo, reglas = self._reglas(url)
        if reglas is None:
            return None
        d = reglas.crawl_delay(self.user_agent)
        return float(d) if d else None


# ---------------------------------------------------------------- ritmo

class LimitadorRitmo:
    def __init__(self, activo: bool = True):
        self.activo = activo
        self._ultimo: dict[str, float] = {}

    def esperar(self, url: str, pausa: tuple[float, float], crawl_delay: float | None = None) -> float:
        if not self.activo:
            return 0.0
        dominio = urlparse(url).netloc
        objetivo = max(random.uniform(*pausa), crawl_delay or 0)
        transcurrido = time.monotonic() - self._ultimo.get(dominio, 0)
        espera = max(0.0, objetivo - transcurrido)
        if espera:
            time.sleep(espera)
        self._ultimo[dominio] = time.monotonic()
        return espera


# ---------------------------------------------------------------- descarga

class Navegador:
    def __init__(self, user_agent: str, timeout: float = 30):
        self.user_agent = user_agent
        self.cliente = httpx.Client(
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "es-MX,es;q=0.9",
            },
            timeout=timeout,
            follow_redirects=True,
        )
        self._pw = None
        self._browser = None

    def obtener(self, url: str, render_js: bool = False) -> Pagina:
        if render_js and self._playwright_disponible():
            return self._obtener_js(url)
        try:
            r = self.cliente.get(url)
            return Pagina(url=str(r.url), http_status=r.status_code, html=r.text)
        except httpx.HTTPError as e:
            return Pagina(url=url, http_status=None, html=None, error=f"{e.__class__.__name__}: {e}")

    @staticmethod
    def _playwright_disponible() -> bool:
        try:
            import playwright.sync_api  # noqa: F401
            return True
        except ImportError:
            return False

    def _obtener_js(self, url: str) -> Pagina:
        from playwright.sync_api import sync_playwright
        try:
            if self._browser is None:
                self._pw = sync_playwright().start()
                self._browser = self._pw.chromium.launch(headless=True)
            contexto = self._browser.new_context(user_agent=self.user_agent, locale="es-MX")
            pagina = contexto.new_page()
            resp = pagina.goto(url, wait_until="domcontentloaded", timeout=45_000)
            pagina.wait_for_timeout(2500)
            for _ in range(3):  # baja un poco para que carguen las tarjetas diferidas
                pagina.mouse.wheel(0, 2200)
                pagina.wait_for_timeout(900)
            html = pagina.content()
            contexto.close()
            return Pagina(url=url, http_status=resp.status if resp else None, html=html, renderizada_js=True)
        except Exception as e:
            return Pagina(url=url, http_status=None, html=None, error=f"{e.__class__.__name__}: {e}")

    def cerrar(self) -> None:
        self.cliente.close()
        if self._browser is not None:
            self._browser.close()
            self._pw.stop()


# ---------------------------------------------------------------- bloqueo

MARCAS_BLOQUEO = (
    "captcha", "robot check", "validatecaptcha", "are you a robot", "unusual traffic",
    "tráfico inusual", "verifica que eres humano", "verify you are human", "slide to verify",
    "desliza para verificar", "punish", "access denied", "acceso denegado", "x5secdata",
)


def detectar_bloqueo(http_status: int | None, html: str | None) -> str | None:
    """Se llama SOLO cuando no se extrajo ningún producto (las páginas normales también
    pueden mencionar 'captcha' en sus scripts)."""
    if http_status in (401, 403, 429):
        return f"HTTP {http_status}"
    bajo = (html or "")[:300_000].lower()
    for marca in MARCAS_BLOQUEO:
        if marca in bajo:
            return f"página de verificación ('{marca}')"
    if http_status == 503:
        return "HTTP 503"
    return None


# ---------------------------------------------------------------- adaptador

class AdaptadorBase:
    """Cada plataforma hereda de aquí y solo implementa `extraer_especifico`."""

    id: str = "base"

    def __init__(self, conf: dict):
        self.conf = conf
        self.url_base = conf["url_base"]

    def url_busqueda(self, consulta: str) -> str:
        c = " ".join(consulta.lower().split())
        return self.conf["url_busqueda"].format(
            consulta_guiones=quote(c.replace(" ", "-"), safe="-"),
            consulta_mas=quote_plus(c),
            consulta_url=quote(c),
        )

    def extraer_especifico(self, soup: BeautifulSoup, html: str) -> list[ItemCrudo]:
        return []

    def extraer(self, html: str) -> tuple[list[ItemCrudo], str | None]:
        soup = BeautifulSoup(html, "lxml")
        for estrategia, funcion in (
            ("selectores", lambda: self.extraer_especifico(soup, html)),
            ("jsonld", lambda: desde_jsonld(soup, self.url_base)),
            ("json_embebido", lambda: desde_json_embebido(soup, self.url_base)),
        ):
            items = [i for i in funcion() if i.titulo]
            if items:
                return items, estrategia
        return [], None
