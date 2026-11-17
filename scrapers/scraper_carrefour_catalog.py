# ============================================================
# NOTA IMPORTANTE: CLOUDFLARE BOT MANAGEMENT
# ============================================================
#
# carrefour.es tiene Cloudflare Bot Management activo (cookie
# __cf_bm). Comprobado con peticiones reales: rafagas de peticiones
# con menos de ~10s entre ellas devuelven un 403 con una pagina de
# reto "Just a moment..." en vez del HTML normal. Con pausas de
# 12-18s entre peticiones (ver REQUEST_DELAY_SECONDS) no se ha vuelto
# a reproducir. Por eso este scraper usa pausas mas largas que el de
# DIA - no reutilizar las constantes de scraper_dia_catalog.py.
#
# Un 403 de Cloudflare no distingue por status code de un fallo de
# red normal (requests.raise_for_status() lo trata igual), asi que
# cae en el mismo mecanismo de reintentos con backoff 5/15/45s.
# ============================================================

import hashlib
import json
import os
import random
import re
import sys
import time

import requests
from bs4 import BeautifulSoup


# ============================================================
# IMPORTAR ARCHIVOS DESDE LA CARPETA PRINCIPAL
# ============================================================

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


from db_writer import (
    start_scraping_log,
    finish_scraping_log,
    upsert_raw_scraped_product
)


# ============================================================
# CONFIGURACIÓN
# ============================================================

BASE_URL = "https://www.carrefour.es"

# Pares (nombre, url_base) de categorias a recorrer. La URL es la de
# la pagina 1 (offset=0); las siguientes paginas se construyen con
# build_page_url.
CATEGORIES = [
    (
        "Leche",
        "https://www.carrefour.es/supermercado/la-despensa/lacteos/"
        "leche/cat20093/c"
    ),
    (
        "Agua",
        "https://www.carrefour.es/supermercado/bebidas/aguas-y-zumos/"
        "agua/cat32590552/c"
    ),
    (
        "Pan de molde",
        "https://www.carrefour.es/supermercado/la-despensa/"
        "panaderia-bolleria-y-pasteleria/pan-de-molde/cat21618473/c"
    ),
    (
        "Platanos y bananas",
        "https://www.carrefour.es/supermercado/frescos/frutas/"
        "platanos-y-bananas/cat220011/c"
    ),
    (
        # Categoria mas amplia que la de DIA: incluye tambien
        # yogures con sabores, no solo naturales. No existe en
        # carrefour.es una categoria "solo naturales" accesible (la
        # que aparecia en el sitemap, cat32989566, devuelve 404
        # real). Decision confirmada con el usuario.
        "Yogures naturales y de sabores",
        "https://www.carrefour.es/supermercado/la-despensa/"
        "yogures-y-postres/yogur-natural-y-sabores/cat360008/c"
    ),
]

# Limite de paginas por categoria, para poder probar el scraper sin
# recorrer el catalogo entero.
MAX_PAGES_PER_CATEGORY = 1

# Productos por pagina en carrefour.es (comprobado con peticiones
# reales: siempre 24, tanto en el "page_size" de pagination como en
# el numero de entradas de impressions).
PAGE_SIZE = 24

# Espera entre reintentos de una misma peticion (segundos). Igual
# que en DIA.
RETRY_DELAYS_SECONDS = [5, 15, 45]

# Pausa aleatoria entre peticiones normales. Mas alta que en DIA
# (2-4s) porque por debajo de ~10s Cloudflare Bot Management empieza
# a devolver la pagina de reto "Just a moment..." (comprobado con
# peticiones reales, ver nota de cabecera).
REQUEST_DELAY_SECONDS = (12, 18)

SUPERMARKET_SLUG = "carrefour"
SCRAPER_NAME = "scraper_carrefour_catalog_v1"

# Se asigna dinamicamente antes de procesar cada categoria (ver
# scrape_category).
CATEGORY_NAME = None

REQUEST_TIMEOUT = 30


# ============================================================
# UTILIDADES DE TEXTO
# ============================================================

def clean_text(text):
    """
    Limpia espacios especiales, saltos de línea y espacios duplicados.
    """

    if text is None:
        return None

    cleaned = str(text)

    cleaned = cleaned.replace("\xa0", " ")
    cleaned = cleaned.replace("\n", " ")
    cleaned = cleaned.replace("\r", " ")
    cleaned = cleaned.replace("\t", " ")

    cleaned = re.sub(r"\s+", " ", cleaned)

    return cleaned.strip()


def normalize_text(text):
    """
    Normaliza el texto para comparaciones y generación de identificadores.
    """

    if text is None:
        return ""

    normalized = clean_text(text).lower()

    replacements = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ü": "u",
        "ñ": "n"
    }

    for original, replacement in replacements.items():
        normalized = normalized.replace(original, replacement)

    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)

    return normalized.strip()


def generate_external_id(product_name):
    """
    Genera un identificador provisional si no conseguimos obtener
    el ID real del producto.

    El mismo nombre normalizado producirá siempre el mismo ID.
    """

    normalized_name = normalize_text(product_name)

    name_hash = hashlib.sha256(
        normalized_name.encode("utf-8")
    ).hexdigest()[:24]

    return "carrefour_hash_" + name_hash


# ============================================================
# DESCARGA DE LA PÁGINA
# ============================================================

def fetch_page(url):
    """
    Descarga la página de catálogo.
    """

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    print("HTTP status:", response.status_code)
    print("HTML descargado:", len(response.text), "caracteres")

    return response.text


def fetch_page_with_retries(url):
    """
    Descarga una pagina con reintentos ante fallos de red, 429/503 y
    tambien ante el reto de Cloudflare (llega como 403, lo capta
    raise_for_status igual que cualquier otro error HTTP).

    Hasta 3 reintentos con espera creciente (5s, 15s, 45s). Si se
    agotan los reintentos, devuelve None y deja el error impreso
    para que el llamante lo registre y continue con la siguiente
    pagina o categoria.
    """

    last_error = None
    max_attempts = len(RETRY_DELAYS_SECONDS) + 1

    for attempt in range(1, max_attempts + 1):
        try:
            return fetch_page(url)

        except Exception as error:
            last_error = error

            print(
                "[FALLO PETICION]",
                url,
                "| intento",
                attempt,
                "de",
                max_attempts,
                "|",
                str(error)
            )

            if attempt <= len(RETRY_DELAYS_SECONDS):
                delay = RETRY_DELAYS_SECONDS[attempt - 1]

                print("Reintentando en", delay, "segundos...")

                time.sleep(delay)

    print(
        "[ERROR PAGINA]",
        url,
        "- se agotaron los reintentos:",
        str(last_error)
    )

    return None


def build_page_url(base_url, page_number):
    """
    Construye la URL de la pagina N de una categoria de carrefour.es.

    carrefour.es pagina con el parametro de query ?offset=N*24
    (comprobado con leche: offset=0 pagina 1, offset=24 pagina 2,
    offset=96 pagina 5/5). La pagina 1 no necesita el parametro.
    """

    if page_number <= 1:
        return base_url

    offset = (page_number - 1) * PAGE_SIZE

    separator = "&" if "?" in base_url else "?"

    return base_url + separator + "offset=" + str(offset)


def extract_total_pages(html):
    """
    Lee el total de paginas del JSON de estado embebido en el HTML.

    Ejemplo:
    "pagination":{"limit":300,"offset":0,"page_size":24,
    "total_pages":5,"route":{...}}

    Hay otro objeto "pagination" mas simple en la misma pagina
    (solo offset/page_size/total_results) - se busca especificamente
    el que trae "limit" y "total_pages" para no confundirlos.
    """

    match = re.search(
        r'"pagination":\{"limit":\d+,"offset":\d+,"page_size":\d+,'
        r'"total_pages":(\d+)',
        html
    )

    if not match:
        return 1

    return int(match.group(1))


# ============================================================
# BLOQUE window["impressions"]: PRODUCTOS RENDERIZADOS EN SERVIDOR
# ============================================================
#
# carrefour.es incrusta en cada pagina de categoria un array
# window["impressions"]=[...] usado internamente para analitica, que
# trae TODOS los productos de la pagina (24) con nombre en formato
# slug, id, ean, precio, marca y categoria - sin necesidad de
# ejecutar JavaScript ni de una peticion aparte a una API.
#
# OJO: item_name viene en formato slug (minusculas, sin acentos,
# separado por guiones), no el nombre legible. Tampoco siempre trae
# item_brand (ver extract_page_products). El nombre legible con
# acentos se saca del atributo alt de las imagenes de producto en el
# mismo HTML, cruzado por item_internal_id (ver build_image_alt_map
# mas abajo) - nunca por posicion, porque el orden de las imagenes en
# el HTML no tiene por que coincidir con el orden del array
# impressions.
# ============================================================

IMPRESSIONS_REGEX = re.compile(
    r'window\["impressions"\]\s*=\s*(\[.*?\]);',
    re.DOTALL
)


def extract_impressions(html):
    """
    Extrae y decodifica el array window["impressions"] de una pagina
    de categoria.

    Devuelve una lista de dicts (posiblemente vacia si el bloque no
    aparece o no se puede decodificar).
    """

    match = IMPRESSIONS_REGEX.search(html)

    if not match:
        return []

    try:
        data = json.loads(match.group(1))
    except ValueError:
        return []

    if not isinstance(data, list):
        return []

    return data


# ============================================================
# NOMBRES LEGIBLES: ALT DE LAS IMAGENES, CRUZADO POR item_internal_id
# ============================================================
#
# Cada tarjeta de producto trae un enlace a la ficha con formato
# /supermercado/<slug>/R-<ID>/p y, dentro, una imagen con alt igual
# al nombre legible del producto.
#
# IMPORTANTE (comprobado con peticiones reales): el <ID> del enlace
# NO es item_id, es item_internal_id. Por ejemplo, para la Leche
# entera Pascual brik 1L: item_id="003629" pero el enlace es
# ".../R-521006986/p", que coincide con item_internal_id="521006986"
# de esa misma entrada de impressions, no con item_id. Para productos
# de marca Carrefour el patron es ".../R-VC4AECOMM-481229/p", que
# coincide con item_internal_id="vc4aecomm-481229" (mayus/minus
# aparte). item_id nunca aparece tal cual en el enlace.
#
# Por eso el cruce real se hace por item_internal_id
# (case-insensitive), no por item_id directamente - item_internal_id
# es el identificador que usa Carrefour en sus propias URLs y mapea
# 1:1 con item_id dentro de la misma entrada de impressions, asi que
# el resultado final sigue siendo "un nombre legible por item_id".
# ============================================================

PRODUCT_LINK_ID_REGEX = re.compile(r"/R-([A-Za-z0-9-]+)/p")


def build_image_alt_map(html):
    """
    Construye un mapa {item_internal_id en minusculas: nombre_legible}
    a partir de las imagenes de producto de la pagina.
    """

    soup = BeautifulSoup(html, "html.parser")

    alt_map = {}

    product_links = soup.find_all("a", href=True)

    for link in product_links:
        href = link["href"]

        id_match = PRODUCT_LINK_ID_REGEX.search(href)

        if not id_match:
            continue

        internal_id = id_match.group(1).lower()

        image = link.find("img")

        if image is None:
            continue

        alt_text = clean_text(image.get("alt"))

        if not alt_text:
            continue

        if internal_id not in alt_map:
            alt_map[internal_id] = alt_text

    return alt_map


# ============================================================
# CONVERSION DE PRODUCTOS
# ============================================================

def build_product_url(internal_id, item_name):
    """
    Construye la URL de ficha de producto.

    Formato comprobado con peticiones reales:
    /supermercado/<slug>/R-<item_internal_id>/p
    (usa item_internal_id, no item_id - ver nota en
    build_image_alt_map).
    """

    if not internal_id or not item_name:
        return None

    return (
        BASE_URL
        + "/supermercado/"
        + item_name
        + "/R-"
        + internal_id
        + "/p"
    )


def convert_impression_to_product(entry, alt_map):
    """
    Convierte una entrada de window["impressions"] al formato comun
    usado por save_raw_product.

    Usa .get() para item_brand porque no siempre viene (comprobado
    con productos a granel sin marca, ej. platano de freir a granel).
    """

    if not isinstance(entry, dict):
        return None

    item_id = clean_text(entry.get("item_id"))
    slug_name = clean_text(entry.get("item_name"))

    if not item_id or not slug_name:
        return None

    price = entry.get("price")

    try:
        price = round(float(price), 2) if price is not None else None
    except (TypeError, ValueError):
        price = None

    if price is None:
        return None

    internal_id = entry.get("item_internal_id")
    internal_id = internal_id.lower() if internal_id else None

    readable_name = alt_map.get(internal_id) if internal_id else None

    if readable_name:
        name = readable_name
        name_source = "alt_text"
    else:
        name = slug_name
        name_source = "slug_fallback"

    product = {
        "external_id": item_id,
        "name": name,
        "normalized_name": normalize_text(name),
        "price": price,
        "original_price": None,
        "price_per_unit": None,
        "unit_price_text": None,
        "product_url": build_product_url(
            entry.get("item_internal_id"),
            slug_name
        ),
        "image_url": None,
        "category_name": CATEGORY_NAME,
        "available": True,
        "extraction_method": "carrefour_html",
        "item_ean": entry.get("item_ean"),
        "item_brand": entry.get("item_brand"),
        "item_category": entry.get("item_category"),
        "item_provider": entry.get("item_provider"),
        "item_provider_type": entry.get("item_provider_type"),
        "item_internal_id": entry.get("item_internal_id"),
        "name_source": name_source,
        "slug_name": slug_name
    }

    return product


def validate_product(product):
    """
    Validación mínima antes de guardar en MySQL.
    """

    if not product.get("external_id"):
        return False

    if not product.get("name"):
        return False

    price = product.get("price")

    if price is None:
        return False

    if price <= 0:
        return False

    if price > 1000:
        return False

    return True


def extract_page_products(html):
    """
    Extrae y valida los productos de una pagina de categoria a
    partir de window["impressions"], con el nombre legible cruzado
    por item_internal_id via el alt de las imagenes.
    """

    impressions = extract_impressions(html)

    print("Entradas en impressions:", len(impressions))

    alt_map = build_image_alt_map(html)

    print("Nombres legibles encontrados (alt):", len(alt_map))

    products = []
    seen_external_ids = set()
    corrected_count = 0

    for entry in impressions:
        product = convert_impression_to_product(entry, alt_map)

        if product is None:
            continue

        if product["external_id"] in seen_external_ids:
            continue

        if not validate_product(product):
            continue

        if product["name_source"] == "alt_text":
            corrected_count += 1

        seen_external_ids.add(product["external_id"])
        products.append(product)

    print(
        "Nombres via alt_text:",
        corrected_count,
        "| via slug_fallback:",
        len(products) - corrected_count,
        "de",
        len(products)
    )

    products.sort(key=lambda product: product["name"].lower())

    return products


# ============================================================
# GUARDADO EN MYSQL
# ============================================================

def save_raw_product(product):
    """
    Guarda o actualiza un producto del catálogo de Carrefour
    en raw_scraped_products.
    """

    raw_payload = dict(product)

    raw_product_id = upsert_raw_scraped_product(
        supermarket_slug=SUPERMARKET_SLUG,
        scraper_name=SCRAPER_NAME,
        external_product_id=product.get("external_id"),
        external_name=product.get("name"),
        normalized_name=product.get("normalized_name"),
        current_price=product.get("price"),
        original_price=product.get("original_price"),
        price_per_unit=product.get("price_per_unit"),
        unit_price_text=product.get("unit_price_text"),
        product_url=product.get("product_url"),
        image_url=product.get("image_url"),
        available=product.get("available", True),
        raw_data=raw_payload
    )

    return raw_product_id


# ============================================================
# EJECUCIÓN PRINCIPAL
# ============================================================

def get_page_products(base_url, page_number):
    """
    Obtiene los productos de una pagina de categoria.

    Devuelve (products, total_pages_hint):
        products: None si la peticion fallo a nivel de red tras
            agotar reintentos - fallo transitorio, hay que saltar la
            pagina pero no dar la categoria por acabada. Lista
            (posiblemente vacia) en cualquier otro caso.
        total_pages_hint: total_pages leido del HTML si es la pagina
            1, None en cualquier otro caso.

    Caso especial (comprobado contra carrefour.es real): pedir un
    offset mayor que el que cubre total_pages no da error ni JSON
    vacio - carrefour.es responde 301 y redirige a /supermercado (la
    home generica de categorias), que no tiene impressions de la
    categoria pedida. Por eso el bucle de scrape_category para en
    cuanto se alcanza total_pages_reported, igual que hace DIA con su
    propio total_pages.
    """

    page_url = build_page_url(base_url, page_number)

    print("URL:", page_url)

    html = fetch_page_with_retries(page_url)

    if html is None:
        return None, None

    total_pages_hint = None

    if page_number == 1:
        total_pages_hint = extract_total_pages(html)

        print("Total de páginas según carrefour.es:", total_pages_hint)

    products = extract_page_products(html)

    return products, total_pages_hint


def scrape_category(category_name, base_url, totals):
    """
    Recorre las paginas de una categoria (hasta MAX_PAGES_PER_CATEGORY)
    y guarda los productos encontrados.

    Acumula los contadores en `totals` (compartido entre categorias
    para el scraping_log unico de la ejecucion) y devuelve un resumen
    de la categoria para el informe final.
    """

    global CATEGORY_NAME
    CATEGORY_NAME = category_name

    print()
    print("=" * 80)
    print("CATEGORÍA:", category_name)
    print("URL:", base_url)
    print("=" * 80)

    total_pages_reported = None
    pages_scraped = 0

    for page_number in range(1, MAX_PAGES_PER_CATEGORY + 1):
        print()
        print("-- Página", page_number, "--")

        products, total_pages_hint = get_page_products(
            base_url,
            page_number
        )

        if total_pages_hint is not None:
            total_pages_reported = total_pages_hint

        if products is None:
            totals["errors_count"] += 1

            print(
                "[SALTANDO PÁGINA]",
                page_number,
                "- fallo de red tras agotar reintentos"
            )

            continue

        if not products:
            print(
                "[SIN PRODUCTOS] página",
                page_number,
                "- se asume fin de la categoría"
            )

            break

        print("Productos válidos en esta página:", len(products))

        totals["products_found"] += len(products)
        pages_scraped += 1

        for product in products:
            try:
                save_raw_product(product)

                totals["products_saved"] += 1

                print(
                    "[GUARDADO]",
                    product["external_id"],
                    "|",
                    product["name"],
                    "|",
                    str(product["price"]) + "€",
                    "|",
                    product.get("item_brand"),
                    "|",
                    product.get("name_source")
                )

            except Exception as product_error:
                totals["errors_count"] += 1

                print(
                    "[ERROR PRODUCTO]",
                    product.get("external_id"),
                    "|",
                    product.get("name"),
                    "|",
                    str(product_error)
                )

        if (
            total_pages_reported is not None
            and page_number >= total_pages_reported
        ):
            break

        if page_number < MAX_PAGES_PER_CATEGORY:
            delay = random.uniform(*REQUEST_DELAY_SECONDS)

            print("Pausa de", round(delay, 1), "segundos...")

            time.sleep(delay)

    return {
        "category_name": category_name,
        "total_pages_reported": total_pages_reported,
        "pages_scraped": pages_scraped
    }


def main():
    log_id = start_scraping_log(
        supermarket_slug=SUPERMARKET_SLUG,
        scraper_name=SCRAPER_NAME
    )

    totals = {
        "products_found": 0,
        "products_saved": 0,
        "errors_count": 0
    }

    category_summaries = []

    try:
        print("=" * 80)
        print("SCRAPER CATÁLOGO CARREFOUR")
        print("Categorías a recorrer:", len(CATEGORIES))
        print("Máx. páginas por categoría:", MAX_PAGES_PER_CATEGORY)
        print("=" * 80)

        for index, (category_name, base_url) in enumerate(CATEGORIES):
            try:
                summary = scrape_category(
                    category_name,
                    base_url,
                    totals
                )

                category_summaries.append(summary)

            except Exception as category_error:
                totals["errors_count"] += 1

                print(
                    "[ERROR CATEGORÍA]",
                    category_name,
                    "|",
                    str(category_error)
                )

                continue

            if index < len(CATEGORIES) - 1:
                delay = random.uniform(*REQUEST_DELAY_SECONDS)

                print()
                print(
                    "Pausa entre categorías:",
                    round(delay, 1),
                    "segundos..."
                )

                time.sleep(delay)

        status = (
            "success"
            if totals["errors_count"] == 0
            else "partial_success"
        )

        finish_scraping_log(
            log_id=log_id,
            status=status,
            products_found=totals["products_found"],
            products_updated=totals["products_saved"],
            errors_count=totals["errors_count"]
        )

        print()
        print("=" * 80)
        print("SCRAPING FINALIZADO")
        print("-" * 80)

        for summary in category_summaries:
            total_pages_text = (
                summary["total_pages_reported"]
                if summary["total_pages_reported"] is not None
                else "desconocido"
            )

            print(
                " -",
                summary["category_name"],
                "| páginas recorridas:",
                summary["pages_scraped"],
                "de",
                total_pages_text
            )

        print("-" * 80)
        print("Productos encontrados:", totals["products_found"])
        print("Productos guardados:", totals["products_saved"])
        print("Errores:", totals["errors_count"])
        print("Estado:", status)
        print("=" * 80)

    except Exception as general_error:
        finish_scraping_log(
            log_id=log_id,
            status="error",
            products_found=totals["products_found"],
            products_updated=totals["products_saved"],
            errors_count=totals["errors_count"] + 1,
            error_message=str(general_error)
        )

        print()
        print("=" * 80)
        print("ERROR GENERAL DEL SCRAPER")
        print(str(general_error))
        print("=" * 80)

        raise


if __name__ == "__main__":
    main()
