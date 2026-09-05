from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
import re

from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from feedgen.feed import FeedGenerator
from playwright.sync_api import sync_playwright


BASE_URL = "https://www.r-pharm.com"
NEWS_URL = "https://www.r-pharm.com/en/media-center/news"
OUTPUT_FILE = Path("docs/feed.xml")

MONTHS = (
    r"January|February|March|April|May|June|July|August|"
    r"September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
)

DATE_PATTERNS = [
    rf"\b\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}}\b",
    rf"\b(?:{MONTHS})\s+\d{{1,2}},?\s+\d{{4}}\b",
    r"\b\d{1,2}[./-]\d{1,2}[./-]\d{4}\b",
]


def limpiar(texto):
    return " ".join((texto or "").split()).strip()


def es_noticia(url):
    ruta = urlparse(url).path.rstrip("/").lower()
    prefijo = "/en/media-center/news/"

    return (
        ruta.startswith(prefijo)
        and ruta != "/en/media-center/news"
        and len(ruta) > len(prefijo)
    )


def encontrar_fecha(contenedor):
    etiqueta_time = contenedor.find("time")

    if etiqueta_time:
        texto_fecha = (
            etiqueta_time.get("datetime")
            or etiqueta_time.get_text(" ", strip=True)
        )

        try:
            return date_parser.parse(texto_fecha, fuzzy=True)
        except (ValueError, TypeError, OverflowError):
            pass

    texto = limpiar(contenedor.get_text(" ", strip=True))

    for patron in DATE_PATTERNS:
        coincidencia = re.search(patron, texto, re.IGNORECASE)

        if coincidencia:
            try:
                return date_parser.parse(
                    coincidencia.group(0),
                    fuzzy=True,
                    dayfirst=True,
                )
            except (ValueError, TypeError, OverflowError):
                continue

    return None


def encontrar_descripcion(contenedor, titulo):
    for elemento in contenedor.find_all(["p", "div", "span"]):
        texto = limpiar(elemento.get_text(" ", strip=True))

        if (
            texto
            and texto != titulo
            and 40 <= len(texto) <= 500
            and titulo.lower() not in texto.lower()
        ):
            return texto

    return ""


def descargar_paginas():
    año = datetime.now(timezone.utc).year

    urls = [
        NEWS_URL,
        f"{NEWS_URL}?year={año}",
        f"{NEWS_URL}?year={año - 1}",
        f"{NEWS_URL}?year={año - 2}",
        f"{NEWS_URL}?year={año - 3}",
    ]

    paginas = []

    with sync_playwright() as playwright:
        navegador = playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        contexto = navegador.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            viewport={"width": 1440, "height": 1200},
        )

        pagina = contexto.new_page()

        for url in urls:
            print(f"Abriendo: {url}")

            try:
                pagina.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=90000,
                )

                pagina.wait_for_timeout(7000)

                # Desplazamiento para activar contenido de carga diferida.
                for _ in range(5):
                    pagina.mouse.wheel(0, 1500)
                    pagina.wait_for_timeout(1000)

                paginas.append(pagina.content())

                print(
                    f"Enlaces encontrados en la página: "
                    f"{pagina.locator('a').count()}"
                )

            except Exception as error:
                print(f"No se pudo abrir {url}: {error}")

        navegador.close()

    return paginas


def obtener_noticias():
    paginas = descargar_paginas()
    noticias = {}

    for html in paginas:
        soup = BeautifulSoup(html, "html.parser")

        for enlace in soup.find_all("a", href=True):
            url = urljoin(BASE_URL, enlace.get("href", ""))

            if not es_noticia(url):
                continue

            titulo = limpiar(enlace.get_text(" ", strip=True))

            if len(titulo) < 5:
                imagen = enlace.find("img")

                if imagen:
                    titulo = limpiar(imagen.get("alt", ""))

            if len(titulo) < 5:
                continue

            contenedor = enlace

            for _ in range(5):
                if not contenedor.parent:
                    break

                contenedor = contenedor.parent

                if contenedor.name in ("article", "li"):
                    break

            fecha = encontrar_fecha(contenedor)
            descripcion = encontrar_descripcion(contenedor, titulo)

            noticias[url] = {
                "titulo": titulo,
                "url": url,
                "fecha": fecha,
                "descripcion": descripcion,
            }

    if not noticias:
        raise RuntimeError(
            "R-Pharm no mostró enlaces de noticias después de cargar "
            "la página con Chromium. La RSS anterior no será eliminada."
        )

    fecha_antigua = datetime(1970, 1, 1)

    resultado = sorted(
        noticias.values(),
        key=lambda noticia: (
            noticia["fecha"].replace(tzinfo=None)
            if noticia["fecha"]
            else fecha_antigua
        ),
        reverse=True,
    )

    print(f"Noticias encontradas: {len(resultado)}")

    return resultado


def crear_rss(noticias):
    feed = FeedGenerator()

    feed.id(NEWS_URL)
    feed.title("R-Pharm – News")
    feed.description(
        "Latest news and announcements from R-Pharm"
    )
    feed.language("en")
    feed.link(href=NEWS_URL, rel="alternate")
    feed.link(
        href=(
            "https://raw.githubusercontent.com/"
            "plis2100/rss-rpharm/main/docs/feed.xml"
        ),
        rel="self",
    )
    feed.lastBuildDate(datetime.now(timezone.utc))

    for noticia in noticias[:100]:
        entrada = feed.add_entry()

        entrada.id(noticia["url"])
        entrada.title(noticia["titulo"])
        entrada.link(href=noticia["url"])

        entrada.description(
            noticia["descripcion"]
            or f"Read the complete announcement: {noticia['titulo']}"
        )

        if noticia["fecha"]:
            fecha = noticia["fecha"]

            if fecha.tzinfo is None:
                fecha = fecha.replace(tzinfo=timezone.utc)

            entrada.pubDate(format_datetime(fecha))

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    feed.rss_file(
        str(OUTPUT_FILE),
        pretty=True,
        encoding="UTF-8",
    )

    print(f"RSS creada correctamente: {OUTPUT_FILE}")


if __name__ == "__main__":
    noticias_obtenidas = obtener_noticias()
    crear_rss(noticias_obtenidas)
