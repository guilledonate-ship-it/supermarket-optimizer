import re
import sys
import os
import hashlib
import requests
from bs4 import BeautifulSoup

# Permite importar db_writer.py desde la carpeta padre
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
sys.path.append(PROJECT_ROOT)

from db_writer import (
    start_scraping_log,
    finish_scraping_log,
    insert_price,
    upsert_raw_scraped_product
)


URL = "https://www.dia.es/ofertas"
SUPERMARKET = "dia"
SCRAPER_NAME = "scraper_dia_offers_v1"


PRODUCT_MAPPING = {
    # De momento solo intentamos mapear productos que existen en nuestra BBDD.
    # Si DIA devuelve nombres similares, se actualizarán.
    "Leche Pascual Entera 1L": "leche_pascual_entera_1l",
    "Leche entera marca blanca 1L": "leche_blanca_entera_1l",
    "Arroz SOS 1kg": "arroz_sos_1kg",
    "Arroz marca blanca 1kg": "arroz_blanco_1kg",
    "Pan de molde Bimbo": "pan_bimbo_molde",
    "Pan de molde marca blanca": "pan_blanco_molde",
    "Pechuga de pollo 500g": "pollo_pechuga_500g"
}


def fetch_page(url):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "es-ES,es;q=0.9"
    }

    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()

    return response.text


def parse_price(text):
    match = re.search(r"(\d+,\d{2})\s*€", text)

    if not match:
        return None

    return float(match.group(1).replace(",", "."))


def clean_text(text):
    if text is None:
        return None

    return (
        text
        .replace("\xa0", " ")
        .replace("\n", " ")
        .strip()
    )


def normalize_text(text):
    if text is None:
        return ""

    return (
        text.lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ü", "u")
        .replace("ñ", "n")
        .strip()
    )

def generate_external_id(product_name):
    """
    Genera un identificador estable a partir del nombre del producto.

    Más adelante sustituiremos este ID provisional por el identificador
    real de DIA si conseguimos extraerlo del catálogo o de su API.
    """
    normalized_name = normalize_text(product_name)

    name_hash = hashlib.sha256(
        normalized_name.encode("utf-8")
    ).hexdigest()[:24]

    return f"dia_{name_hash}"

def is_standalone_price(line):
    line = clean_text(line)

    if "/" in line:
        return False

    if "(" in line or ")" in line:
        return False

    return re.fullmatch(r"\d+,\d{2}\s*€", line) is not None


def is_unit_price(line):
    line = clean_text(line)
    line_lower = line.lower()

    return (
        "/litro" in line_lower
        or "/kilo" in line_lower
        or "/kg" in line_lower
        or "/unidad" in line_lower
        or "/ud" in line_lower
    )


def is_bad_name(line):
    """
    Filtra textos que no deben considerarse nombres de productos:
    etiquetas promocionales, navegación, categorías y encabezados.
    """
    line_clean = clean_text(line)

    if not line_clean:
        return True

    line_lower = line_clean.lower()

    bad_words = [
        # Características y promociones
        "sin gluten",
        "sin lactosa",
        "oferta club",
        "club dia",
        "dto",
        "añadir",
        "mejor valorado",
        "precio destacado",
        "ver todos",
        "gratis",
        "descuento",
        "novedad",
        "verano",
        "promoción",
        "promocion",
        "top ventas",
        "selección",
        "seleccion",
        "nuevo",
        "ahorro",
        "rebaja",
        "destacado",
        "destacados",

        # Navegación y cabeceras de la web
        "folletos",
        "tiendas",
        "recibe hoy",
        "compra online",
        "supermercado online",
        "supermercados dia",
        "comprar ofertas dia online",
        "tu supermercado online",
        "elige tu codigo postal",
        "elige código postal",
        "club dia precios",
        "folletos y tiendas",
        "envios",
        "envíos",
        "pedidos y listas",
        "codigo postal",
        "código postal",

        # Categorías que no son productos
        "quesos",
        "charcuteria",
        "charcutería",
        "carniceria",
        "carnicería",
        "pescaderia",
        "pescadería",
        "lacteos",
        "lácteos",
        "fruta",
        "verdura",
        "panaderia",
        "panadería",
        "bebidas",
        "postres",
        "congelados"
    ]

    if len(line_clean) < 4:
        return True

    if "€" in line_clean:
        return True

    for word in bad_words:
        if word in line_lower:
            return True

    return False


def looks_like_product_name(line):
    line_clean = clean_text(line)
    line_lower = line_clean.lower()

    product_indicators = [
        " l",
        " ml",
        " g",
        " kg",
        "pack",
        "x ",
        "dia",
        "alvalle",
        "bimbo",
        "pascual",
        "sos",
        "mahou",
        "frigo",
        "magnum",
        "central lechera",
        "asturiana",
        "nestlé",
        "nestle",
        "coca-cola",
        "cocacola",
        "danone",
        "gallo",
        "carbonell",
        "ybarra",
        "campofrío",
        "campofrio"
    ]

    return any(indicator in line_lower for indicator in product_indicators)


def split_product_blocks(lines):
    blocks = []
    current_block = []

    for line in lines:
        current_block.append(line)

        if line.lower().strip() == "añadir":
            blocks.append(current_block)
            current_block = []

    return blocks


def extract_product_from_block(block):
    product_name = None
    standalone_prices = []
    unit_price_text = None

    for line in block:
        line = clean_text(line)

        if product_name is None and not is_bad_name(line):
            if looks_like_product_name(line):
                product_name = line

        if is_standalone_price(line):
            price = parse_price(line)

            if price is not None:
                standalone_prices.append(price)

        if is_unit_price(line):
            unit_price_text = clean_text(line)

    if product_name is None:
        for line in block:
            line = clean_text(line)

            if not is_bad_name(line):
                product_name = line
                break

    if not product_name:
        return None

    if not standalone_prices:
        return None

    final_price = standalone_prices[-1]
    original_price = standalone_prices[0] if len(standalone_prices) > 1 else None

    # Si el precio original no tiene sentido, lo anulamos
    if original_price is not None and original_price <= final_price:
        original_price = None

    return {
        "name": product_name,
        "price": final_price,
        "original_price": original_price,
        "unit_price_text": unit_price_text
    }


def extract_products(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    lines = [
        clean_text(line)
        for line in text.split("\n")
        if clean_text(line)
    ]

    blocks = split_product_blocks(lines)
    print(f"DEBUG - Líneas extraídas: {len(lines)}")
    print(f"DEBUG - Bloques encontrados: {len(blocks)}")
    products = []
    seen = set()

    for block in blocks:
        product = extract_product_from_block(block)

        if not product:
            continue

        key = (
            product["name"],
            product["price"],
            product["original_price"]
        )

        if key in seen:
            continue

        seen.add(key)
        products.append(product)

    return products


def match_to_existing_product(scraped_name):
    normalized_scraped = normalize_text(scraped_name)

    for known_name, product_key in PRODUCT_MAPPING.items():
        normalized_known = normalize_text(known_name)

        if normalized_known in normalized_scraped or normalized_scraped in normalized_known:
            return product_key

    return None


def main():
    log_id = start_scraping_log(
        supermarket_slug=SUPERMARKET,
        scraper_name=SCRAPER_NAME
    )

    products_found = 0
    products_updated = 0
    products_pending = 0
    errors_count = 0

    try:
        html = fetch_page(URL)
        scraped_products = extract_products(html)

        products_found = len(scraped_products)

        print(f"Productos estructurados detectados: {products_found}")
        print("-" * 80)

        for product in scraped_products:
            product_name = product["name"]
            product_key = match_to_existing_product(product_name)

            try:
                if product_key:
                    # El producto ya está relacionado con un producto maestro.
                    # Guardamos un nuevo precio histórico.
                    insert_price(
                        product_key=product_key,
                        supermarket_slug=SUPERMARKET,
                        price=product["price"],
                        price_per_unit=None,
                        data_source=SCRAPER_NAME
                    )

                    products_updated += 1

                    print(
                        f"[PRECIO ACTUALIZADO] "
                        f"{product_key} | "
                        f"{product_name} | "
                        f"{product['price']}€"
                    )

                else:
                    # El producto todavía no está relacionado con products.
                    # Lo guardamos para revisión y mapeo posterior.
                    external_id = generate_external_id(product_name)

                    upsert_raw_scraped_product(
                        supermarket_slug=SUPERMARKET,
                        external_id=external_id,
                        external_name=product_name,
                        normalized_name=normalize_text(product_name),
                        price=product["price"],
                        original_price=product.get("original_price"),
                        price_per_unit=None,
                        unit_price_text=product.get("unit_price_text"),
                        product_url=product.get("product_url"),
                        image_url=product.get("image_url"),
                        category_name=product.get("category_name"),
                        available=True,
                        raw_data=product
                    )

                    products_pending += 1

                    print(
                        f"[PENDIENTE DE MAPEAR] "
                        f"{product_name} | "
                        f"{product['price']}€"
                    )

            except Exception as product_error:
                errors_count += 1

                print(
                    f"[ERROR PRODUCTO] "
                    f"{product_name}: {product_error}"
                )

        finish_scraping_log(
            log_id=log_id,
            status="success",
            products_found=products_found,
            products_updated=products_updated,
            errors_count=errors_count
        )

        print()
        print("Scraping terminado correctamente")
        print(f"Productos encontrados: {products_found}")
        print(f"Precios actualizados en BBDD: {products_updated}")
        print(f"Productos pendientes de mapeo: {products_pending}")
        print(f"Errores: {errors_count}")

    except Exception as general_error:
        finish_scraping_log(
            log_id=log_id,
            status="error",
            products_found=products_found,
            products_updated=products_updated,
            errors_count=errors_count + 1,
            error_message=str(general_error)
        )

        print(f"Error general en scraper: {general_error}")

if __name__ == "__main__":
    main()