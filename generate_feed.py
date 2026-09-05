from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
import re

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from feedgen.feed import FeedGenerator


BASE_URL = "https://www.r-pharm.com"
NEWS_URL = "https://www.r-pharm.com/en/media-center/news"
OUTPUT_FILE = Path("docs/feed.xml")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

MONTH_PATTERN = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
    r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)

DATE_PATTERNS = [
    rf"\b\d{{1,2}}\s+{MONTH_PATTERN}\s+\d{{4}}\b",
    rf"\b{MONTH_PATTERN}\s+\d{{1,2}},?\s+\d{{4}}\b",
    r"\b\d{1,2}[./-]\d{1,2}[./-]\d{4}\b",
]


def descargar_pagina(url):
    respuesta = requests.get(
        url,
        headers=HEADERS,
        timeout=40,
    )
    respuesta.raise_for_status()
    return respuesta.text


def es_enlace_noticia(url):
    ruta = urlparse(url).path.rstrip("/")

    prefijo = "/en/media-center/news/"

    return (
        ruta.startswith(prefijo)
        and ruta != "/en/media-center/news"
        and len(ruta.split("/")) > 4
    )


def limpiar_texto(texto):
    return " ".join(texto.split()).strip()


def buscar_fecha(elemento):
    time_element = elemento.find("time")

    if time_element:
        fecha = time_element.get("datetime") or time_element.get_text(
            " ", strip=True
        )

        try:
            return date_parser.parse(fecha, fuzzy=True)
        except (ValueError, TypeError, OverflowError):
            pass

    texto = limpiar_texto(elemento.get_text(" ", strip=True))

    for patron in DATE_PATTERNS:
        coincidencia = re.search(patron, texto, flags=re.IGNORECASE)

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


def buscar_descripcion(elemento, titulo):
    parrafos = elemento.find_all(["p", "div", "span"])

    for parrafo in parrafos:
        texto = limpiar_texto(parrafo.get_text(" ", strip=True))

        if (
            texto
            and texto != titulo
            and len(texto) >= 35
            and len(texto) <= 600
            and not any(
                re.fullmatch(patron, texto, flags=re.IGNORECASE)
                for patron in DATE_PATTERNS
            )
        ):
            return texto

    return ""


def obtener_noticias():
    año_actual = datetime.now(timezone.utc).year

    paginas = [
        NEWS_URL,
        f"{NEWS_URL}?year={año_actual}",
        f"{NEWS_URL}?year={año_actual - 1}",
        f"{NEWS_URL}?year={año_actual - 2}",
    ]

    noticias = {}
    errores = []

    for pagina in paginas:
        try:
            html = descargar_pagina(pagina)
        except requests.RequestException as error:
            errores.append(f"{pagina}: {error}")
            continue

        soup = BeautifulSoup(html, "html.parser")

        for enlace in soup.find_all("a", href=True):
            url = urljoin(BASE_URL, enlace["href"])

            if not es_enlace_noticia(url):
                continue

            titulo = limpiar_texto(enlace.get_text(" ", strip=True))

            if len(titulo) < 5:
                imagen = enlace.find("img")
                titulo = limpiar_texto(
                    imagen.get("alt", "") if imagen else ""
                )

            if len(titulo) < 5:
                continue

            contenedor = enlace

            for _ in range(4):
                if contenedor.parent is None:
                    break

                contenedor = contenedor.parent

                if contenedor.name in ["article", "li"]:
                    break

            fecha = buscar_fecha(contenedor)
            descripcion = buscar_descripcion(contenedor, titulo)

            noticias[url] = {
                "titulo": titulo,
                "url": url,
                "fecha": fecha,
                "descripcion": descripcion,
            }

    if not noticias:
        detalle = "\n".join(errores)

        raise RuntimeError(
            "No se encontraron noticias de R-Pharm. "
            "La RSS anterior no será eliminada.\n"
            f"{detalle}"
        )

    fecha_minima = datetime(1970, 1, 1)

    return sorted(
        noticias.values(),
        key=lambda noticia: (
            noticia["fecha"].replace(tzinfo=None)
            if noticia["fecha"]
            else fecha_minima
        ),
        reverse=True,
    )


def crear_rss(noticias):
    feed = FeedGenerator()

    feed.id(NEWS_URL)
    feed.title("R-Pharm – News")
    feed.link(href=NEWS_URL, rel="alternate")
    feed.link(
        href=(
            "https://raw.githubusercontent.com/"
            "plis2100/rss-rpharm/main/docs/feed.xml"
        ),
        rel="self",
    )
    feed.description(
        "Latest news and announcements from R-Pharm"
    )
    feed.language("en")
    feed.lastBuildDate(datetime.now(timezone.utc))

    for noticia in noticias[:100]:
        entrada = feed.add_entry()

        entrada.id(noticia["url"])
        entrada.title(noticia["titulo"])
        entrada.link(href=noticia["url"])

        descripcion = (
            noticia["descripcion"]
            or f"Read the complete announcement on R-Pharm: "
               f"{noticia['titulo']}"
        )

        entrada.description(descripcion)

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
    print(f"Número de noticias: {len(noticias)}")


if __name__ == "__main__":
    noticias_obtenidas = obtener_noticias()
    crear_rss(noticias_obtenidas)
