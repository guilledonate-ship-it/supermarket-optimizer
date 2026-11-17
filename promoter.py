"""
Promueve los grupos de find_exact_matches() (ver matcher.py) a las
tablas "de verdad": products, product_offers y prices.

Modo dry-run por defecto (no escribe nada en MySQL, solo calcula y
muestra lo que haría). Con --apply ejecuta los INSERT/UPDATE de
verdad, igual que normalizer.py.

No modifica matcher.py, normalizer.py, db_writer.py ni los scrapers.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from db import get_connection
from matcher import find_exact_matches


# ============================================================
# MAPEO category_name (raw_data) -> CATEGORÍA UNIFICADA
# ============================================================
#
# DIA y Eroski usan textos de categoría distintos para lo mismo
# (comprobado contra raw_data real de ambas cadenas). Claves en
# minúsculas - la búsqueda es case-insensitive.
#
# "Leche" y "Pan" ya existen en la tabla categories (id 1 y 3,
# creadas junto con los productos mock) - se reutilizan. "Agua",
# "Yogur" y "Plátanos" no existían y se crean la primera vez que
# hacen falta.
#
# Cualquier category_name que NO esté aquí no se deja en NULL en
# silencio: se registra como "categoría sin mapear" en el informe
# (ver _resolve_group_category) para poder ampliarlo a mano.
# ============================================================

CATEGORY_NAME_MAP = {
    "agua": "Agua",
    "leche": "Leche",
    "leche entera": "Leche",
    "leche semidesnatada": "Leche",
    "leche desnatada": "Leche",
    "leche, batidos y bebidas vegetales": "Leche",
    "pan de molde y especiales": "Pan",
    "pan de molde y tostado": "Pan",
    "yogures naturales y desnatados": "Yogur",
    "yogur natural": "Yogur",
    "platanos y bananas": "Plátanos",
    "platanos y kiwis": "Plátanos",
    # Ampliación de categorías (aceite/pasta/arroz/cafe/huevos/atun/
    # papel), añadidas tras el primer dry-run con las 7 categorías
    # nuevas: 9 avisos de "categoría sin mapear" (aceite, pasta,
    # arroz, atun, cafe) más papel/huevos, que no salieron en ese
    # aviso porque ninguno de los grupos nuevos pertenecía a esas dos
    # categorías (sin solape real de marca/tamaño entre dia/eroski
    # ahi todavia), pero se añaden igual para no repetir esto cuando
    # si aparezca un match.
    "aceites": "Aceite",
    "aceite de oliva suave": "Aceite",
    "aceite de oliva virgen": "Aceite",
    "aceite de oliva intenso": "Aceite",
    "macarrones, espaguetis y pastas secas": "Pasta",
    "macarrones y pasta corta": "Pasta",
    "espagueti, tallarines y pasta larga": "Pasta",
    "arroz": "Arroz",
    "cafe molido": "Café",
    "huevos": "Huevos",
    "atun y bonito": "Atún",
    "atun en aceite": "Atún",
    "bonito en aceite": "Atún",
    "atun y bonito natural": "Atún",
    "atun y bonito en escabeche": "Atún",
    "ventresca de bonito y atun": "Atún",
    "papel higienico, cocina y servilletas": "Papel higiénico",
    "papel higienico": "Papel higiénico",
    # Segunda ampliación (legumbres/harina/azucar/galletas/cereales/
    # quesos/jamon cocido/congelados de verdura/detergente/
    # mantequilla). Ninguna de las 10 categorías unificadas coincide
    # con las que ya existían en `categories` - las 10 se crean.
    "garbanzos y alubias": "Legumbres",
    "lentejas": "Legumbres",
    "legumbres": "Legumbres",
    "legumbres cocidas": "Legumbres",
    "harinas y levaduras": "Harina",
    "harina": "Harina",
    "levaduras y otros": "Harina",
    "azucar, miel y edulcorantes": "Azúcar",
    "azucar en paquete": "Azúcar",
    "azucar moreno": "Azúcar",
    "azucar especial": "Azúcar",
    "azucarillos": "Azúcar",
    "edulcorante y fructosa": "Azúcar",
    "azucar ecologico": "Azúcar",
    "galletas clasicas y digestive": "Galletas",
    "galletas de chocolate y rellenas": "Galletas",
    "galletas saladas y crackers": "Galletas",
    "galletas clasicas": "Galletas",
    "galletas snack, surtidas y pastas": "Galletas",
    "galletas fibra, integrales y ecologicas": "Galletas",
    "galletas rellenas y cubiertas": "Galletas",
    "galletas aperitivos": "Galletas",
    "cereales": "Cereales",
    "cereales integrales y muesli": "Cereales",
    "cornflakes": "Cereales",
    "cereales fibra, linea y muesli": "Cereales",
    "queso fresco": "Quesos",
    "queso curado": "Quesos",
    "queso semicurado": "Quesos",
    "queso tierno": "Quesos",
    "queso en lonchas": "Quesos",
    "queso azul y de cabra": "Quesos",
    "queso rallado": "Quesos",
    "queso untable y en porciones": "Quesos",
    "quesos internacionales": "Quesos",
    "queso curado y viejo": "Quesos",
    "queso lonchas y fundidos": "Quesos",
    "queso azul": "Quesos",
    "queso tarrina": "Quesos",
    "jamon cocido": "Jamón cocido",
    "verduras y patatas congeladas": "Congelados de verdura",
    "patatas, verduras y setas congeladas": "Congelados de verdura",
    "detergentes": "Detergente",
    "detergente maquina liquido": "Detergente",
    "detergente maquina polvo": "Detergente",
    "detergente maquina pastillas": "Detergente",
    "detergente prendas delicadas": "Detergente",
    "mantequilla y margarina": "Mantequilla",
    "mantequilla": "Mantequilla",
    "margarina": "Mantequilla",
}


# ============================================================
# COLUMNAS QUE NECESITA ESTE SCRIPT
# ============================================================
#
# Comprobado contra el esquema real antes de escribir promoter.py:
# todas estas columnas ya existen (products y product_offers vienen
# del esquema original, no de las migraciones 001/002, que solo
# tocan raw_scraped_products). Aun así, se comprueba en caliente cada
# vez por si el esquema cambia - ver check_required_columns().
# ============================================================

REQUIRED_COLUMNS = {
    "products": {
        "product_key", "name", "brand", "brand_type",
        "unit_size", "unit_type", "category_id"
    },
    "product_offers": {
        "product_id", "supermarket_id", "external_product_id",
        "external_name", "product_url", "image_url", "available"
    },
    "prices": {
        "product_offer_id", "price", "price_per_unit", "currency",
        "data_source"
    },
    "categories": {"name", "slug"},
}

DEFAULT_SLUGS = ("dia", "eroski")


# ============================================================
# COMPROBACIÓN DE ESQUEMA Y PREPARACIÓN DE MIGRACIÓN (SIN EJECUTAR)
# ============================================================

def check_required_columns(cursor):
    """
    Comprueba que todas las columnas de REQUIRED_COLUMNS existen.

    Devuelve (ok, missing): ok=True si no falta nada. missing es un
    dict {tabla: [columnas faltantes]} (vacío si ok=True).
    """

    missing = {}

    for table, required in REQUIRED_COLUMNS.items():
        cursor.execute(
            """
            SELECT column_name AS column_name
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = %s;
            """,
            (table,)
        )

        existing = {row["column_name"] for row in cursor.fetchall()}
        missing_columns = required - existing

        if missing_columns:
            missing[table] = sorted(missing_columns)

    return not missing, missing


# Tipo de columna a usar en el ALTER TABLE si algún día falta alguna
# de REQUIRED_COLUMNS. Solo se usa para preparar la migración: hoy no
# falta ninguna, así que este mapa no se ejecuta contra la base de
# datos, solo sirve para generar el SQL si hiciera falta en el futuro.
_COLUMN_DDL = {
    "product_key": "VARCHAR(191) NOT NULL UNIQUE",
    "name": "VARCHAR(255) NOT NULL",
    "brand": "VARCHAR(100) NULL",
    "brand_type": "VARCHAR(20) NULL",
    "unit_size": "DECIMAL(10,3) NULL",
    "unit_type": "VARCHAR(10) NULL",
    "product_id": "INT NOT NULL",
    "supermarket_id": "INT NOT NULL",
    "external_product_id": "VARCHAR(100) NULL",
    "external_name": "VARCHAR(255) NULL",
    "product_url": "TEXT NULL",
    "image_url": "TEXT NULL",
    "available": "TINYINT(1) NULL DEFAULT 1",
    "product_offer_id": "INT NOT NULL",
    "price": "DECIMAL(10,2) NOT NULL",
    "price_per_unit": "DECIMAL(10,2) NULL",
    "currency": "VARCHAR(3) NULL DEFAULT 'EUR'",
    "data_source": "VARCHAR(20) NULL DEFAULT 'manual'",
    "category_id": "INT NULL",
    "slug": "VARCHAR(191) NOT NULL UNIQUE",
}


def _next_migration_path():
    migrations_dir = Path(__file__).resolve().parent / "migrations"
    migrations_dir.mkdir(exist_ok=True)

    existing_numbers = []

    for path in migrations_dir.glob("*.sql"):
        match = re.match(r"^(\d+)_", path.name)

        if match:
            existing_numbers.append(int(match.group(1)))

    next_number = max(existing_numbers, default=0) + 1

    return migrations_dir / f"{next_number:03d}_add_promoter_columns.sql"


def prepare_missing_columns_migration(missing):
    """
    Escribe (sin ejecutar) un archivo de migración con los
    ALTER TABLE ... ADD COLUMN IF NOT EXISTS necesarios para las
    columnas de `missing`. Devuelve la ruta del archivo escrito.
    """

    lines = [
        "-- Generado automáticamente por promoter.py",
        "-- (check_required_columns detectó columnas faltantes).",
        "-- Revisar antes de ejecutar - no se ha aplicado sola.",
        ""
    ]

    for table, columns in missing.items():
        for column in columns:
            ddl = _COLUMN_DDL.get(column, "VARCHAR(255) NULL")

            lines.append(
                f"ALTER TABLE {table}\n"
                f"    ADD COLUMN IF NOT EXISTS {column} {ddl};"
            )
            lines.append("")

    migration_path = _next_migration_path()
    migration_path.write_text("\n".join(lines), encoding="utf-8")

    return migration_path


# ============================================================
# GENERACIÓN DE product_key / name
# ============================================================

def _slugify(text):
    if not text:
        return ""

    normalized = text.lower()

    replacements = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
        "ü": "u", "ñ": "n"
    }

    for original, replacement in replacements.items():
        normalized = normalized.replace(original, replacement)

    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")

    return normalized


def _format_size_token(normalized_size):
    return f"{float(normalized_size):g}"


def build_product_key(brand, product_core_clean, normalized_size, unit_type):
    """
    Clave estable y determinista: mismo brand + product_core +
    normalized_size + unit_type siempre generan la misma clave, para
    que reejecutar promote_matches() no duplique productos.

    IMPORTANTE: cada segmento (brand, product_core, tamaño+unidad) se
    slugifica POR SEPARADO y se unen con "--", que _slugify nunca
    puede producir (solo genera [a-z0-9_]). Comprobado que juntarlo
    todo en un único string antes de slugificar (como se hacía antes)
    puede colisionar: product_core="bifidus doble 0" + size=5L daba
    la misma clave que product_core="bifidus doble" + size=0.5L
    (ambos "..._0_5l"), porque el "0" final del core y el "5" del
    tamaño se fusionaban en un solo "_0_5" indistinguible de un
    tamaño real de 0,5. Con "--" como separador reservado, el límite
    entre segmentos nunca se pierde, pase lo que pase dentro de cada
    uno.
    """

    size_token = _format_size_token(normalized_size)

    brand_slug = _slugify(brand)
    core_slug = _slugify(product_core_clean)
    size_slug = _slugify(f"{size_token}{unit_type}")

    return f"{brand_slug}--{core_slug}--{size_slug}"


def build_product_name(brand, product_core_clean, normalized_size, unit_type):
    size_token = _format_size_token(normalized_size)

    core_label = product_core_clean[:1].upper() + product_core_clean[1:]
    brand_label = brand.title()

    return f"{core_label} {brand_label} {size_token}{unit_type}"


# ============================================================
# CARGA DESDE MYSQL (SOLO LECTURA)
# ============================================================

def fetch_products_for_promotion(slugs=DEFAULT_SLUGS):
    """
    Igual que matcher.fetch_normalized_products, pero con las
    columnas extra que necesita promoter.py: product_url/image_url
    para product_offers, y raw_data para leer category_name (ver
    resolve_category_id) - todo sin tocar matcher.py.
    """

    connection = get_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        placeholders = ",".join(["%s"] * len(slugs))

        cursor.execute(
            f"""
            SELECT
                s.id AS supermarket_id,
                s.slug AS supermarket_slug,
                r.id,
                r.external_product_id,
                r.external_name,
                r.product_url,
                r.image_url,
                r.current_price,
                r.brand,
                r.brand_type,
                r.normalized_size,
                r.normalized_unit_type,
                r.normalized_unit_price,
                r.product_core,
                r.raw_data
            FROM raw_scraped_products r
            JOIN supermarkets s ON s.id = r.supermarket_id
            WHERE s.slug IN ({placeholders})
            ORDER BY r.brand, r.normalized_size, s.slug;
            """,
            slugs
        )

        return cursor.fetchall()

    finally:
        cursor.close()
        connection.close()


# ============================================================
# PROTECCIÓN DE DATOS 'mock'
# ============================================================

def _product_has_mock_prices(cursor, product_id):
    """
    True si el producto ya tiene algún precio con data_source='mock'
    colgando de alguno de sus product_offers. Si es así, promote_matches
    no lo toca (ni products ni sus offers/prices) aunque su
    product_key generado coincida por casualidad.
    """

    cursor.execute(
        """
        SELECT 1
        FROM prices pr
        JOIN product_offers po ON po.id = pr.product_offer_id
        WHERE po.product_id = %s
          AND pr.data_source = 'mock'
        LIMIT 1;
        """,
        (product_id,)
    )

    return cursor.fetchone() is not None


# ============================================================
# RESOLUCIÓN DE CATEGORÍA (raw_data.category_name -> categories.id)
# ============================================================

def _parse_raw_data(raw_data):
    """
    raw_data puede llegar como dict o como str según el conector -
    misma situación que en normalizer.py, copia local pequeña en vez
    de importar su función privada.
    """

    if isinstance(raw_data, dict):
        return raw_data

    if isinstance(raw_data, str):
        try:
            return json.loads(raw_data)
        except ValueError:
            return {}

    return {}


def get_or_create_category(cursor, apply, canonical_name, categories_cache):
    """
    Busca en categories por slug (derivado de canonical_name). Si no
    existe, la crea (o la planea crear en dry-run).

    categories_cache: dict {canonical_name: category_id} que vive
    durante todo el run de promote_matches(), para no repetir el
    SELECT/INSERT una vez por grupo cuando varios grupos comparten
    categoría.

    Devuelve (category_id, action): category_id es None en dry-run
    cuando la categoría todavía no existe (no hay id real hasta que
    se inserte de verdad, mismo criterio que _resolve_product).
    """

    if canonical_name in categories_cache:
        return categories_cache[canonical_name], "cached"

    slug = _slugify(canonical_name)

    cursor.execute(
        "SELECT id FROM categories WHERE slug = %s;",
        (slug,)
    )

    existing = cursor.fetchone()

    if existing:
        category_id = existing["id"]
        categories_cache[canonical_name] = category_id

        return category_id, "reuse"

    if apply:
        cursor.execute(
            "INSERT INTO categories (name, slug) VALUES (%s, %s);",
            (canonical_name, slug)
        )

        category_id = cursor.lastrowid
    else:
        category_id = None

    categories_cache[canonical_name] = category_id

    return category_id, "create"


def resolve_category_id(cursor, apply, group, categories_cache, unmapped_categories):
    """
    Resuelve el category_id de un grupo a partir de los
    category_name que traen sus productos en raw_data.

    Cualquier category_name que no esté en CATEGORY_NAME_MAP se
    añade a `unmapped_categories` (con su texto original, para que se
    vea tal cual llegó) y se ignora - NUNCA se deja una categoría sin
    mapear pasar en silencio a NULL.

    Si dentro del mismo grupo hay category_name que mapean a más de
    una categoría unificada distinta (no debería pasar - comprobado
    contra los 24 grupos reales, siempre coinciden - pero se avisa
    igualmente si algún día pasa), se usa la primera y se imprime un
    aviso.

    Devuelve (category_id, category_created) - category_created=True
    si esta llamada ha creado (o planeado crear) una categoría nueva.
    """

    raw_names = set()

    for product in group["products"]:
        raw = _parse_raw_data(product.get("raw_data"))
        category_name = raw.get("category_name")

        if category_name:
            raw_names.add(category_name)

    canonical_names = []

    for raw_name in raw_names:
        canonical = CATEGORY_NAME_MAP.get(raw_name.strip().lower())

        if canonical is None:
            unmapped_categories.add(raw_name)
        elif canonical not in canonical_names:
            canonical_names.append(canonical)

    if not canonical_names:
        return None, False

    if len(canonical_names) > 1:
        print(
            "[AVISO] el grupo '" + group["product_core_clean"] + "' "
            "(" + group["brand"] + ") tiene category_name que mapean a "
            "categorías distintas: " + ", ".join(canonical_names) +
            " - se usa '" + canonical_names[0] + "'."
        )

    category_id, action = get_or_create_category(
        cursor, apply, canonical_names[0], categories_cache
    )

    return category_id, action == "create"


# ============================================================
# RECONCILIACIÓN POR SKU (product_key inestable ante cambios en
# clean_product_core)
# ============================================================
#
# product_key se genera a partir de product_core_clean (ver
# build_product_key), que es un valor DE COMPARACIÓN: cambia cada vez
# que se afina PACKAGING_WORDS, la singularización u otra regla de
# clean_product_core en matcher.py. Si solo se busca por product_key
# exacto, un cambio así hace que el mismo producto real genere una
# clave nueva -> INSERT en vez de UPDATE -> la fila vieja se queda
# huérfana sin que nadie se entere (nos pasó de verdad con "Findus
# verduras/guisantes para sofrito" al añadir la singularización).
#
# Antes de dar un grupo por "no existe, hay que crearlo", se busca si
# alguno de sus SKUs concretos (supermarket_id + external_product_id,
# no el texto derivado) ya está enganchado a un product_offers de un
# product_id existente. Si hay exactamente uno, es el mismo producto
# con la clave cambiada: se renombra esa fila en vez de crear otra.
#
# Casos ambiguos (un grupo cuyos SKUs apuntan a MÁS DE UN producto
# existente distinto = fusión; o un producto existente cuyos SKUs
# ahora reparten ENTRE VARIOS grupos nuevos = división) NO se deciden
# aquí - se reportan y esas filas no se tocan, tal como se pidió.
# ============================================================

def _find_candidate_product_ids(cursor, group):
    """
    Busca, para los SKUs concretos usados por este grupo
    (cheapest_by_slug - los que de verdad se van a escribir en
    product_offers), si ya existe algún product_offers apuntando a un
    product_id via (supermarket_id, external_product_id) real, no via
    product_key derivado de texto.

    Devuelve un set de product_id distintos encontrados (puede tener
    0, 1 o más de uno).
    """

    candidate_ids = set()

    for entry in group["cheapest_by_slug"].values():
        sku = entry["product"]
        external_id = sku.get("external_product_id")
        supermarket_id = sku.get("supermarket_id")

        if not external_id or not supermarket_id:
            continue

        cursor.execute(
            """
            SELECT DISTINCT product_id
            FROM product_offers
            WHERE supermarket_id = %s AND external_product_id = %s;
            """,
            (supermarket_id, external_id)
        )

        for row in cursor.fetchall():
            candidate_ids.add(row["product_id"])

    return candidate_ids


def _plan_reconciliation(cursor, match_groups, product_keys_by_group):
    """
    Fase de solo lectura (antes de escribir nada): para cada grupo
    cuyo product_key NO existe ya tal cual en products, busca
    candidatos por SKU (_find_candidate_product_ids) y detecta
    ambigüedades cruzando todos los grupos entre sí.

    Devuelve (reconcile_by_key, ambiguous_by_key, ambiguous_report):
      reconcile_by_key: {product_key: product_id} - candidato único,
        sin ambigüedad, para renombrar en vez de crear.
      ambiguous_by_key: set de product_key que no se deben tocar esta
        pasada (ni crear, ni actualizar, ni renombrar).
      ambiguous_report: lista de dicts describiendo cada caso, para
        el informe.
    """

    candidates_by_key = {}

    for group in match_groups:
        product_key = product_keys_by_group[id(group)]

        cursor.execute(
            "SELECT id FROM products WHERE product_key = %s;",
            (product_key,)
        )

        if cursor.fetchone():
            # Ya existe con esta clave exacta - camino normal de
            # _resolve_product, no hace falta reconciliar por SKU.
            continue

        candidates_by_key[product_key] = _find_candidate_product_ids(
            cursor, group
        )

    reverse_map = defaultdict(list)

    for product_key, candidate_ids in candidates_by_key.items():
        for product_id in candidate_ids:
            reverse_map[product_id].append(product_key)

    ambiguous_by_key = set()
    ambiguous_report = []

    for product_key, candidate_ids in candidates_by_key.items():
        if len(candidate_ids) > 1:
            ambiguous_by_key.add(product_key)

            cursor.execute(
                "SELECT id, product_key FROM products WHERE id IN ("
                + ",".join(["%s"] * len(candidate_ids)) + ");",
                tuple(candidate_ids)
            )
            existing_rows = cursor.fetchall()

            ambiguous_report.append({
                "type": "fusion",
                "new_product_key": product_key,
                "candidate_products": [
                    {"id": r["id"], "product_key": r["product_key"]}
                    for r in existing_rows
                ]
            })

    for product_id, product_keys in reverse_map.items():
        if len(product_keys) > 1:
            for product_key in product_keys:
                ambiguous_by_key.add(product_key)

            cursor.execute(
                "SELECT product_key FROM products WHERE id = %s;",
                (product_id,)
            )
            old_row = cursor.fetchone()

            ambiguous_report.append({
                "type": "division",
                "old_product_id": product_id,
                "old_product_key": old_row["product_key"] if old_row else None,
                "new_product_keys": sorted(product_keys)
            })

    reconcile_by_key = {
        product_key: next(iter(candidate_ids))
        for product_key, candidate_ids in candidates_by_key.items()
        if len(candidate_ids) == 1 and product_key not in ambiguous_by_key
    }

    return reconcile_by_key, ambiguous_by_key, ambiguous_report


# ============================================================
# PRODUCTOS PROMOVIDOS NO TOCADOS EN ESTA PASADA (SOLO INFORME)
# ============================================================
#
# No se marca nada inactivo automáticamente a propósito: un producto
# puede no tocarse por motivos legítimos (un scraper que falla en una
# categoría ese día, un SKU sin stock puntual) - desactivarlo solo
# porque no apareció hoy convertiría un fallo puntual en pérdida de
# catálogo. Se lista para que se decida a mano.
# ============================================================

def _find_stale_products(cursor, touched_product_ids):
    cursor.execute(
        "SELECT id, product_key FROM products WHERE product_key LIKE '%--%';"
    )

    stale = []

    for row in cursor.fetchall():
        if row["id"] in touched_product_ids:
            continue

        cursor.execute(
            """
            SELECT MAX(last_seen_at) AS last_seen
            FROM product_offers
            WHERE product_id = %s;
            """,
            (row["id"],)
        )

        last_seen_row = cursor.fetchone()
        last_seen = last_seen_row["last_seen"] if last_seen_row else None

        days_since_update = (
            (datetime.now() - last_seen).days
            if last_seen is not None
            else None
        )

        stale.append({
            "product_id": row["id"],
            "product_key": row["product_key"],
            "days_since_update": days_since_update
        })

    stale.sort(
        key=lambda p: (
            p["days_since_update"] is None,
            -(p["days_since_update"] or 0)
        )
    )

    return stale


# ============================================================
# PROMOCIÓN DE UN GRUPO (CREATE-OR-UPDATE-OR-RENAME)
# ============================================================

def _resolve_product(cursor, apply, product_key, fields, reconcile_product_id=None):
    """
    Busca products por product_key. Si existe y no está protegido
    por datos 'mock', se actualiza (o se planea actualizar).

    Si NO existe con esa clave exacta pero `reconcile_product_id` no
    es None (ver _plan_reconciliation - mismo producto real, SKUs ya
    conocidos, solo cambió el texto derivado), se renombra esa fila
    en vez de crear una nueva.

    Si tampoco hay reconciliación posible, se crea (o se planea
    crear).

    Devuelve un dict:
      {"action": "create"|"update"|"renamed"|"skipped_mock_conflict",
       "product_id": int|None, "fields": fields,
       "old_product_key": str (solo si action="renamed")}
    product_id es None en dry-run cuando la acción es "create" (no
    hay id real hasta que se inserte de verdad).
    """

    cursor.execute(
        "SELECT id FROM products WHERE product_key = %s;",
        (product_key,)
    )

    existing = cursor.fetchone()

    if existing:
        product_id = existing["id"]

        if _product_has_mock_prices(cursor, product_id):
            return {
                "action": "skipped_mock_conflict",
                "product_id": product_id,
                "fields": fields
            }

        if apply:
            cursor.execute(
                """
                UPDATE products
                SET name = %s, brand = %s, brand_type = %s,
                    unit_size = %s, unit_type = %s,
                    category_id = COALESCE(%s, category_id)
                WHERE id = %s;
                """,
                (
                    fields["name"], fields["brand"], fields["brand_type"],
                    fields["unit_size"], fields["unit_type"],
                    fields["category_id"], product_id
                )
            )

        return {
            "action": "update",
            "product_id": product_id,
            "fields": fields
        }

    if reconcile_product_id is not None:
        if _product_has_mock_prices(cursor, reconcile_product_id):
            return {
                "action": "skipped_mock_conflict",
                "product_id": reconcile_product_id,
                "fields": fields
            }

        cursor.execute(
            "SELECT product_key FROM products WHERE id = %s;",
            (reconcile_product_id,)
        )
        old_row = cursor.fetchone()
        old_product_key = old_row["product_key"] if old_row else None

        if apply:
            cursor.execute(
                """
                UPDATE products
                SET product_key = %s, name = %s, brand = %s,
                    brand_type = %s, unit_size = %s, unit_type = %s,
                    category_id = COALESCE(%s, category_id)
                WHERE id = %s;
                """,
                (
                    product_key, fields["name"], fields["brand"],
                    fields["brand_type"], fields["unit_size"],
                    fields["unit_type"], fields["category_id"],
                    reconcile_product_id
                )
            )

        return {
            "action": "renamed",
            "product_id": reconcile_product_id,
            "fields": fields,
            "old_product_key": old_product_key
        }

    if apply:
        cursor.execute(
            """
            INSERT INTO products
                (product_key, name, brand, brand_type, unit_size,
                 unit_type, category_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s);
            """,
            (
                product_key, fields["name"], fields["brand"],
                fields["brand_type"], fields["unit_size"],
                fields["unit_type"], fields["category_id"]
            )
        )

        product_id = cursor.lastrowid
    else:
        product_id = None

    return {
        "action": "create",
        "product_id": product_id,
        "fields": fields
    }


def _resolve_offer(cursor, apply, product_id, supermarket_id, fields):
    """
    Busca product_offers por (product_id, supermarket_id). product_id
    puede ser None en dry-run (producto todavía no creado) - en ese
    caso la oferta solo puede ser "create" planeada, sin consultar.
    """

    if product_id is None:
        return {"action": "create", "offer_id": None, "fields": fields}

    cursor.execute(
        """
        SELECT id FROM product_offers
        WHERE product_id = %s AND supermarket_id = %s;
        """,
        (product_id, supermarket_id)
    )

    existing = cursor.fetchone()

    if existing:
        offer_id = existing["id"]

        if apply:
            cursor.execute(
                """
                UPDATE product_offers
                SET external_product_id = %s, external_name = %s,
                    product_url = %s, image_url = %s, available = 1,
                    last_seen_at = NOW()
                WHERE id = %s;
                """,
                (
                    fields["external_product_id"], fields["external_name"],
                    fields["product_url"], fields["image_url"], offer_id
                )
            )

        return {"action": "update", "offer_id": offer_id, "fields": fields}

    if apply:
        cursor.execute(
            """
            INSERT INTO product_offers
                (product_id, supermarket_id, external_product_id,
                 external_name, product_url, image_url, available,
                 last_seen_at)
            VALUES (%s, %s, %s, %s, %s, %s, 1, NOW());
            """,
            (
                product_id, supermarket_id, fields["external_product_id"],
                fields["external_name"], fields["product_url"],
                fields["image_url"]
            )
        )

        offer_id = cursor.lastrowid
    else:
        offer_id = None

    return {"action": "create", "offer_id": offer_id, "fields": fields}


def _price_already_exists_today(cursor, offer_id, price):
    """
    True si ya hay un precio idéntico (mismo product_offer_id, mismo
    price) con scraped_at de hoy. prices es un histórico y no se
    actualiza, pero no debe acumular dos filas idénticas de la misma
    ejecución (o de reejecutar promote_matches el mismo día sin que
    el precio real haya cambiado).
    """

    cursor.execute(
        """
        SELECT 1
        FROM prices
        WHERE product_offer_id = %s
          AND price = %s
          AND DATE(scraped_at) = CURDATE()
        LIMIT 1;
        """,
        (offer_id, price)
    )

    return cursor.fetchone() is not None


def _insert_price(cursor, apply, offer_id, fields):
    """
    Inserta en prices salvo que ya exista un precio idéntico para el
    mismo product_offer_id en el día de hoy (ver
    _price_already_exists_today) - en ese caso no inserta nada y
    devuelve action="skipped_duplicate".

    offer_id puede ser None en dry-run (oferta todavía no creada):
    no hay nada que consultar todavía, así que no puede haber
    duplicado - se planea "create" sin más.
    """

    if offer_id is None:
        return {"action": "create", "fields": fields}

    if _price_already_exists_today(cursor, offer_id, fields["price"]):
        return {"action": "skipped_duplicate", "fields": fields}

    if apply:
        cursor.execute(
            """
            INSERT INTO prices
                (product_offer_id, price, price_per_unit, currency,
                 data_source)
            VALUES (%s, %s, %s, 'EUR', 'scraped');
            """,
            (offer_id, fields["price"], fields["price_per_unit"])
        )

    return {"action": "create", "fields": fields}


# ============================================================
# FUNCIÓN PRINCIPAL
# ============================================================

def promote_matches(products, apply=False, slugs=DEFAULT_SLUGS):
    """
    Para cada grupo de find_exact_matches(products):
      - crea/actualiza products (product_key determinista)
      - crea/actualiza product_offers por cada supermercado del
        grupo, usando el SKU más barato de esa cadena
        (cheapest_by_slug, ver matcher.py)
      - inserta una fila en prices (data_source='scraped'), salvo que
        ya exista un precio idéntico para ese product_offer_id en el
        día de hoy (ver _price_already_exists_today)

    apply=False (por defecto): no ejecuta ningún INSERT/UPDATE, solo
    calcula y devuelve/imprime lo que haría.
    apply=True: ejecuta de verdad y hace commit.

    Nunca toca products/product_offers/prices que ya tengan precios
    con data_source='mock' (ver _product_has_mock_prices).
    """

    connection = get_connection()
    cursor = connection.cursor(dictionary=True)

    summary = {
        "products_created": 0,
        "products_updated": 0,
        "products_renamed": 0,
        "offers_created": 0,
        "offers_updated": 0,
        "prices_created": 0,
        "prices_skipped_duplicate": 0,
        "categories_created": 0,
        "unmapped_categories": set(),
        "groups_skipped_mock_conflict": 0,
        "ambiguous_reconciliations": [],
        "stale_products": [],
        "examples": {
            "product": None, "offer": None, "price": None, "renamed": None
        }
    }

    categories_cache = {}

    try:
        ok, missing = check_required_columns(cursor)

        if not ok:
            migration_path = prepare_missing_columns_migration(missing)

            print("[ERROR] Faltan columnas necesarias:")

            for table, columns in missing.items():
                print(f"  - {table}: {', '.join(columns)}")

            print(
                "Migración preparada (sin ejecutar):",
                migration_path
            )
            print("Revísala y ejecútala antes de volver a intentarlo.")

            return summary

        match_groups, _ = find_exact_matches(products)

        product_keys_by_group = {
            id(group): build_product_key(
                group["brand"],
                group["product_core_clean"],
                group["normalized_size"],
                group["normalized_unit_type"]
            )
            for group in match_groups
        }

        reconcile_by_key, ambiguous_by_key, ambiguous_report = (
            _plan_reconciliation(cursor, match_groups, product_keys_by_group)
        )

        summary["ambiguous_reconciliations"] = ambiguous_report

        touched_product_ids = set()

        for group in match_groups:
            product_key = product_keys_by_group[id(group)]

            if product_key in ambiguous_by_key:
                print(
                    "[AVISO] '" + product_key + "' tiene una "
                    "reconciliación por SKU ambigua (fusión o división "
                    "- ver informe) - grupo omitido, no se toca."
                )

                continue

            category_id, category_created = resolve_category_id(
                cursor, apply, group, categories_cache,
                summary["unmapped_categories"]
            )

            if category_created:
                summary["categories_created"] += 1

            product_fields = {
                "name": build_product_name(
                    group["brand"],
                    group["product_core_clean"],
                    group["normalized_size"],
                    group["normalized_unit_type"]
                ),
                "brand": group["brand"],
                "brand_type": group["brand_type"],
                "unit_size": group["normalized_size"],
                "unit_type": group["normalized_unit_type"],
                "category_id": category_id
            }

            product_result = _resolve_product(
                cursor, apply, product_key, product_fields,
                reconcile_product_id=reconcile_by_key.get(product_key)
            )

            if product_result["action"] == "skipped_mock_conflict":
                summary["groups_skipped_mock_conflict"] += 1

                print(
                    "[AVISO] product_key '" + product_key + "' ya existe "
                    "y tiene precios data_source='mock' - grupo omitido "
                    "por completo, no se toca."
                )

                continue

            if product_result["product_id"] is not None:
                touched_product_ids.add(product_result["product_id"])

            if product_result["action"] == "create":
                summary["products_created"] += 1
            elif product_result["action"] == "renamed":
                summary["products_renamed"] += 1

                print(
                    "[RENOMBRADO] '" + product_result["old_product_key"] +
                    "' -> '" + product_key + "' (mismos SKUs, cambió el "
                    "texto derivado de product_core)"
                )

                if summary["examples"]["renamed"] is None:
                    summary["examples"]["renamed"] = {
                        "old_product_key": product_result["old_product_key"],
                        "new_product_key": product_key
                    }
            else:
                summary["products_updated"] += 1

            if summary["examples"]["product"] is None:
                summary["examples"]["product"] = {
                    "product_key": product_key,
                    "action": product_result["action"],
                    **product_fields
                }

            for slug, entry in group["cheapest_by_slug"].items():
                sku = entry["product"]

                offer_fields = {
                    "external_product_id": sku.get("external_product_id"),
                    "external_name": sku.get("external_name"),
                    "product_url": sku.get("product_url"),
                    "image_url": sku.get("image_url")
                }

                offer_result = _resolve_offer(
                    cursor,
                    apply,
                    product_result["product_id"],
                    sku["supermarket_id"],
                    offer_fields
                )

                if offer_result["action"] == "create":
                    summary["offers_created"] += 1
                else:
                    summary["offers_updated"] += 1

                if summary["examples"]["offer"] is None:
                    summary["examples"]["offer"] = {
                        "product_key": product_key,
                        "supermarket_slug": slug,
                        "action": offer_result["action"],
                        **offer_fields
                    }

                price_fields = {
                    "price": sku["current_price"],
                    "price_per_unit": sku.get("normalized_unit_price")
                }

                price_result = _insert_price(
                    cursor, apply, offer_result["offer_id"], price_fields
                )

                if price_result["action"] == "skipped_duplicate":
                    summary["prices_skipped_duplicate"] += 1
                else:
                    summary["prices_created"] += 1

                    if summary["examples"]["price"] is None:
                        summary["examples"]["price"] = {
                            "product_key": product_key,
                            "supermarket_slug": slug,
                            "sku_name": sku.get("external_name"),
                            "data_source": "scraped",
                            **price_fields
                        }

        summary["stale_products"] = _find_stale_products(
            cursor, touched_product_ids
        )

        if apply:
            connection.commit()
        else:
            connection.rollback()

        return summary

    except Exception:
        connection.rollback()
        raise

    finally:
        cursor.close()
        connection.close()


# ============================================================
# INFORME
# ============================================================

def print_promotion_report(summary, apply):
    print("=" * 100)
    print("PROMOCIÓN DE MATCHES A products / product_offers / prices")
    print("Modo:", "APPLY (escribe de verdad)" if apply else "DRY-RUN")
    print("=" * 100)

    print(
        "products  -> nuevos:", summary["products_created"],
        "| actualizados:", summary["products_updated"],
        "| renombrados (reconciliados por SKU):", summary["products_renamed"]
    )
    print(
        "offers    -> nuevos:", summary["offers_created"],
        "| actualizados:", summary["offers_updated"]
    )
    print(
        "prices    -> insertados:", summary["prices_created"],
        "| omitidos por duplicado (mismo precio, misma fecha):",
        summary["prices_skipped_duplicate"]
    )
    print("categories -> nuevas:", summary["categories_created"])
    print(
        "grupos omitidos por conflicto con datos mock:",
        summary["groups_skipped_mock_conflict"]
    )

    if summary["unmapped_categories"]:
        print("-" * 100)
        print(
            "[AVISO] categorías sin mapear encontradas en raw_data "
            "(category_id quedó a NULL para esos productos, añádelas "
            "a CATEGORY_NAME_MAP):"
        )

        for raw_name in sorted(summary["unmapped_categories"]):
            print(f"  - categoría sin mapear: {raw_name!r}")

    if summary["ambiguous_reconciliations"]:
        print("-" * 100)
        print(
            "[AVISO] reconciliaciones por SKU ambiguas - NO se han "
            "tocado, decide a mano:"
        )

        for case in summary["ambiguous_reconciliations"]:
            if case["type"] == "fusion":
                print(
                    "  - FUSIÓN: el grupo nuevo '" +
                    case["new_product_key"] + "' tiene SKUs repartidos "
                    "entre " + str(len(case["candidate_products"])) +
                    " productos existentes distintos:"
                )

                for candidate in case["candidate_products"]:
                    print(
                        "      · id", candidate["id"], "|",
                        candidate["product_key"]
                    )
            else:
                print(
                    "  - DIVISIÓN: el producto existente '" +
                    str(case["old_product_key"]) + "' (id " +
                    str(case["old_product_id"]) + ") tiene SKUs "
                    "repartidos ahora entre " +
                    str(len(case["new_product_keys"])) +
                    " grupos nuevos distintos:"
                )

                for new_key in case["new_product_keys"]:
                    print("      ·", new_key)

    if summary["stale_products"]:
        print("-" * 100)
        print(
            "Productos promovidos NO tocados en esta pasada",
            "(no se marcan inactivos automáticamente - revisa si es",
            "un fallo puntual o hay que darlos de baja a mano):"
        )

        for stale in summary["stale_products"]:
            days = stale["days_since_update"]
            days_label = f"{days} días" if days is not None else "sin ofertas"

            print(
                f"  - {stale['product_key']:50} | sin tocar desde hace",
                days_label
            )

    print("-" * 100)
    print("Ejemplo de products:")
    print(" ", summary["examples"]["product"])
    print("Ejemplo de product_offers:")
    print(" ", summary["examples"]["offer"])
    print("Ejemplo de prices:")
    print(" ", summary["examples"]["price"])

    if summary["examples"]["renamed"]:
        print("Ejemplo de renombrado:")
        print(" ", summary["examples"]["renamed"])

    print("=" * 100)

    if not apply:
        print(
            "DRY-RUN: no se ha escrito nada en la base de datos. "
            "Vuelve a ejecutar con --apply para persistir estos cambios."
        )
        print("=" * 100)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description=(
            "Promueve los grupos de matcher.find_exact_matches() a "
            "products/product_offers/prices."
        )
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Ejecuta los INSERT/UPDATE de verdad. Sin este flag, solo "
            "se muestra un dry-run (no se escribe nada)."
        )
    )

    args = parser.parse_args()

    products = fetch_products_for_promotion(DEFAULT_SLUGS)
    summary = promote_matches(products, apply=args.apply)

    print_promotion_report(summary, args.apply)


if __name__ == "__main__":
    main()
