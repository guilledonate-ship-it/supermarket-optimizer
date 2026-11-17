# ============================================================
# NOTA IMPORTANTE: SIN PROTECCION ANTIBOT DETECTADA (POR AHORA)
# ============================================================
#
# A diferencia de carrefour.es (Cloudflare Bot Management por
# fingerprint TLS), en la investigacion contra supermercado.eroski.es
# no se detecto ninguna proteccion: mas de 15 peticiones reales con
# requests (no curl) devolvieron 200 sin retos ni cabeceras de WAF.
#
# Aun asi, este scraper trata cualquier 403/429 o pagina de reto
# (Cloudflare/Akamai/captcha) como un evento que DETIENE el scraper
# por completo (no solo la pagina o categoria actual) - ver
# BotBlockDetected. La instruccion explicita fue: si aparece un
# bloqueo, parar y avisar antes de ajustar nada (pausas, reintentos,
# etc.), a diferencia de un fallo de red normal, que si seguimos
# tratando como en scraper_dia_catalog.py (reintento con backoff y
# se salta la pagina).
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

BASE_URL = "https://supermercado.eroski.es"

# Pares (nombre, url_base) de categorias a recorrer. La URL es la de
# la pagina 0 (implicita, sin parametro); las siguientes paginas se
# construyen con build_page_url.
CATEGORIES = [
    (
        # Las tres subcategorias de leche en vez de la categoria padre
        # (2059807): la padre mezcla leche con bebidas vegetales y
        # batidos, y hacia falta recorrer sus 16 paginas para llegar a
        # productos de leche real. Confirmado con peticiones reales
        # que los tres IDs existen al mismo nivel bajo 2059807.
        "Leche entera",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059806-alimentacion/2059807-leche-batidos-y-bebidas-vegetales/"
        "2059810-leche-entera/"
    ),
    (
        "Leche semidesnatada",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059806-alimentacion/2059807-leche-batidos-y-bebidas-vegetales/"
        "2059808-leche-semidesnatada/"
    ),
    (
        "Leche desnatada",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059806-alimentacion/2059807-leche-batidos-y-bebidas-vegetales/"
        "2059809-leche-desnatada/"
    ),
    (
        "Agua",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060211-bebidas/2060212-agua/"
    ),
    (
        "Pan de molde y tostado",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060118-dulces-y-desayuno/2060149-pan-de-molde-y-tostado/"
    ),
    (
        "Platanos y kiwis",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059698-frescos/2059699-frutas/2059702-platanos-y-kiwis/"
    ),
    (
        "Yogur natural",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059806-alimentacion/2059818-yogures/2059819-yogur-natural/"
    ),
    (
        # Aceite: DIA no separa por tipo (categoria "Aceites" unica,
        # mezcla oliva y girasol), asi que aqui se combinan las tres
        # subcategorias de aceite de oliva de Eroski (suave/virgen/
        # intenso) en vez de escoger solo una - si no, los productos
        # de las otras dos variedades en DIA se quedarian sin pareja
        # posible en el matching. Mismo motivo que agua-y-refrescos:
        # category_name distinto por subcategoria, se unifica despues
        # en promoter.py (CATEGORY_NAME_MAP), igual que con la leche.
        "Aceite de oliva suave",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059806-alimentacion/2059988-aceite-vinagre-sal-harina-y-pan-rallado/"
        "2059990-aceite-de-oliva-suave/"
    ),
    (
        "Aceite de oliva virgen",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059806-alimentacion/2059988-aceite-vinagre-sal-harina-y-pan-rallado/"
        "2059989-aceite-de-oliva-virgen/"
    ),
    (
        "Aceite de oliva intenso",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059806-alimentacion/2059988-aceite-vinagre-sal-harina-y-pan-rallado/"
        "2059991-aceite-de-oliva-intenso/"
    ),
    (
        # Pasta: DIA mezcla pasta corta y larga en una sola categoria,
        # asi que aqui se combinan las dos subcategorias de Eroski.
        "Macarrones y pasta corta",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059806-alimentacion/2060029-legumbres-arroz-y-pasta/"
        "2060034-macarrones-y-pasta-corta/"
    ),
    (
        "Espagueti, tallarines y pasta larga",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059806-alimentacion/2060029-legumbres-arroz-y-pasta/"
        "2060033-espagueti-tallarines-y-pasta-larga/"
    ),
    (
        "Arroz",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059806-alimentacion/2060029-legumbres-arroz-y-pasta/"
        "2060032-arroz/"
    ),
    (
        "Cafe molido",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060118-dulces-y-desayuno/2060119-la-tienda-del-cafe/"
        "2060120-cafe-molido/"
    ),
    (
        # Categoria padre "huevos" (2059760): lista productos
        # directamente (comprobado con peticion real, 20 productos en
        # la pagina 0), no hace falta bajar a las subcategorias por
        # talla/tipo.
        "Huevos",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059698-frescos/2059760-huevos/"
    ),
    (
        # Atun/bonito: DIA mezcla atun Y bonito, en aceite/tomate/
        # natural/escabeche, en una sola categoria "atun-y-bonito".
        # Eroski lo separa en 5 subcategorias bajo conservas-de-
        # pescado (comprobado que son exactamente estas 5, sin
        # contar mejillon/sardina/berberechos/etc., que son otro
        # pescado). Se combinan las 5 para no dejar fuera del
        # matching los productos de DIA que no sean "en aceite".
        "Atun en aceite",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059806-alimentacion/2060015-conservas-de-pescado/"
        "2060016-atun-aceite/"
    ),
    (
        "Bonito en aceite",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059806-alimentacion/2060015-conservas-de-pescado/"
        "2060017-bonito-aceite/"
    ),
    (
        "Atun y bonito natural",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059806-alimentacion/2060015-conservas-de-pescado/"
        "2060018-atun-y-bonito-natural/"
    ),
    (
        "Atun y bonito en escabeche",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059806-alimentacion/2060015-conservas-de-pescado/"
        "2060019-atun-y-bonito-escabeche/"
    ),
    (
        "Ventresca de bonito y atun",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059806-alimentacion/2060015-conservas-de-pescado/"
        "2060020-ventresca-de-bonito-y-atun/"
    ),
    (
        "Papel higienico",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060538-limpieza/2060592-papel-higienico-y-panuelos/"
        "2060593-papel-higienico/"
    ),
    (
        # Legumbres: se combina con "legumbres-cocidas" porque DIA
        # mezcla secas y cocidas en una sola categoria (comprobado
        # con peticion real) - si no, los "garbanzos cocidos" de DIA
        # se quedarian sin nada que comparar en Eroski.
        "Legumbres",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059806-alimentacion/2060029-legumbres-arroz-y-pasta/"
        "2060030-legumbres/"
    ),
    (
        "Legumbres cocidas",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059806-alimentacion/2060029-legumbres-arroz-y-pasta/"
        "2060031-legumbres-cocidas/"
    ),
    (
        "Harina",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059806-alimentacion/2059988-aceite-vinagre-sal-harina-y-"
        "pan-rallado/2059998-harina/"
    ),
    (
        # Levaduras: en Eroski esta archivada bajo azucar/reposteria,
        # no junto a la harina, pero es el equivalente real a la
        # mitad "levaduras" de "harinas y levaduras" de DIA.
        "Levaduras y otros",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060118-dulces-y-desayuno/2060182-azucar-y-edulcorante/"
        "5000287-levaduras-y-otros/"
    ),
    (
        # Azucar: DIA no separa por formato/tipo - se combinan las 6
        # subcategorias de azucar/edulcorante de Eroski (se excluyen
        # las que no son azucar/edulcorante: levaduras, gelatinas,
        # sirope, adornos, pasteles, flanes - eso es otra cosa, no
        # tiene equivalente en la categoria de DIA). Eroski no separa
        # "miel" en esta seccion; queda fuera de los dos lados.
        "Azucar en paquete",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060118-dulces-y-desayuno/2060182-azucar-y-edulcorante/"
        "2060183-azucar-paquete/"
    ),
    (
        "Azucar moreno",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060118-dulces-y-desayuno/2060182-azucar-y-edulcorante/"
        "2060184-azucar-moreno/"
    ),
    (
        "Azucar especial",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060118-dulces-y-desayuno/2060182-azucar-y-edulcorante/"
        "2060185-azucar-especial/"
    ),
    (
        "Azucarillos",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060118-dulces-y-desayuno/2060182-azucar-y-edulcorante/"
        "2060186-azucarillos/"
    ),
    (
        "Edulcorante y fructosa",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060118-dulces-y-desayuno/2060182-azucar-y-edulcorante/"
        "2060187-edulcorante-y-fructosa/"
    ),
    (
        "Azucar ecologico",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060118-dulces-y-desayuno/2060182-azucar-y-edulcorante/"
        "2060965-azucar-ecologico/"
    ),
    (
        # Galletas: se combinan las 5 subcategorias no infantiles/
        # sin-gluten de Eroski con las 3 de DIA (ver comentario en
        # scraper_dia_catalog.py) - el motivo explicito es cubrir
        # producto, no simetria: sin "rellenas" aqui, las Oreo de DIA
        # se quedarian sin pareja posible.
        "Galletas clasicas",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060118-dulces-y-desayuno/2060135-galletas/"
        "2060136-galletas-clasicas/"
    ),
    (
        "Galletas snack, surtidas y pastas",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060118-dulces-y-desayuno/2060135-galletas/"
        "2060137-galletas-snack-surtidas-y-pastas/"
    ),
    (
        "Galletas fibra, integrales y ecologicas",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060118-dulces-y-desayuno/2060135-galletas/"
        "2060139-galletas-fibra-integrales-y-ecologicas/"
    ),
    (
        "Galletas rellenas y cubiertas",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060118-dulces-y-desayuno/2060135-galletas/"
        "2060141-galletas-rellenas-y-cubiertas/"
    ),
    (
        "Galletas aperitivos",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060118-dulces-y-desayuno/2060135-galletas/"
        "2060142-galletas-aperitivos/"
    ),
    (
        "Cornflakes",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060118-dulces-y-desayuno/5000189-cereales-y-barritas/"
        "2060146-cornflakes/"
    ),
    (
        "Cereales fibra, linea y muesli",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060118-dulces-y-desayuno/5000189-cereales-y-barritas/"
        "2060148-cereales-fibra-linea-y-muesli/"
    ),
    (
        # Queso: 9 subcategorias, combinadas con las 9 de DIA -
        # mismo motivo que galletas (cubrir producto, no simetria).
        # Se excluyen membrillo (no es queso), infantiles-y-porciones
        # (formato infantil) y tablas-y-bandejas (formato bandeja,
        # no un tipo de queso) por no tener equivalente en DIA.
        "Queso fresco",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059698-frescos/2059858-queso-y-membrillo/"
        "2059859-queso-fresco/"
    ),
    (
        "Queso tierno",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059698-frescos/2059858-queso-y-membrillo/"
        "2059860-queso-tierno/"
    ),
    (
        "Queso semicurado",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059698-frescos/2059858-queso-y-membrillo/"
        "2059861-queso-semicurado-/"
    ),
    (
        "Queso curado y viejo",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059698-frescos/2059858-queso-y-membrillo/"
        "2059862-queso-curado-y-viejo/"
    ),
    (
        "Queso lonchas y fundidos",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059698-frescos/2059858-queso-y-membrillo/"
        "2059863-queso-lonchas-y-fundidos/"
    ),
    (
        "Quesos internacionales",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059698-frescos/2059858-queso-y-membrillo/"
        "2059864-quesos-internacionales/"
    ),
    (
        "Queso azul",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059698-frescos/2059858-queso-y-membrillo/"
        "2059865-queso-azul/"
    ),
    (
        "Queso tarrina",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059698-frescos/2059858-queso-y-membrillo/"
        "2059868-queso-tarrina/"
    ),
    (
        "Queso rallado",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059698-frescos/2059858-queso-y-membrillo/"
        "2059869-queso-rallado/"
    ),
    (
        "Jamon cocido",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059698-frescos/2059872-fiambres-y-cocidos/"
        "2059873-jamon-cocido/"
    ),
    (
        "Patatas, verduras y setas congeladas",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059919-congelados/2059942-patatas-verduras-y-setas/"
    ),
    (
        # Detergente: se combinan los 3 formatos de maquina + prendas
        # delicadas (DIA incluye "detergente liquido para prendas
        # delicadas" en su categoria unica, comprobado con peticion
        # real) - no se incluye "lavado-a-mano" porque no aparecio
        # nada equivalente en la muestra de DIA.
        "Detergente maquina liquido",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060538-limpieza/2060539-detergente-jabon-y-suavizante/"
        "2060540-detergente-maquina-liquido/"
    ),
    (
        "Detergente maquina polvo",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060538-limpieza/2060539-detergente-jabon-y-suavizante/"
        "2060541-detergente-maquina-polvo/"
    ),
    (
        "Detergente maquina pastillas",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060538-limpieza/2060539-detergente-jabon-y-suavizante/"
        "2060542-detergente-maquina-pastillas/"
    ),
    (
        "Detergente prendas delicadas",
        "https://supermercado.eroski.es/es/supermercado/"
        "2060538-limpieza/2060539-detergente-jabon-y-suavizante/"
        "2060543-prendas-delicadas/"
    ),
    (
        # DIA combina mantequilla y margarina en una sola categoria -
        # Eroski las separa (mismo parent "mantequilla-nata-y-cremas"
        # que tambien incluye nata/bechamel, excluidos por no tener
        # equivalente en la categoria de DIA).
        "Mantequilla",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059806-alimentacion/2059851-mantequilla-nata-y-cremas/"
        "2059852-mantequilla/"
    ),
    (
        "Margarina",
        "https://supermercado.eroski.es/es/supermercado/"
        "2059806-alimentacion/2059851-mantequilla-nata-y-cremas/"
        "2059853-margarina/"
    ),
]

# Limite de paginas por categoria, para poder probar el scraper sin
# recorrer el catalogo entero.
MAX_PAGES_PER_CATEGORY = 15

# Espera entre reintentos de una misma peticion tras un fallo de red
# normal (segundos). Igual que en DIA. No se usa para bloqueos de
# bot (ver BotBlockDetected), que detienen el scraper de inmediato.
RETRY_DELAYS_SECONDS = [5, 15, 45]

# Pausa aleatoria entre peticiones normales.
REQUEST_DELAY_SECONDS = (3, 6)

SUPERMARKET_SLUG = "eroski"
SCRAPER_NAME = "scraper_eroski_catalog_v1"

# Se asigna dinamicamente antes de procesar cada categoria (ver
# scrape_category).
CATEGORY_NAME = None

REQUEST_TIMEOUT = 30

# Marcadores de pagina de reto/bloqueo (Cloudflare, Akamai, WAF
# generico). Si aparecen en el cuerpo de la respuesta, se trata como
# bloqueo aunque el status sea 200 (algunos retos JS responden 200).
CHALLENGE_MARKERS = [
    "just a moment",
    "attention required",
    "cf-challenge",
    "captcha",
    "access denied",
    "request blocked"
]

# Cabeceras Server que no vimos en la investigacion (Server: None en
# todas las pruebas). Si aparecen, es una senal de que se ha activado
# algun WAF que antes no estaba.
UNEXPECTED_SERVER_HEADERS = ["cloudflare", "akamaighost"]


class BotBlockDetected(Exception):
    """
    Se lanza cuando una respuesta parece un bloqueo/reto antibot
    (403, 429, cuerpo de reto conocido, o cabecera Server inesperada).

    A diferencia de un fallo de red normal, esto NO se reintenta ni
    se salta - detiene todo el scraper para que se revise a mano,
    tal y como pidio el usuario.
    """
    pass


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

    return "eroski_hash_" + name_hash


# ============================================================
# DESCARGA DE LA PÁGINA
# ============================================================

def fetch_page(url):
    """
    Descarga la página de catálogo.

    Lanza BotBlockDetected (no la captura) ante cualquier señal de
    bloqueo/reto antibot - ver cabecera del archivo.
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

    server_header = (response.headers.get("Server") or "").lower()

    if server_header in UNEXPECTED_SERVER_HEADERS:
        raise BotBlockDetected(
            "Cabecera Server inesperada ('" + server_header + "') en "
            + url + " - no se vio en la investigacion previa."
        )

    if response.status_code in (403, 429):
        raise BotBlockDetected(
            "HTTP " + str(response.status_code) + " en " + url
        )

    body_lower = response.text[:5000].lower()

    for marker in CHALLENGE_MARKERS:
        if marker in body_lower:
            raise BotBlockDetected(
                "Marcador de reto/bloqueo ('" + marker + "') "
                "encontrado en el cuerpo de la respuesta de " + url
            )

    response.raise_for_status()

    print("HTTP status:", response.status_code)
    print("HTML descargado:", len(response.text), "caracteres")

    return response.text


def fetch_page_with_retries(url):
    """
    Descarga una pagina con reintentos ante fallos de red normales
    (5s, 15s, 45s), igual que en DIA.

    BotBlockDetected NO se reintenta: se deja propagar hacia arriba
    para detener el scraper por completo (ver cabecera del archivo).
    """

    last_error = None
    max_attempts = len(RETRY_DELAYS_SECONDS) + 1

    for attempt in range(1, max_attempts + 1):
        try:
            return fetch_page(url)

        except BotBlockDetected:
            raise

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
    Construye la URL de la pagina N de una categoria de
    supermercado.eroski.es.

    Pagina con el parametro de query ?pageNumber=N, indexado en 0
    (comprobado con peticiones reales: la URL base sin parametro es
    la pagina 0, luego pageNumber=1, pageNumber=2...). page_number
    aqui es 1-indexado (igual que en DIA/Carrefour), asi que se
    traduce restando 1.
    """

    if page_number <= 1:
        return base_url

    page_number_param = page_number - 1

    separator = "&" if "?" in base_url else "?"

    return base_url + separator + "pageNumber=" + str(page_number_param)


def extract_total_pages(html):
    """
    Lee el total de paginas a partir de los enlaces
    ?pageNumber=N del bloque de paginacion.

    La pagina 0 nunca aparece enlazada (es la URL base sin
    parametro), asi que el total de paginas es el pageNumber maximo
    enlazado + 1. Si no hay ningun enlace de paginacion, la
    categoria tiene una sola pagina (comprobado con "Platanos y
    kiwis" y "Yogur natural", que no traen bloque de paginas).
    """

    page_numbers = [
        int(match)
        for match in re.findall(r"pageNumber=(\d+)", html)
    ]

    if not page_numbers:
        return 1

    return max(page_numbers) + 1


# ============================================================
# BLOQUE data-metrics: PRODUCTOS RENDERIZADOS EN SERVIDOR
# ============================================================
#
# Cada tarjeta de producto trae un enlace
# <a class="product-title-link" data-metrics='{...}'> con un evento
# GA4 "select_item" completo: item_id, item_name, item_brand,
# price, item_category/2/3, item_list_name, item_list_id,
# item_affiliation. Todo servido en el HTML, sin ejecutar JS.
#
# OJO: cada producto aparece duplicado (dos <a> identicos con el
# mismo data-metrics) - comprobado en varias categorias. Se deduplica
# por item_id.
#
# No trae EAN.
# ============================================================

def extract_products_from_metrics(html):
    """
    Extrae y decodifica los bloques data-metrics de una pagina de
    categoria.

    Devuelve una lista de dicts {producto} deduplicada por item_id,
    en el mismo orden de aparicion (primera ocurrencia).
    """

    soup = BeautifulSoup(html, "html.parser")

    links = soup.find_all("a", attrs={"data-metrics": True})

    print("Enlaces con data-metrics encontrados:", len(links))

    products_by_id = {}

    for link in links:
        raw_metrics = link.get("data-metrics")

        try:
            metrics = json.loads(raw_metrics)
        except ValueError:
            continue

        ecommerce = metrics.get("ecommerce")

        if not isinstance(ecommerce, dict):
            continue

        items = ecommerce.get("items")

        if not items or not isinstance(items, list):
            continue

        item = items[0]

        if not isinstance(item, dict):
            continue

        item_id = clean_text(item.get("item_id"))

        if not item_id:
            continue

        if item_id in products_by_id:
            continue

        products_by_id[item_id] = (item, link)

    print(
        "Productos únicos (deduplicados por item_id):",
        len(products_by_id)
    )

    products = []

    for item_id, (item, link) in products_by_id.items():
        products.append(
            convert_metrics_item_to_product(item_id, item, link)
        )

    return products


def convert_metrics_item_to_product(item_id, item, link):
    """
    Convierte un item de ecommerce.items (data-metrics) al formato
    comun usado por save_raw_product.
    """

    name = clean_text(item.get("item_name"))

    price = item.get("price")

    try:
        price = round(float(price), 2) if price is not None else None
    except (TypeError, ValueError):
        price = None

    href = link.get("href")
    product_url = clean_text(href) if href else None

    product = {
        "external_id": item_id,
        "name": name,
        "normalized_name": normalize_text(name),
        "price": price,
        "original_price": None,
        "price_per_unit": None,
        "unit_price_text": None,
        "product_url": product_url,
        "image_url": None,
        "category_name": CATEGORY_NAME,
        "available": True,
        "extraction_method": "eroski_html",
        "item_brand": item.get("item_brand"),
        "item_category": item.get("item_category"),
        "item_category2": item.get("item_category2"),
        "item_category3": item.get("item_category3"),
        "item_variant": item.get("item_variant"),
        "item_list_name": item.get("item_list_name"),
        "item_list_id": item.get("item_list_id"),
        "item_affiliation": item.get("item_affiliation"),
        "index": item.get("index"),
        "quantity": item.get("quantity")
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
    partir de los bloques data-metrics.
    """

    products = extract_products_from_metrics(html)

    valid_products = [
        product
        for product in products
        if validate_product(product)
    ]

    print("Productos válidos en esta página:", len(valid_products))

    valid_products.sort(key=lambda product: product["name"].lower())

    return valid_products


# ============================================================
# GUARDADO EN MYSQL
# ============================================================

def save_raw_product(product):
    """
    Guarda o actualiza un producto del catálogo de Eroski
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

    BotBlockDetected no se captura aqui - se deja propagar para
    detener el scraper por completo.
    """

    page_url = build_page_url(base_url, page_number)

    print("URL:", page_url)

    html = fetch_page_with_retries(page_url)

    if html is None:
        return None, None

    total_pages_hint = None

    if page_number == 1:
        total_pages_hint = extract_total_pages(html)

        print("Total de páginas según eroski.es:", total_pages_hint)

    products = extract_page_products(html)

    return products, total_pages_hint


def scrape_category(category_name, base_url, totals):
    """
    Recorre las paginas de una categoria (hasta MAX_PAGES_PER_CATEGORY)
    y guarda los productos encontrados.

    Acumula los contadores en `totals` (compartido entre categorias
    para el scraping_log unico de la ejecucion) y devuelve un resumen
    de la categoria para el informe final.

    BotBlockDetected no se captura aqui - se deja propagar hacia
    main() para detener el scraper por completo.
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
                    product.get("item_brand")
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
        print("SCRAPER CATÁLOGO EROSKI")
        print("Categorías a recorrer:", len(CATEGORIES))
        print("Máx. páginas por categoría:", MAX_PAGES_PER_CATEGORY)
        print("=" * 80)

        for index, (category_name, base_url) in enumerate(CATEGORIES):
            summary = scrape_category(
                category_name,
                base_url,
                totals
            )

            category_summaries.append(summary)

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

    except BotBlockDetected as block_error:
        finish_scraping_log(
            log_id=log_id,
            status="error",
            products_found=totals["products_found"],
            products_updated=totals["products_saved"],
            errors_count=totals["errors_count"] + 1,
            error_message="BLOQUEO ANTIBOT: " + str(block_error)
        )

        print()
        print("=" * 80)
        print("SCRAPER DETENIDO: POSIBLE BLOQUEO ANTIBOT")
        print(str(block_error))
        print(
            "No se ha reintentado ni se ha seguido con otras "
            "categorías. Revisar antes de volver a ejecutar."
        )
        print("=" * 80)

        raise

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
