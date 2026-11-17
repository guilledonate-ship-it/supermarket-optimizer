"""
Normalizador de productos scrapeados.

normalize_product() es puro (no toca la base de datos). El resto del
módulo lee de raw_scraped_products y, si se pide explícitamente con
--apply, persiste el resultado en las columnas añadidas por
migrations/001_add_normalized_columns.sql y
migrations/002_add_brand_type_source.sql (esas migraciones hay que
haberlas ejecutado a mano antes; este archivo no las ejecuta).
"""

import argparse
import json
import re
import sys

from db import get_connection


# ============================================================
# CONFIGURACIÓN: MARCAS BLANCAS POR CADENA
# ============================================================
#
# Clasificación de brand_type. Cualquier marca detectada que no
# aparezca aquí (como marca completa o como palabra dentro de una
# marca detectada, p. ej. "Dia" dentro de "El Molino de Dia") se
# clasifica como "marca". No se consulta ninguna fuente externa:
# esta lista se basa únicamente en conocimiento ya dado.
# ============================================================

PRIVATE_LABEL_BRANDS = {
    "DIA": ["Dia Láctea", "Dia", "As", "Baby Smile"],
    "Mercadona": ["Hacendado", "Bosque Verde", "Deliplus"],
    "Carrefour": ["Carrefour"],
    "Auchan/Alcampo": ["Auchan", "Alcampo", "Producción Controlada"],
    "El Corte Inglés": ["Aliada", "El Corte Inglés"],
    "Eroski": [
        "Eroski",
        "Eroski Basic",
        "E.Basic",
        "Eroski Natur",
        "E. Natur",
        "Eroski Seleqtia",
        "Seleqtia",
    ],
}

# Marcas de terceros observadas en los productos ya scrapeados.
# Se usan solo para DETECTAR la marca en el texto; no afectan a la
# clasificación marca_blanca / marca, que depende exclusivamente de
# PRIVATE_LABEL_BRANDS.
OTHER_KNOWN_BRANDS = [
    "Dia Selección Mundial",
    "El Molino de Dia",
    "El afortunado",
    "Asturiana",
    "Pascual",
    "Lauki",
    "Celta",
    "Madriz",
    "Covap",
    "Danone",
    "Aquarel",
    "Bezoya",
    "Solán de Cabras",
    "Fonter",
    "San Pellegrino",
    "Font Vella",
    "Lanjarón",
    "Ursu9",
    "Aquabona",
    "Cabreiroá",
    "Fuente Primavera",
    "Thai Coco",
    "Dr. Schar",
]


def _build_brand_patterns():
    """
    Junta marcas blancas + marcas de terceros, elimina duplicados y
    las ordena de más larga a más corta para que, por ejemplo,
    "Dia Láctea" se detecte antes que "Dia".
    """

    all_brands = []

    for chain_brands in PRIVATE_LABEL_BRANDS.values():
        all_brands.extend(chain_brands)

    all_brands.extend(OTHER_KNOWN_BRANDS)

    seen = set()
    unique_brands = []

    for brand in all_brands:
        key = brand.lower()

        if key not in seen:
            seen.add(key)
            unique_brands.append(brand)

    unique_brands.sort(key=len, reverse=True)

    return [
        (brand, re.compile(r"\b" + re.escape(brand) + r"\b", re.IGNORECASE))
        for brand in unique_brands
    ]


_BRAND_PATTERNS = _build_brand_patterns()

def _build_private_label_patterns():
    """
    Un patrón por marca blanca, usado para clasificar brand_type.

    Las marcas de dos letras o menos (p. ej. "As") son muy propensas
    a falsos positivos si se buscan como simple palabra dentro de una
    marca detectada más larga, así que para esas exigimos coincidencia
    exacta con el texto completo de la marca detectada.
    """

    patterns = []

    for chain_brands in PRIVATE_LABEL_BRANDS.values():
        for private_brand in chain_brands:
            if len(private_brand) <= 2:
                pattern = re.compile(
                    r"^" + re.escape(private_brand) + r"$",
                    re.IGNORECASE
                )
            else:
                pattern = re.compile(
                    r"\b" + re.escape(private_brand) + r"\b",
                    re.IGNORECASE
                )

            patterns.append(pattern)

    return patterns


_PRIVATE_LABEL_PATTERNS = _build_private_label_patterns()


# ============================================================
# EXTRACCIÓN DE TAMAÑO / FORMATO
# ============================================================

# Unidades por abreviatura (ml, cl, dl, kg, g, l) o escritas en
# palabra (litro/litros, mililitro/mililitros, centilitro/
# centilitros, decilitro/decilitros, kilo/kilos/kilogramo/
# kilogramos, gramo/gramos). El \b final impide que, p. ej., "kilo"
# case dentro de "kilogramo" (backtracking normal de alternancia).
_UNIT_ALTERNATION = (
    r"(ml|cl|dl|kg|g|l|"
    r"litros?|mililitros?|centilitros?|decilitros?|"
    r"kilogramos?|kilos?|gramos?)"
)

# "pack 6 x 1,5 L", "4 x 125 g", "3 x 200 ml", "2 x 1 litro"...
_PACK_SIZE_REGEX = re.compile(
    r"(\d+)\s*[xX]\s*(\d+(?:[.,]\d+)?)\s*" + _UNIT_ALTERNATION + r"\b",
    re.IGNORECASE
)

# "1 L", "1.5 L", "6,25 L", "900 g", "1 Kg", "botella 1 litro",
# "botella 1,5 litros" (+ "aprox." opcional detrás)
_SINGLE_SIZE_REGEX = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*" + _UNIT_ALTERNATION + r"\b(?:\s*aprox\.?)?",
    re.IGNORECASE
)

# Fallback para productos contados en unidades sueltas ("8 unidades",
# "1 unidad", "12 rollos" de papel higiénico - un rollo cuenta como
# una unidad; "30 dosis"/"30 lavados" de detergente - cada dosis o
# lavado cuenta como una unidad). OJO: "unidades?" por sí solo NO
# matchea "unidad" en singular (le faltaría la "e" antes de la "s"
# opcional) - de ahí "unidad(?:es)?" en vez de "unidades?".
#
# "dosis" y "lavados" son el mismo concepto de detergente con
# palabras distintas por cadena: Eroski usa "dosis" (188 productos
# antes de este fix), DIA usa "lavados" (33 productos) - juntas eran
# el 93% de los productos sin normalizar antes de este cambio, y
# como ninguna de las dos se reconocía, esos productos ni siquiera
# entraban en el matching (normalized_size quedaba NULL).
#
# El (?<![+=\d]) delante del número evita que "paquete 4+2 rollos" o
# "paquete 4=12 rollos" (packs promocionales de Eroski) se lean como
# "2 rollos"/"12 rollos": sin ese guardado, re.search encuentra igual
# el número que va justo antes de "rollos" aunque esté precedido por
# "+" o "=", y normalizaría mal una notación que no es un simple
# "N rollos". Hace falta excluir tambien \d (no solo + =): en
# "4=12 rollos" el primer intento (arrancar en el "1" de "12") ya
# queda bloqueado por el "=", pero \d+ puede arrancar igual en el "2"
# de "12" (precedido por el propio "1", un digito) y colar un falso
# "2 rollos" si solo se excluyen + y =. Se dejan sin normalizar a
# propósito (son pocos casos).
_UNIT_COUNT_REGEX = re.compile(
    r"(?<![+=\d])(\d+)\s*(?:unidad(?:es)?|uds?\.?|rollos?|dosis|lavados?)\b",
    re.IGNORECASE
)

# Docenas ("cartón 1 docena", "caja 10 docenas") - se convierten a
# unidades multiplicando por 12 (ver _find_size), para poder comparar
# "1 docena" de huevos con "12 huevos" de otra cadena. Mismo guardado
# (?<![+=\d]) que _UNIT_COUNT_REGEX, por si acaso.
_DOZEN_COUNT_REGEX = re.compile(
    r"(?<![+=\d])(\d+)\s*docenas?\b",
    re.IGNORECASE
)

# Palabras de formato/envase que no forman parte del nombre del producto.
_FORMAT_WORDS_REGEX = re.compile(
    r"\b(pack|botella|granel|aprox\.?)\b",
    re.IGNORECASE
)


def _parse_decimal(text):
    return float(text.replace(",", "."))


# Mapea tanto abreviaturas como formas en palabra (singular/plural) a
# la unidad canónica usada por _normalize_size.
_UNIT_WORD_TO_ABBR = {
    "l": "L",
    "litro": "L",
    "litros": "L",
    "ml": "ml",
    "mililitro": "ml",
    "mililitros": "ml",
    "cl": "cl",
    "centilitro": "cl",
    "centilitros": "cl",
    "dl": "dl",
    "decilitro": "dl",
    "decilitros": "dl",
    "kg": "kg",
    "kilo": "kg",
    "kilos": "kg",
    "kilogramo": "kg",
    "kilogramos": "kg",
    "g": "g",
    "gramo": "g",
    "gramos": "g",
}


def _normalize_unit_type(raw_unit):
    return _UNIT_WORD_TO_ABBR.get(raw_unit.lower(), raw_unit.lower())


def _find_brand(name):
    """
    Devuelve (texto_de_marca, (inicio, fin)) o (None, None).
    """

    for _, pattern in _BRAND_PATTERNS:
        match = pattern.search(name)

        if match:
            return match.group(0), match.span()

    return None, None


def _find_size(name):
    """
    Devuelve (pack_units, unit_size, unit_type, span) analizando el
    nombre del producto. pack_units siempre es un entero (1 si no es
    un pack). unit_size/unit_type/span son None si no se detecta
    ningún tamaño.
    """

    pack_match = _PACK_SIZE_REGEX.search(name)

    if pack_match:
        pack_units = int(pack_match.group(1))
        unit_size = _parse_decimal(pack_match.group(2))
        unit_type = _normalize_unit_type(pack_match.group(3))

        return pack_units, unit_size, unit_type, pack_match.span()

    single_matches = list(_SINGLE_SIZE_REGEX.finditer(name))

    if single_matches:
        match = single_matches[-1]
        unit_size = _parse_decimal(match.group(1))
        unit_type = _normalize_unit_type(match.group(2))

        return 1, unit_size, unit_type, match.span()

    unit_count_match = _UNIT_COUNT_REGEX.search(name)

    if unit_count_match:
        unit_size = float(unit_count_match.group(1))

        return 1, unit_size, "ud", unit_count_match.span()

    dozen_match = _DOZEN_COUNT_REGEX.search(name)

    if dozen_match:
        unit_size = float(dozen_match.group(1)) * 12

        return 1, unit_size, "ud", dozen_match.span()

    return 1, None, None, None


def _classify_brand_type(brand):
    """
    "marca_blanca" si la marca detectada contiene (como palabra
    completa) alguna de las marcas blancas de PRIVATE_LABEL_BRANDS.
    "generico" si no se detectó ninguna marca (p. ej. fruta a granel).
    En cualquier otro caso, "marca".
    """

    if not brand:
        return "generico"

    for pattern in _PRIVATE_LABEL_PATTERNS:
        if pattern.search(brand):
            return "marca_blanca"

    return "marca"


# ============================================================
# COMBINAR item_brand / item_brand_type (JSON de dia.es) CON LA
# HEURÍSTICA DE NOMBRE
# ============================================================
#
# Comprobado contra los 152 productos scrapeados vía JSON: de 15
# discrepancias entre item_brand_type y la heurística de nombre, en
# 11 acertaba dia.es (marcas de terceros que no estaban en
# OTHER_KNOWN_BRANDS: Perrier, Bimbo, Activia...) y en 4 acertaba la
# heurística (fruta a granel con item_brand="sin marca" pero
# item_brand_type="n", contradictorio). item_brand_type tiene además
# un tercer valor, "r", que no encaja en el eje marca/marca_blanca
# (agrupa tanto una submarca propia de Dia como una marca de tercero).
# ============================================================

# Valores de item_brand que en la práctica significan "no tiene
# marca" (fruta a granel, etc.), no un nombre de marca real.
_NO_BRAND_VALUES = {"sin marca", "sin  marca", "no brand", ""}

# Vocales/eñe con o sin tilde, para poder localizar item_brand (que
# llega sin acentos) dentro de external_name (que sí los tiene) y así
# poder recortarlo al construir product_core.
_ACCENT_CLASS_BY_LETTER = {
    "a": "[aáàâä]",
    "e": "[eéèêë]",
    "i": "[iíìîï]",
    "o": "[oóòôö]",
    "u": "[uúùûü]",
    "n": "[nñ]",
}


def _is_no_brand_value(item_brand):
    if item_brand is None:
        return False

    return item_brand.strip().lower() in _NO_BRAND_VALUES


def _find_span_of_text(name, text):
    """
    Busca `text` dentro de `name` sin distinguir mayúsculas, acentos
    ni apóstrofes (item_brand llega en minúsculas y sin acentos, y
    puede o no traer apóstrofe aunque external_name sí lo tenga o al
    revés - comprobado con Kellogg's/Kelloggs de Eroski, donde
    item_brand="KELLOGGS" pero external_name dice "KELLOGG'S": si no
    se ignora el apóstrofe, la búsqueda falla, la marca no se recorta
    de product_core y se queda pegada al texto, rompiendo el
    matching de esos productos), y devuelve su posición real en
    `name` para poder recortarla en product_core. None si no aparece.

    Afecta a cualquier marca con apóstrofe, no solo Kellogg's - el
    hueco opcional se inserta tras cada carácter (haya o no apóstrofe
    en `text` en ese punto) para cubrir los dos sentidos: que `name`
    tenga un apóstrofe que `text` no tiene, o al revés.
    """

    if not text:
        return None

    pattern_parts = []

    for char in text:
        if char in "'’`":
            continue

        lower_char = char.lower()

        if lower_char in _ACCENT_CLASS_BY_LETTER:
            pattern_parts.append(_ACCENT_CLASS_BY_LETTER[lower_char])
        else:
            pattern_parts.append(re.escape(char))

        pattern_parts.append(r"['’`]?")

    pattern = re.compile(
        r"\b" + "".join(pattern_parts) + r"\b",
        re.IGNORECASE
    )

    match = pattern.search(name)

    return match.span() if match else None


def _resolve_brand_and_type(name, item_brand, item_brand_type):
    """
    Combina item_brand/item_brand_type (si vienen del JSON de
    dia.es) con la heurística de nombre, con esta prioridad:

    1. item_brand es "sin marca" (o equivalente) -> "generico",
       ignorando item_brand_type por completo. Cubre fruta a granel.
    2. item_brand_type == "d" -> "marca_blanca".
       item_brand_type == "n" -> "marca".
    3. item_brand_type == "r" u otro valor no reconocido -> cae a la
       heurística de PRIVATE_LABEL_BRANDS.
    4. No hay item_brand_type (otros supermercados, o fallback HTML)
       -> heurística.

    brand: si item_brand está disponible y no es "sin marca", se usa
    tal cual (ya viene resuelto por dia.es) en vez de extraerlo del
    nombre. Si no, se extrae del nombre como antes.

    Devuelve (brand, brand_span, brand_type, brand_type_source).
    """

    if _is_no_brand_value(item_brand):
        return None, None, "generico", "sin_marca"

    if item_brand:
        brand = item_brand
        brand_span = _find_span_of_text(name, item_brand)
    else:
        brand, brand_span = _find_brand(name)

    if item_brand_type == "d":
        return brand, brand_span, "marca_blanca", "dia_json"

    if item_brand_type == "n":
        return brand, brand_span, "marca", "dia_json"

    # item_brand_type es "r", otro valor no reconocido, o no vino.
    brand_type = _classify_brand_type(brand)

    return brand, brand_span, brand_type, "heuristica"


def _normalize_size(total_size, unit_type):
    """
    Convierte total_size/unit_type a una unidad estándar:
    litros para L/ml, kilos para kg/g, o "ud" tal cual.

    Devuelve (normalized_size, normalized_unit_type), ambos None si
    no se detectó tamaño.
    """

    if total_size is None or unit_type is None:
        return None, None

    if unit_type == "L":
        return round(total_size, 3), "L"

    if unit_type == "ml":
        return round(total_size / 1000.0, 3), "L"

    if unit_type == "cl":
        return round(total_size / 100.0, 3), "L"

    if unit_type == "dl":
        return round(total_size / 10.0, 3), "L"

    if unit_type == "kg":
        return round(total_size, 3), "kg"

    if unit_type == "g":
        return round(total_size / 1000.0, 3), "kg"

    if unit_type == "ud":
        return round(total_size, 3), "ud"

    return None, None


def _compute_normalized_unit_price(
    current_price,
    normalized_size,
    normalized_unit_type
):
    if current_price is None or normalized_size is None:
        return None

    if normalized_size <= 0:
        return None

    if normalized_unit_type not in ("L", "kg"):
        return None

    return round(float(current_price) / normalized_size, 2)


def _build_product_core(name, brand_span, size_span):
    spans = [span for span in (brand_span, size_span) if span is not None]
    spans.sort(key=lambda span: span[0], reverse=True)

    core = name

    for start, end in spans:
        core = core[:start] + " " + core[end:]

    core = _FORMAT_WORDS_REGEX.sub(" ", core)
    core = _UNIT_COUNT_REGEX.sub(" ", core)
    core = _DOZEN_COUNT_REGEX.sub(" ", core)
    core = re.sub(r"[,;]", " ", core)
    core = re.sub(r"\s+", " ", core).strip()
    core = core.lower()

    return core if core else None


# ============================================================
# FUNCIÓN PRINCIPAL
# ============================================================

def normalize_product(
    external_name,
    current_price,
    item_brand=None,
    item_brand_type=None
):
    """
    Analiza external_name/current_price de un producto scrapeado y
    devuelve un dict con marca, formato y precio normalizado.

    item_brand / item_brand_type son opcionales: vienen de
    raw_data.item_brand / raw_data.item_brand_type, presentes solo en
    productos de dia.es con extraction_method="json_api". Si no se
    pasan (fallback HTML, u otro supermercado en el futuro), se usa
    solo la heurística de nombre, igual que antes.

    No accede a la base de datos.
    """

    name = external_name or ""

    brand, brand_span, brand_type, brand_type_source = (
        _resolve_brand_and_type(name, item_brand, item_brand_type)
    )

    # Normaliza a minúsculas siempre, venga de item_brand (que llega
    # en mayúsculas en Eroski, minúsculas en DIA...) o de la
    # heurística de nombre, para que la misma marca de distintos
    # supermercados compare igual (p. ej. "ASTURIANA" == "asturiana").
    if brand:
        brand = brand.lower()

    pack_units, unit_size, unit_type, size_span = _find_size(name)

    if unit_size is not None:
        total_size = round(pack_units * unit_size, 3)
    else:
        total_size = None

    normalized_size, normalized_unit_type = _normalize_size(
        total_size,
        unit_type
    )

    normalized_unit_price = _compute_normalized_unit_price(
        current_price,
        normalized_size,
        normalized_unit_type
    )

    product_core = _build_product_core(name, brand_span, size_span)

    return {
        "brand": brand,
        "brand_type": brand_type,
        "brand_type_source": brand_type_source,
        "pack_units": pack_units,
        "unit_size": unit_size,
        "unit_type": unit_type,
        "total_size": total_size,
        "normalized_size": normalized_size,
        "normalized_unit_type": normalized_unit_type,
        "normalized_unit_price": normalized_unit_price,
        "product_core": product_core,
    }


# ============================================================
# PERSISTENCIA EN MYSQL (raw_scraped_products)
# ============================================================
#
# Requiere haber ejecutado antes, a mano:
#   migrations/001_add_normalized_columns.sql
#   migrations/002_add_brand_type_source.sql
# ============================================================

def _parse_raw_data(raw_data):
    """
    raw_data puede llegar como dict (mysql-connector suele decodificar
    JSON) o como str, según versión/config del conector. Normaliza a
    dict siempre; {} si no hay nada o no se puede decodificar.
    """

    if isinstance(raw_data, dict):
        return raw_data

    if isinstance(raw_data, str):
        try:
            return json.loads(raw_data)
        except ValueError:
            return {}

    return {}


def _normalization_columns_exist(cursor):
    """
    Comprueba que estan aplicadas tanto 001 (normalized_at y compañia)
    como 002 (brand_type_source), ya que apply_normalization_to_db
    escribe en ambas.
    """

    cursor.execute(
        """
        SELECT column_name AS column_name
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'raw_scraped_products'
          AND column_name IN ('normalized_at', 'brand_type_source');
        """
    )

    found_columns = {row["column_name"] for row in cursor.fetchall()}

    return {"normalized_at", "brand_type_source"} <= found_columns


def apply_normalization_to_db(apply=False, force=False):
    """
    Aplica normalize_product() a los productos de raw_scraped_products
    y persiste el resultado en brand, brand_type, brand_type_source,
    pack_units, unit_size, unit_type, normalized_size,
    normalized_unit_type, normalized_unit_price, product_core y
    normalized_at.

    apply=False (por defecto): modo dry-run. Calcula y muestra por
    pantalla lo que se actualizaría, pero no escribe nada en MySQL.
    apply=True: ejecuta los UPDATE y hace commit.

    force=False (por defecto): solo procesa productos con
    normalized_at IS NULL (para no repetir trabajo ya hecho).
    force=True: reprocesa todos los productos, tengan o no
    normalized_at ya relleno.
    """

    connection = get_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        if not _normalization_columns_exist(cursor):
            print(
                "[ERROR] La tabla raw_scraped_products no tiene todas "
                "las columnas de normalización todavía. Ejecuta antes "
                "migrations/001_add_normalized_columns.sql y "
                "migrations/002_add_brand_type_source.sql."
            )

            return

        if force:
            cursor.execute(
                """
                SELECT id, external_name, current_price, raw_data
                FROM raw_scraped_products
                ORDER BY id;
                """
            )
        else:
            cursor.execute(
                """
                SELECT id, external_name, current_price, raw_data
                FROM raw_scraped_products
                WHERE normalized_at IS NULL
                ORDER BY id;
                """
            )

        products = cursor.fetchall()

        print("=" * 100)
        print("APLICAR NORMALIZACIÓN A raw_scraped_products")
        print(
            "Modo:",
            "APPLY (escribe en la base de datos)"
            if apply
            else "DRY-RUN (no se escribe nada)"
        )
        print(
            "Alcance:",
            "todos los productos (--force)"
            if force
            else "solo normalized_at IS NULL"
        )
        print("Productos a procesar:", len(products))
        print("=" * 100)

        updated_count = 0

        for product in products:
            raw = _parse_raw_data(product.get("raw_data"))

            result = normalize_product(
                product["external_name"],
                product["current_price"],
                item_brand=raw.get("item_brand"),
                item_brand_type=raw.get("item_brand_type")
            )

            print(
                product["id"], "|",
                product["external_name"], "|",
                "brand=" + repr(result["brand"]), "|",
                "brand_type=" + repr(result["brand_type"]), "|",
                "brand_type_source="
                + repr(result["brand_type_source"]), "|",
                "pack_units=" + repr(result["pack_units"]), "|",
                "unit_size=" + repr(result["unit_size"]), "|",
                "unit_type=" + repr(result["unit_type"]), "|",
                "normalized_size=" + repr(result["normalized_size"]), "|",
                "normalized_unit_type="
                + repr(result["normalized_unit_type"]), "|",
                "normalized_unit_price="
                + repr(result["normalized_unit_price"]), "|",
                "product_core=" + repr(result["product_core"])
            )

            if apply:
                cursor.execute(
                    """
                    UPDATE raw_scraped_products
                    SET
                        brand = %s,
                        brand_type = %s,
                        brand_type_source = %s,
                        pack_units = %s,
                        unit_size = %s,
                        unit_type = %s,
                        normalized_size = %s,
                        normalized_unit_type = %s,
                        normalized_unit_price = %s,
                        product_core = %s,
                        normalized_at = NOW()
                    WHERE id = %s;
                    """,
                    (
                        result["brand"],
                        result["brand_type"],
                        result["brand_type_source"],
                        result["pack_units"],
                        result["unit_size"],
                        result["unit_type"],
                        result["normalized_size"],
                        result["normalized_unit_type"],
                        result["normalized_unit_price"],
                        result["product_core"],
                        product["id"]
                    )
                )

                updated_count += 1

        if apply:
            connection.commit()
        else:
            connection.rollback()

        print("-" * 100)

        if apply:
            print("Filas actualizadas:", updated_count)
        else:
            print(
                "DRY-RUN: no se ha escrito nada en la base de datos. "
                "Vuelve a ejecutar con --apply para persistir estos "
                "cambios."
            )

        print("=" * 100)

    except Exception:
        connection.rollback()
        raise

    finally:
        cursor.close()
        connection.close()


# ============================================================
# SCRIPT DE PRUEBA (SOLO LECTURA)
# ============================================================

def _fetch_raw_products():
    connection = get_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT id, external_name, current_price, raw_data
            FROM raw_scraped_products
            ORDER BY id;
            """
        )

        return cursor.fetchall()

    finally:
        cursor.close()
        connection.close()


def _truncate(text, width):
    if text is None:
        return ""

    text = str(text)

    if len(text) <= width:
        return text

    return text[:width - 1] + "…"


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    products = _fetch_raw_products()

    print("=" * 150)
    print("NORMALIZACIÓN DE PRODUCTOS (raw_scraped_products) - solo lectura")
    print("Total de productos:", len(products))
    print("=" * 150)

    header = (
        f"{'external_name':45} | {'brand':18} | {'brand_type':12} | "
        f"{'total_size':10} | {'unit_type':9} | {'norm_unit_price':15} | "
        f"{'product_core':30}"
    )
    print(header)
    print("-" * 150)

    missing_brand = []
    missing_size = []

    for product in products:
        raw = _parse_raw_data(product.get("raw_data"))

        result = normalize_product(
            product["external_name"],
            product["current_price"],
            item_brand=raw.get("item_brand"),
            item_brand_type=raw.get("item_brand_type")
        )

        if result["brand"] is None:
            missing_brand.append(product)

        if result["unit_size"] is None:
            missing_size.append(product)

        row = (
            f"{_truncate(product['external_name'], 45):45} | "
            f"{_truncate(result['brand'], 18):18} | "
            f"{_truncate(result['brand_type'], 12):12} | "
            f"{_truncate(result['total_size'], 10):10} | "
            f"{_truncate(result['unit_type'], 9):9} | "
            f"{_truncate(result['normalized_unit_price'], 15):15} | "
            f"{_truncate(result['product_core'], 30):30}"
        )
        print(row)

    print("-" * 150)
    print()
    print("=" * 150)
    print("RESUMEN")
    print("=" * 150)
    print(
        "Productos sin marca detectada:",
        len(missing_brand),
        "de",
        len(products)
    )
    print(
        "Productos sin tamaño detectado:",
        len(missing_size),
        "de",
        len(products)
    )

    if missing_brand:
        print()
        print("-- Casos sin marca detectada (revisar) --")

        for product in missing_brand:
            print(" -", product["id"], "|", product["external_name"])

    if missing_size:
        print()
        print("-- Casos sin tamaño detectado (revisar) --")

        for product in missing_size:
            print(" -", product["id"], "|", product["external_name"])

    print("=" * 150)


def _parse_cli_args(argv):
    parser = argparse.ArgumentParser(
        description=(
            "Normaliza raw_scraped_products (marca, tamaño, "
            "precio por L/kg)."
        )
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Ejecuta los UPDATE de verdad. Sin este flag, solo se "
            "muestra un dry-run (no se escribe nada)."
        )
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Reprocesa todos los productos, no solo los que tienen "
            "normalized_at NULL."
        )
    )

    parser.add_argument(
        "--report",
        action="store_true",
        help=(
            "Muestra la tabla de diagnóstico completa (marca/"
            "tamaño/precio_normalizado) sin tocar la base de datos, "
            "en vez de aplicar/dry-run la normalización."
        )
    )

    return parser.parse_args(argv)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    cli_args = _parse_cli_args(sys.argv[1:])

    if cli_args.report:
        main()
    else:
        apply_normalization_to_db(
            apply=cli_args.apply,
            force=cli_args.force
        )
