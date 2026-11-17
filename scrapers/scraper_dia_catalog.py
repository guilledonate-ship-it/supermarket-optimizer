# ============================================================
# NOTA IMPORTANTE: COBERTURA PARCIAL DEL CATALOGO
# ============================================================
#
# dia.es renderiza cada pagina de categoria con un JSON de estado
# embebido que declara "page_size":20, pero en el HTML estatico
# (sin ejecutar JS) solo llegan con nombre y precio los primeros
# ~10 productos. Los otros ~10 aparecen unicamente como
# comentarios placeholder (item.sku_id::X item.data::... sin "€")
# marcados por un campo "initialLoadedItems::10": se cargan via
# scroll/JS del lado cliente y una peticion HTTP simple no los ve.
#
# Por tanto este scraper obtiene una cobertura PARCIAL de cada
# pagina de categoria (aprox. la mitad de los productos listados
# por dia.es), no el catalogo completo. Ver tambien total_pages,
# que si se respeta correctamente via paginacion pag-N/.
# ============================================================

import hashlib
import json
import os
import random
import re
import sys
import time
from urllib.parse import urljoin

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

BASE_URL = "https://www.dia.es"

# Pares (nombre, url_base) de categorias a recorrer. La URL es la de
# la pagina 1; las siguientes paginas se construyen con build_page_url.
CATEGORIES = [
    (
        "Leche",
        "https://www.dia.es/huevos-leche-y-mantequilla/leche/c/L2051"
    ),
    (
        "Agua",
        "https://www.dia.es/agua-y-refrescos/agua/c/L2107"
    ),
    (
        "Pan de molde y especiales",
        "https://www.dia.es/panaderia/pan-de-molde-y-especiales/c/L2069"
    ),
    (
        "Platanos y bananas",
        "https://www.dia.es/frutas/platanos-y-bananas/c/L2033"
    ),
    (
        "Yogures naturales y desnatados",
        "https://www.dia.es/yogures-y-postres/"
        "yogures-naturales-y-desnatados/c/L2079"
    ),
    (
        # DIA no separa por tipo de aceite (mezcla oliva y girasol) -
        # equivalente a las 3 subcategorias de aceite de oliva que
        # se combinan en el scraper de Eroski.
        "Aceites",
        "https://www.dia.es/aceites-salsas-y-especias/aceites/c/L2046"
    ),
    (
        # DIA mezcla pasta corta y larga en una sola categoria -
        # equivalente a combinar las 2 subcategorias de pasta de
        # Eroski.
        "Macarrones, espaguetis y pastas secas",
        "https://www.dia.es/arroz-pastas-y-legumbres/"
        "macarrones-espaguetis-y-pastas-secas/c/L2044"
    ),
    (
        "Arroz",
        "https://www.dia.es/arroz-pastas-y-legumbres/arroz/c/L2042"
    ),
    (
        "Cafe molido",
        "https://www.dia.es/cafe-cacao-e-infusiones/cafe-molido/c/L2277"
    ),
    (
        "Huevos",
        "https://www.dia.es/huevos-leche-y-mantequilla/huevos/c/L2055"
    ),
    (
        # DIA mezcla atun y bonito, en aceite/tomate/natural/
        # escabeche - equivalente a combinar las 5 subcategorias de
        # atun/bonito de Eroski.
        "Atun y bonito",
        "https://www.dia.es/conservas-caldos-y-cremas/"
        "atun-y-bonito/c/L2179"
    ),
    (
        "Papel higienico, cocina y servilletas",
        "https://www.dia.es/limpieza-y-hogar/"
        "papel-higienico-cocina-y-servilletas/c/L2168"
    ),
    (
        # Legumbres: DIA mezcla secas y cocidas/en conserva en la
        # misma categoria (comprobado con peticion real: "garbanzos
        # cocidos", "alubias cocidas" aparecen junto a las secas) -
        # equivalente a combinar "legumbres" + "legumbres-cocidas" de
        # Eroski, no solo la primera.
        "Garbanzos y alubias",
        "https://www.dia.es/arroz-pastas-y-legumbres/"
        "garbanzos-y-alubias/c/L2191"
    ),
    (
        "Lentejas",
        "https://www.dia.es/arroz-pastas-y-legumbres/lentejas/c/L2193"
    ),
    (
        # DIA combina harina y levaduras en una sola categoria -
        # equivalente a combinar "harina" + "levaduras-y-otros" de
        # Eroski (esta ultima esta archivada bajo azucar/reposteria
        # en Eroski, no junto a la harina).
        "Harinas y levaduras",
        "https://www.dia.es/bolleria-reposteria-y-azucar/"
        "harinas-y-levaduras/c/L2075"
    ),
    (
        "Azucar, miel y edulcorantes",
        "https://www.dia.es/bolleria-reposteria-y-azucar/"
        "azucar-miel-y-edulcorantes/c/L2060"
    ),
    (
        # Galletas: DIA ya combina tipos por pares/tercios en sus 3
        # categorias (clasicas+digestive, chocolate+rellenas,
        # saladas+crackers) - equivalente a combinar las 5
        # subcategorias no-infantiles/no-sin-gluten de Eroski.
        "Galletas clasicas y digestive",
        "https://www.dia.es/galletas-cereales-y-mermeladas/"
        "galletas-clasicas-y-digestive/c/L2065"
    ),
    (
        "Galletas de chocolate y rellenas",
        "https://www.dia.es/galletas-cereales-y-mermeladas/"
        "galletas-de-chocolate-y-rellenas/c/L2320"
    ),
    (
        "Galletas saladas y crackers",
        "https://www.dia.es/galletas-cereales-y-mermeladas/"
        "galletas-saladas-y-crackers/c/L2066"
    ),
    (
        "Cereales",
        "https://www.dia.es/galletas-cereales-y-mermeladas/cereales/c/L2068"
    ),
    (
        "Cereales integrales y muesli",
        "https://www.dia.es/galletas-cereales-y-mermeladas/"
        "cereales-integrales-y-muesli/c/L2321"
    ),
    (
        # Queso: mismo criterio que galletas - las 9 categorias
        # reales de DIA se combinan con las 9 equivalentes de Eroski,
        # no solo curado+lonchas.
        "Queso fresco",
        "https://www.dia.es/quesos/fresco/c/L2008"
    ),
    (
        "Queso curado",
        "https://www.dia.es/quesos/curado/c/L2007"
    ),
    (
        "Queso semicurado",
        "https://www.dia.es/quesos/semicurado/c/L2345"
    ),
    (
        "Queso tierno",
        "https://www.dia.es/quesos/tierno/c/L2346"
    ),
    (
        "Queso en lonchas",
        "https://www.dia.es/quesos/en-lonchas/c/L2205"
    ),
    (
        "Queso azul y de cabra",
        "https://www.dia.es/quesos/azul-y-de-cabra/c/L2009"
    ),
    (
        "Queso rallado",
        "https://www.dia.es/quesos/rallado/c/L2347"
    ),
    (
        "Queso untable y en porciones",
        "https://www.dia.es/quesos/untable-y-en-porciones/c/L2010"
    ),
    (
        "Quesos internacionales",
        "https://www.dia.es/quesos/especialidades/c/L2011"
    ),
    (
        "Jamon cocido",
        "https://www.dia.es/charcuteria/jamon-cocido/c/L2001"
    ),
    (
        "Verduras y patatas congeladas",
        "https://www.dia.es/congelados-y-helados/"
        "verduras-y-patatas/c/L2210"
    ),
    (
        # DIA no separa lavado a mano de maquina en esta categoria
        # (comprobado con peticion real: todo el muestreo es lavado a
        # maquina, incluida "prendas delicadas") - equivalente a
        # combinar los 4 formatos de maquina + prendas delicadas de
        # Eroski.
        "Detergentes",
        "https://www.dia.es/limpieza-y-hogar/detergentes/c/L2170"
    ),
    (
        "Mantequilla y margarina",
        "https://www.dia.es/huevos-leche-y-mantequilla/"
        "mantequilla-y-margarina/c/L2056"
    ),
]

# Limite de paginas por categoria, para poder probar el scraper sin
# recorrer el catalogo entero.
MAX_PAGES_PER_CATEGORY = 15

# Espera entre reintentos de una misma peticion (segundos).
RETRY_DELAYS_SECONDS = [5, 15, 45]

SUPERMARKET_SLUG = "dia"
SCRAPER_NAME = "scraper_dia_catalog_v2"

# Se asigna dinamicamente antes de procesar cada categoria (ver
# scrape_category). Las funciones de extraccion leen este global,
# igual que hacian cuando solo existia la categoria de leche.
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
    el SKU real del producto.

    El mismo nombre normalizado producirá siempre el mismo ID.
    """

    normalized_name = normalize_text(product_name)

    name_hash = hashlib.sha256(
        normalized_name.encode("utf-8")
    ).hexdigest()[:24]

    return "dia_hash_" + name_hash


# ============================================================
# UTILIDADES DE PRECIOS
# ============================================================

def parse_price(text):
    """
    Convierte un precio escrito como 1,29 € en float 1.29.
    """

    if not text:
        return None

    cleaned = clean_text(text)

    match = re.search(
        r"(\d+(?:[.,]\d{1,2})?)\s*€",
        cleaned
    )

    if not match:
        return None

    price_text = match.group(1).replace(",", ".")

    try:
        return round(float(price_text), 2)
    except ValueError:
        return None


def extract_all_prices(text):
    """
    Extrae todos los precios con símbolo € de un texto.

    Ignora, cuando es posible, los precios por kilo/litro para no
    confundirlos con el precio final del producto.
    """

    if not text:
        return []

    cleaned = clean_text(text)

    parts = re.findall(
        r"\d+(?:[.,]\d{1,2})?\s*€(?:\s*/\s*[A-Za-zÁÉÍÓÚáéíóú]+)?",
        cleaned
    )

    prices = []

    for part in parts:
        part_lower = part.lower()

        if "/" in part_lower:
            continue

        price = parse_price(part)

        if price is not None:
            prices.append(price)

    return prices


def extract_unit_price_text(text):
    """
    Extrae un texto como:
    0,96 €/LITRO
    2,50 €/KILO
    """

    if not text:
        return None

    cleaned = clean_text(text)

    match = re.search(
        r"\(?\d+(?:[.,]\d{1,2})?\s*€\s*/\s*"
        r"(?:LITRO|KILO|KG|UNIDAD|UD|100\s*G|100\s*ML)\)?",
        cleaned,
        re.IGNORECASE
    )

    if not match:
        return None

    return clean_text(match.group(0))


def parse_price_per_unit(unit_price_text):
    """
    Convierte el importe del precio unitario en float.

    Ejemplo:
    0,96 €/LITRO -> 0.96
    """

    if not unit_price_text:
        return None

    return parse_price(unit_price_text)


def resolve_current_and_original_price(prices):
    """
    Determina precio actual y precio anterior.

    Si solo hay un precio:
        precio actual = único precio

    Si hay dos o más:
        normalmente el último precio visible es el actual/oferta
        y el primero es el precio original.
    """

    if not prices:
        return None, None

    current_price = prices[-1]
    original_price = None

    if len(prices) > 1:
        candidate_original = prices[0]

        if candidate_original > current_price:
            original_price = candidate_original

    return current_price, original_price


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
    Descarga una pagina con reintentos ante fallos de red o
    respuestas 429/503.

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
    Construye la URL de la pagina N de una categoria de dia.es.

    dia.es pagina insertando un segmento pag-N/ justo antes de
    /c/L<codigo> (comprobado en leche y agua: pagina 1 sin sufijo,
    pagina 2 en .../pag-2/c/L2051, pagina 3 en .../pag-3/c/L2051).
    """

    if page_number <= 1:
        return base_url

    match = re.search(r"/c/(L\d+)", base_url)

    if not match:
        return base_url

    prefix = base_url[:match.start()]
    category_code = match.group(1)

    return prefix + "/pag-" + str(page_number) + "/c/" + category_code


def extract_total_pages(html):
    """
    Lee el total de paginas del JSON de estado embebido en el HTML.

    Ejemplo: "pagination":{"page_number":1,"page_size":20,"total_pages":3}
    """

    match = re.search(
        r'"pagination":\{"page_number":\d+,"page_size":\d+,'
        r'"total_pages":(\d+)\}',
        html
    )

    if not match:
        return 1

    return int(match.group(1))


# ============================================================
# ENDPOINT JSON: url_manager_initial_analytics
# ============================================================
#
# dia.es expone en cada pagina de categoria un endpoint JSON usado
# internamente para analitica, que trae en page_product_analytics
# TODOS los productos de la pagina (20, no los ~10 que renderiza el
# HTML estatico - ver nota de cobertura parcial al inicio del
# archivo). Tambien trae item_brand_type ("d" = marca propia Dia,
# "n" = marca nacional/tercero), lo que ya viene resuelto por la web.
#
# OJO: item_name viene en minusculas, sin acentos, y con los puntos
# decimales de los tamaños eliminados (ej. "2.2 l" -> "22 l"). Se usa
# tal cual porque asi lo pide esta fase del trabajo, pero es un dato
# a tener en cuenta antes de fiarse de ese campo para tamaños.
#
# El endpoint no devuelve total_pages: una pagina fuera de rango
# responde 200 con "page_product_analytics": null, no un error.
# ============================================================

JSON_API_BASE_URL = (
    "https://www.dia.es/api/v1/plp-insight/"
    "url_manager_initial_analytics/"
)


def build_json_api_url(base_url, page_number):
    """
    Construye la URL del endpoint JSON para la pagina N de una
    categoria, reutilizando el mismo esquema pag-N/ de build_page_url.
    """

    page_url = build_page_url(base_url, page_number)
    category_path = page_url[len(BASE_URL):].lstrip("/")

    return JSON_API_BASE_URL + category_path


def build_product_url_from_category(base_url, external_id):
    """
    Construye la URL de ficha de producto a partir de la URL de la
    categoria (dia.es no resuelve /p/SKU sin el prefijo de categoria
    delante, comprobado con una peticion real).
    """

    if not external_id:
        return None

    match = re.search(r"/c/L\d+", base_url)

    if not match:
        return None

    prefix = base_url[:match.start()]

    return prefix + "/p/" + external_id


def fetch_json(url):
    """
    Descarga y decodifica el endpoint JSON de analitica de categoria.
    """

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.7"
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    print("HTTP status (JSON):", response.status_code)

    return response.json()


def fetch_json_with_retries(url):
    """
    Descarga el JSON de analitica con el mismo esquema de reintentos
    y backoff que fetch_page_with_retries (5s, 15s, 45s).
    """

    last_error = None
    max_attempts = len(RETRY_DELAYS_SECONDS) + 1

    for attempt in range(1, max_attempts + 1):
        try:
            return fetch_json(url)

        except Exception as error:
            last_error = error

            print(
                "[FALLO PETICION JSON]",
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
        "[ERROR JSON]",
        url,
        "- se agotaron los reintentos:",
        str(last_error)
    )

    return None


def fetch_category_json(base_url, page_number):
    """
    Descarga y normaliza los productos de page_product_analytics para
    una pagina de categoria.

    Devuelve (products, network_failed):
        products: lista de dicts con el mismo formato que usan las
            estrategias de parseo HTML, mas item_brand,
            item_brand_type, discount y stock_availability para
            guardarlos en raw_data. Vacia si la peticion falla tras
            agotar reintentos, si la respuesta no trae
            page_product_analytics, o si esta viene vacia/null.
        network_failed: True solo si no se pudo obtener respuesta del
            endpoint (fetch_json_with_retries agoto los reintentos).
            False si el endpoint respondio con normalidad, aunque sea
            confirmando 0 productos - eso es una respuesta valida, no
            un fallo. El llamante usa esta distincion para decidir si
            merece la pena caer al parseo de HTML.
    """

    url = build_json_api_url(base_url, page_number)

    print("URL (JSON):", url)

    data = fetch_json_with_retries(url)

    if data is None:
        return [], True

    page_product_analytics = data.get("page_product_analytics")

    if not page_product_analytics:
        print(
            "[JSON SIN PRODUCTOS]",
            url,
            "- page_product_analytics vacio o ausente"
        )

        return [], False

    products = []

    for item_key, entry in page_product_analytics.items():
        if not isinstance(entry, dict):
            continue

        external_id = (
            clean_text(entry.get("item_id"))
            or clean_text(item_key)
        )

        if not external_id:
            continue

        name = clean_text(entry.get("item_name"))

        if not name:
            continue

        price = entry.get("price")

        try:
            price = round(float(price), 2) if price is not None else None
        except (TypeError, ValueError):
            price = None

        if price is None:
            continue

        product = {
            "external_id": external_id,
            "name": name,
            "normalized_name": normalize_text(name),
            "price": price,
            "original_price": None,
            "price_per_unit": None,
            "unit_price_text": None,
            "product_url": build_product_url_from_category(
                base_url,
                external_id
            ),
            "image_url": None,
            "category_name": CATEGORY_NAME,
            "available": bool(entry.get("stock_availability", True)),
            "extraction_method": "json_api",
            "item_brand": entry.get("item_brand"),
            "item_brand_type": entry.get("item_brand_type"),
            "discount": entry.get("discount"),
            "stock_availability": entry.get("stock_availability"),
            # Por defecto el nombre es el de item_name (sin acentos,
            # sin separador decimal). get_page_products lo sustituye
            # por el del bloque ItemList cuando encuentra el SKU, y
            # entonces sube esto a "ld_json".
            "name_source": "json_fallback"
        }

        products.append(product)

    products.sort(key=lambda product: product["name"].lower())

    print("Productos en page_product_analytics:", len(products))

    return products, False


# ============================================================
# BLOQUE ItemList (application/ld+json) PARA NOMBRES CORRECTOS
# ============================================================
#
# item_name del endpoint JSON viene en minusculas, sin acentos y SIN
# el separador decimal de los tamaños (ej. "2.2 L" -> "22 l",
# "1,5 L" -> "15 l"). Comprobado contra datos reales: 37 de 152
# productos de prueba tenian este problema, lo que daria un precio
# por litro/kilo incorrecto si se usara ese tamaño tal cual.
#
# La misma pagina HTML de categoria incluye un bloque
# <script type="application/ld+json"> con un ItemList de schema.org
# que SI trae el nombre completo (mayusculas, acentos y separador
# decimal intactos) y la imagen. A diferencia del resto del HTML
# (limitado por initialLoadedItems::10), este bloque trae TODOS los
# productos de la pagina: comprobado en Leche, Agua y Yogures que
# coincide exactamente con el recuento de page_product_analytics.
# ============================================================

ITEM_LIST_REGEX = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>\s*'
    r'(\{.*?"@type":\s*"ItemList".*?\})\s*</script>',
    re.DOTALL
)


def extract_item_list_products(html):
    """
    Extrae el bloque ItemList (application/ld+json) de una pagina de
    categoria.

    Devuelve un dict {external_id: {"name": ..., "image_url": ...}}
    con TODOS los productos de la pagina, no solo los primeros ~10.
    Devuelve un dict vacio si el bloque no aparece o no se puede
    decodificar.
    """

    match = ITEM_LIST_REGEX.search(html)

    if not match:
        return {}

    try:
        data = json.loads(match.group(1))
    except ValueError:
        return {}

    items = data.get("itemListElement") or []

    result = {}

    for list_item in items:
        if not isinstance(list_item, dict):
            continue

        item_url = list_item.get("url")
        product = list_item.get("item")

        if not item_url or not isinstance(product, dict):
            continue

        sku_match = re.search(r"/p/([^/?#]+)", item_url)

        if not sku_match:
            continue

        external_id = clean_text(sku_match.group(1))

        if not external_id:
            continue

        name = clean_text(product.get("name"))
        image_url = product.get("image")

        result[external_id] = {
            "name": name,
            "image_url": clean_text(image_url) if image_url else None
        }

    return result


# ============================================================
# FILTROS DE PRODUCTOS
# ============================================================

def is_invalid_product_name(name):
    """
    Descarta categorías, botones y textos promocionales.
    """

    if not name:
        return True

    cleaned = clean_text(name)
    normalized = normalize_text(cleaned)

    if len(cleaned) < 5:
        return True

    exact_invalid_names = {
        "leche",
        "huevos leche y mantequilla",
        "huevos, leche y mantequilla",
        "ordenar",
        "destacados",
        "precio",
        "marca dia",
        "filtros",
        "anadir",
        "añadir",
        "ver todos",
        "inicio"
    }

    if normalized in exact_invalid_names:
        return True

    invalid_fragments = [
        "condition true",
        "initialloadeditems",
        "item isvisible",
        "item type",
        "item sku id",
        "item data",
        "ordenar por",
        "precio kilo",
        "supermercado online",
        "recibe tu compra",
        "club dia",
        "cookie",
        "politica de privacidad"
    ]

    for fragment in invalid_fragments:
        if fragment in normalized:
            return True

    if "€" in cleaned:
        return True

    return False


def looks_like_product_name(name):
    """
    Señales habituales de que un texto representa un producto.
    """

    if is_invalid_product_name(name):
        return False

    normalized = normalize_text(name)

    product_indicators = [
        " leche ",
        " leche",
        "1 l",
        "1.5 l",
        "2 l",
        "200 ml",
        "500 ml",
        "pack",
        "botella",
        "lactea",
        "pascual",
        "asturiana",
        "lauki",
        "celta",
        "dia"
    ]

    padded_name = " " + normalized + " "

    return any(
        indicator in padded_name
        for indicator in product_indicators
    )


# ============================================================
# ESTRATEGIA 1: EXTRAER PRODUCTOS MEDIANTE ENLACES /p/SKU
# ============================================================

def find_product_container(link):
    """
    Sube por el árbol HTML hasta encontrar un contenedor que incluya
    nombre, precio y botón Añadir.

    No depende de una clase CSS exacta.
    """

    current = link

    for _ in range(8):
        if current is None:
            break

        text = clean_text(
            current.get_text(" ", strip=True)
        )

        if (
            text
            and "€" in text
            and len(text) < 2500
        ):
            return current

        current = current.parent

    return link.parent


def extract_name_from_link(link, container):
    """
    Intenta obtener el nombre desde:
    - texto del enlace
    - title
    - aria-label
    - imagen alt
    - textos del contenedor
    """

    candidates = []

    link_text = clean_text(
        link.get_text(" ", strip=True)
    )

    if link_text:
        candidates.append(link_text)

    title = clean_text(link.get("title"))

    if title:
        candidates.append(title)

    aria_label = clean_text(link.get("aria-label"))

    if aria_label:
        candidates.append(aria_label)

    image = link.find("img")

    if image:
        image_alt = clean_text(image.get("alt"))

        if image_alt:
            candidates.append(image_alt)

    if container:
        for element in container.find_all(
            ["h1", "h2", "h3", "h4", "p", "span", "a"],
            limit=40
        ):
            candidate = clean_text(
                element.get_text(" ", strip=True)
            )

            if candidate:
                candidates.append(candidate)

    # Preferimos textos que parecen nombres reales
    for candidate in candidates:
        if looks_like_product_name(candidate):
            return candidate

    # Fallback: primer candidato no inválido
    for candidate in candidates:
        if not is_invalid_product_name(candidate):
            return candidate

    return None


def extract_products_from_links(soup):
    """
    Extrae productos buscando enlaces de ficha con formato /p/SKU.
    """

    products = []
    seen_external_ids = set()

    product_links = soup.find_all(
        "a",
        href=re.compile(r"/p/[^/?#]+")
    )

    print("Enlaces de producto encontrados:", len(product_links))

    for link in product_links:
        href = link.get("href")

        if not href:
            continue

        sku_match = re.search(
            r"/p/([^/?#]+)",
            href
        )

        if not sku_match:
            continue

        external_id = clean_text(sku_match.group(1))

        if external_id in seen_external_ids:
            continue

        container = find_product_container(link)

        if container is None:
            continue

        container_text = clean_text(
            container.get_text(" ", strip=True)
        )

        product_name = extract_name_from_link(
            link,
            container
        )

        if not product_name:
            continue

        if is_invalid_product_name(product_name):
            continue

        prices = extract_all_prices(container_text)

        current_price, original_price = (
            resolve_current_and_original_price(prices)
        )

        if current_price is None:
            continue

        unit_price_text = extract_unit_price_text(
            container_text
        )

        product_url = urljoin(BASE_URL, href)

        image_url = None
        image = container.find("img")

        if image:
            image_url = (
                image.get("src")
                or image.get("data-src")
                or image.get("data-lazy-src")
            )

            if image_url:
                image_url = urljoin(
                    BASE_URL,
                    image_url
                )

        product = {
            "external_id": external_id,
            "name": product_name,
            "normalized_name": normalize_text(product_name),
            "price": current_price,
            "original_price": original_price,
            "price_per_unit": parse_price_per_unit(
                unit_price_text
            ),
            "unit_price_text": unit_price_text,
            "product_url": product_url,
            "image_url": image_url,
            "category_name": CATEGORY_NAME,
            "available": True,
            "extraction_method": "product_link"
        }

        seen_external_ids.add(external_id)
        products.append(product)

    return products


# ============================================================
# ESTRATEGIA 2: EXTRAER DATOS EMBEBIDOS item.sku_id / item.data
# ============================================================

def get_source_lines(html):
    """
    Genera líneas limpias tanto desde HTML como desde texto visible.
    """

    soup = BeautifulSoup(html, "html.parser")

    visible_text = soup.get_text(
        "\n",
        strip=True
    )

    combined_text = html + "\n" + visible_text

    return [
        clean_text(line)
        for line in combined_text.splitlines()
        if clean_text(line)
    ]


def extract_sku_from_line(line):
    """
    Busca estructuras como:
    item.sku_id::608
    """

    match = re.search(
        r"sku[_\s.-]*id\s*[:=]+\s*([A-Za-z0-9_-]+)",
        line,
        re.IGNORECASE
    )

    if not match:
        return None

    return clean_text(match.group(1))


def clean_embedded_product_name(text):
    """
    Limpia prefijos de campos embebidos como item.data::.
    """

    if not text:
        return None

    cleaned = clean_text(text)

    cleaned = re.sub(
        r"^.*?item[._\s-]*data\s*[:=]+\s*",
        "",
        cleaned,
        flags=re.IGNORECASE
    )

    cleaned = re.sub(
        r"^mejor valorado\s+",
        "",
        cleaned,
        flags=re.IGNORECASE
    )

    cleaned = re.sub(
        r"^precio destacado\s+",
        "",
        cleaned,
        flags=re.IGNORECASE
    )

    return clean_text(cleaned)


def extract_products_from_embedded_text(html):
    """
    Estrategia fallback para páginas que contienen referencias
    item.sku_id e item.data en el HTML.
    """

    lines = get_source_lines(html)

    products = []
    seen_external_ids = set()

    for index, line in enumerate(lines):
        external_id = extract_sku_from_line(line)

        if not external_id:
            continue

        if external_id in seen_external_ids:
            continue

        nearby_lines = lines[index:index + 12]

        product_name = None
        block_text = " ".join(nearby_lines)

        for nearby_line in nearby_lines:
            if "item.data" in nearby_line.lower():
                candidate = clean_embedded_product_name(
                    nearby_line
                )

                if (
                    candidate
                    and not is_invalid_product_name(candidate)
                ):
                    product_name = candidate
                    break

        if not product_name:
            for nearby_line in nearby_lines:
                candidate = clean_embedded_product_name(
                    nearby_line
                )

                if looks_like_product_name(candidate):
                    product_name = candidate
                    break

        if not product_name:
            continue

        prices = extract_all_prices(block_text)

        current_price, original_price = (
            resolve_current_and_original_price(prices)
        )

        if current_price is None:
            continue

        unit_price_text = extract_unit_price_text(
            block_text
        )

        product_url = None

        url_match = re.search(
            r'''href=["']([^"']*/p/''' + re.escape(external_id) + r'''[^"']*)["']''',html,
                re.IGNORECASE
        )

        if url_match:
            product_url = urljoin(
                BASE_URL,
                url_match.group(1)
            )

        if url_match:
            product_url = urljoin(
                BASE_URL,
                url_match.group(1)
            )

        product = {
            "external_id": external_id,
            "name": product_name,
            "normalized_name": normalize_text(product_name),
            "price": current_price,
            "original_price": original_price,
            "price_per_unit": parse_price_per_unit(
                unit_price_text
            ),
            "unit_price_text": unit_price_text,
            "product_url": product_url,
            "image_url": None,
            "category_name": CATEGORY_NAME,
            "available": True,
            "extraction_method": "embedded_text"
        }

        seen_external_ids.add(external_id)
        products.append(product)

    return products


# ============================================================
# FUSIONAR Y VALIDAR RESULTADOS
# ============================================================

def merge_products(primary_products, fallback_products):
    """
    Fusiona productos por external_id.

    Los productos extraídos mediante enlace tienen prioridad.
    """

    merged = {}

    for product in fallback_products:
        merged[product["external_id"]] = product

    for product in primary_products:
        external_id = product["external_id"]

        if external_id in merged:
            existing = merged[external_id]

            for key, value in product.items():
                if value is not None:
                    existing[key] = value
        else:
            merged[external_id] = product

    return list(merged.values())


def validate_product(product):
    """
    Validación mínima antes de guardar en MySQL.
    """

    if not product.get("external_id"):
        return False

    if not product.get("name"):
        return False

    if is_invalid_product_name(product["name"]):
        return False

    price = product.get("price")

    if price is None:
        return False

    if price <= 0:
        return False

    if price > 1000:
        return False

    return True


def extract_catalog_products(html):
    """
    Ejecuta las dos estrategias y consolida los resultados.
    """

    soup = BeautifulSoup(html, "html.parser")

    link_products = extract_products_from_links(soup)
    embedded_products = extract_products_from_embedded_text(
        html
    )

    print(
        "Productos por enlaces:",
        len(link_products)
    )

    print(
        "Productos por texto embebido:",
        len(embedded_products)
    )

    merged_products = merge_products(
        link_products,
        embedded_products
    )

    valid_products = [
        product
        for product in merged_products
        if validate_product(product)
    ]

    valid_products.sort(
        key=lambda product: product["name"].lower()
    )

    print(
        "Productos válidos finales:",
        len(valid_products)
    )

    return valid_products


# ============================================================
# GUARDADO EN MYSQL
# ============================================================
def save_raw_product(product):
    """
    Guarda o actualiza un producto del catálogo de DIA
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
    Obtiene los productos de una pagina de categoria: intenta primero
    el endpoint JSON y, si falla o no trae productos, cae al parseo
    de HTML.

    Devuelve (products, source, total_pages_hint):
        products: None si tanto el JSON como el HTML fallaron a nivel
            de red (tras agotar reintentos) - fallo transitorio, hay
            que saltar la pagina pero no dar la categoria por acabada.
            Lista (posiblemente vacia) en cualquier otro caso: una
            lista vacia significa que ya no hay mas productos (fin de
            la categoria).
        source: "json_api", "json_api_empty", "html_fallback" o
            "failed".
        total_pages_hint: total_pages leido del HTML si el fallback se
            ha ejecutado en la pagina 1 (el endpoint JSON no expone
            total_pages), None en cualquier otro caso.

    Caso especial (encontrado probando contra dia.es real): en una
    pagina > 1, si el JSON responde con normalidad pero confirma 0
    productos, NO se cae a HTML. Se comprobo que en ese caso el HTML
    tambien devuelve 404 de forma consistente, y con los reintentos
    (5s/15s/45s) eso cuesta ~75s por pagina y ademas se contabilizaba
    como error, inflando errors_count en categorias con menos paginas
    que MAX_PAGES_PER_CATEGORY. Para la pagina 1 si se sigue cayendo a
    HTML siempre (es la unica forma de conseguir total_pages y de
    distinguir "categoria vacia" de "el JSON fallo para esta
    categoria").

    Cuando el JSON SI trae productos, se hace ademas una peticion HTML
    a la misma pagina solo para leer el bloque ItemList y corregir
    name/image_url (ver extract_item_list_products): item_name del
    JSON pierde el separador decimal de los tamaños. La cruza por SKU,
    no por posicion ni por nombre.
    """

    json_products, json_network_failed = fetch_category_json(
        base_url,
        page_number
    )

    time.sleep(random.uniform(2, 4))

    if json_products:
        page_url = build_page_url(base_url, page_number)

        print(
            "URL (HTML, solo para corregir nombre/imagen vía "
            "ItemList):",
            page_url
        )

        html = fetch_page_with_retries(page_url)

        time.sleep(random.uniform(2, 4))

        total_pages_hint = None

        if html is None:
            print(
                "[AVISO] no se pudo descargar el HTML de la página",
                page_number,
                "para corregir nombres/imágenes - se guardan los "
                "productos con el nombre del JSON tal cual "
                "(name_source=json_fallback)"
            )
        else:
            if page_number == 1:
                total_pages_hint = extract_total_pages(html)

                print(
                    "Total de páginas según dia.es:",
                    total_pages_hint
                )

            item_list_map = extract_item_list_products(html)

            corrected_count = 0

            for product in json_products:
                correction = item_list_map.get(product["external_id"])

                if correction and correction.get("name"):
                    product["name"] = correction["name"]
                    product["normalized_name"] = normalize_text(
                        correction["name"]
                    )
                    product["name_source"] = "ld_json"

                    if correction.get("image_url"):
                        product["image_url"] = correction["image_url"]

                    corrected_count += 1

            print(
                "Nombres corregidos vía ItemList:",
                corrected_count,
                "de",
                len(json_products)
            )

        json_products.sort(key=lambda product: product["name"].lower())

        return json_products, "json_api", total_pages_hint

    if page_number > 1 and not json_network_failed:
        print(
            "[FIN DE CATEGORÍA] el JSON confirma 0 productos en la "
            "página",
            page_number,
            "- no se intenta HTML (ver nota en el docstring)"
        )

        return [], "json_api_empty", None

    print(
        "[JSON SIN RESULTADOS] página",
        page_number,
        "- cayendo a parseo de HTML"
    )

    page_url = build_page_url(base_url, page_number)

    print("URL (HTML):", page_url)

    html = fetch_page_with_retries(page_url)

    time.sleep(random.uniform(2, 4))

    if html is None:
        return None, "failed", None

    total_pages_hint = None

    if page_number == 1:
        total_pages_hint = extract_total_pages(html)

        print("Total de páginas según dia.es:", total_pages_hint)

    html_products = extract_catalog_products(html)

    for product in html_products:
        product["extraction_method"] = "html_fallback"

    return html_products, "html_fallback", total_pages_hint


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
    json_pages = 0
    html_fallback_pages = 0

    for page_number in range(1, MAX_PAGES_PER_CATEGORY + 1):
        print()
        print("-- Página", page_number, "--")

        products, source, total_pages_hint = get_page_products(
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
                "- fallo de red en JSON y en HTML"
            )

            continue

        if not products:
            print(
                "[SIN PRODUCTOS] página",
                page_number,
                "- se asume fin de la categoría"
            )

            break

        print(
            "Fuente de esta página:",
            source,
            "| productos:",
            len(products)
        )

        if source == "json_api":
            json_pages += 1
        else:
            html_fallback_pages += 1

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
                    product.get("extraction_method")
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

    return {
        "category_name": category_name,
        "total_pages_reported": total_pages_reported,
        "pages_scraped": pages_scraped,
        "json_pages": json_pages,
        "html_fallback_pages": html_fallback_pages
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
        print("SCRAPER CATÁLOGO DIA")
        print("Categorías a recorrer:", len(CATEGORIES))
        print("Máx. páginas por categoría:", MAX_PAGES_PER_CATEGORY)
        print("=" * 80)

        for category_name, base_url in CATEGORIES:
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
                else "desconocido (JSON no expone total_pages)"
            )

            print(
                " -",
                summary["category_name"],
                "| páginas recorridas:",
                summary["pages_scraped"],
                "de",
                total_pages_text,
                "| JSON:",
                summary["json_pages"],
                "| fallback HTML:",
                summary["html_fallback_pages"]
            )

        print("-" * 80)
        print("Productos encontrados:", totals["products_found"])
        print("Productos guardados:", totals["products_saved"])
        print("Errores:", totals["errors_count"])
        print("Estado:", status)
        print(
            "NOTA: el endpoint JSON (url_manager_initial_analytics) es "
            "la fuente principal y trae la página completa de "
            "productos. El parseo de HTML solo se usa como fallback "
            "cuando el JSON falla o no trae productos, y en ese caso "
            "sigue teniendo cobertura parcial (ver initialLoadedItems "
            "en la cabecera del archivo)."
        )
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