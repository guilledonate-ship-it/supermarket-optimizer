"""
Matching de productos entre supermercados, a partir de
raw_scraped_products ya normalizado (brand, brand_type,
normalized_size, normalized_unit_type, normalized_unit_price,
product_core - ver normalizer.py).

Solo lectura: no escribe nada en la base de datos ni modifica
normalizer.py/db_writer.py/los scrapers. find_exact_matches() y
find_format_alternatives() son puras (no acceden a MySQL); reciben la
lista de productos ya cargada.
"""

import re
import sys
from collections import defaultdict

from db import get_connection


# ============================================================
# PALABRAS DE ENVASE A IGNORAR AL COMPARAR product_core
# ============================================================
#
# normalizer.py ya limpia pack/botella/granel/aprox de product_core
# (ver _FORMAT_WORDS_REGEX), pero deja pasar otras palabras de envase
# (brik, tarrina, paquete, botellín...) que hacen que el mismo
# producto vendido por dos cadenas no coincida literalmente. Esta
# lista es solo para comparar aquí en matcher.py - nunca se persiste
# ni se toca normalizer.py.
#
# Comprobado contra dia+eroski: "brik" es el caso más frecuente (8 de
# 11 casi-coincidencias), pero no es un caso aislado - de ahí la
# lista centralizada en vez de un strip puntual, para que crezca con
# cada categoría nueva sin tener que tocar la lógica de comparación.
#
# IMPORTANTE: no incluir aquí palabras que describan el producto en
# sí (azucarado, natural, desnatada, integral, bio, sin gluten...) -
# eso cambiaría el significado del producto, no solo el envase, y
# fusionaría productos que en realidad son distintos (comprobado:
# "yogur natural" vs "yogur natural azucarado" de Danone en dia vs
# eroski son productos distintos, no un problema de redacción).
# ============================================================

PACKAGING_WORDS = {
    "brik", "tetrabrik", "botella", "botellin", "lata", "tarrina",
    "tarro", "frasco", "bandeja", "barqueta", "bolsa", "malla",
    "estuche", "sobre", "envase", "garrafa", "paquete", "pack",
    "granel",
}

# Vocales/eñe con o sin tilde, para que "botellin" (sin tilde, tal
# como se escribió PACKAGING_WORDS) reconozca también "botellín"
# (con tilde, como llega en product_core). Mismo mecanismo que usa
# normalizer.py para casar item_brand con external_name.
_ACCENT_CLASS = {
    "a": "[aáàâä]",
    "e": "[eéèêë]",
    "i": "[iíìîï]",
    "o": "[oóòôö]",
    "u": "[uúùûü]",
    "n": "[nñ]",
}


def _build_packaging_words_regex(words):
    alternatives = []

    for word in words:
        chars = [
            _ACCENT_CLASS.get(char, re.escape(char))
            for char in word
        ]

        alternatives.append("".join(chars))

    return re.compile(
        r"\b(?:" + "|".join(alternatives) + r")\b",
        re.IGNORECASE
    )


_PACKAGING_WORDS_REGEX = _build_packaging_words_regex(PACKAGING_WORDS)


# ============================================================
# SINGULAR/PLURAL AL COMPARAR product_core
# ============================================================
#
# DIA suele escribir el tipo de producto en plural ("Garbanzos
# cocidos", "Galletas Oreo golden") y Eroski en singular ("Garbanzo
# cocido LA ASTURIANA", "Galleta Golden OREO") - mismo producto,
# convención de redacción distinta. Solo para comparar aquí (nunca se
# persiste): a cada palabra de product_core que termine en vocal + "s"
# (más de 3 letras) se le quita la "s" final - la regla regular del
# español para sustantivos terminados en vocal (galleta/galletas,
# garbanzo/garbanzos, alubia/alubias).
#
# A propósito NO se tocan plurales en "-es" de consonante (yogures/
# yogur, panes/pan): esa regla es más ambigua y con más riesgo de
# fusionar palabras que no deberían - se prefiere cubrir menos casos
# con seguridad.
#
# Comprobado contra los 2859 product_core reales de la base de datos:
# la regla genera 17 fusiones, las 17 son el mismo producto en
# singular y plural (alubia/alubias, guisante/guisantes, patata/
# patatas...), ninguna cambia de significado. Aplicada al matching
# real, sube de 52 a 65 grupos sin ningún falso positivo detectado.
# ============================================================

_VOWELS = set("aeiouáéíóú")


def _singularize_word(word):
    if len(word) <= 3:
        return word

    if word.endswith("s") and word[-2] in _VOWELS:
        return word[:-1]

    return word


def _canonicalize_plurals(text):
    return " ".join(_singularize_word(word) for word in text.split())


def clean_product_core(product_core, packaging_words=None):
    """
    Limpia product_core solo para comparar equivalencia entre
    cadenas (nunca modifica ni persiste nada):
      1. quita las palabras de envase (PACKAGING_WORDS por defecto)
      2. singulariza cada palabra (ver _canonicalize_plurals)

    packaging_words: set opcional para probar otra lista distinta a
    la constante del módulo (lo usa el diagnóstico de
    print_packaging_words_diagnostic).
    """

    if not product_core:
        return None

    regex = (
        _PACKAGING_WORDS_REGEX
        if packaging_words is None
        else _build_packaging_words_regex(packaging_words)
    )

    cleaned = regex.sub(" ", product_core)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not cleaned:
        return None

    return _canonicalize_plurals(cleaned)


def product_cores_compatible(core_a, core_b, packaging_words=None):
    """
    Igualdad exacta de product_core tras quitar palabras de envase.
    Sin similitud difusa a propósito: comprobado contra datos reales
    que un criterio permisivo (p. ej. "un core es subconjunto del
    otro") fusiona productos distintos como "yogur natural" y "yogur
    natural azucarado".
    """

    cleaned_a = clean_product_core(core_a, packaging_words)
    cleaned_b = clean_product_core(core_b, packaging_words)

    if not cleaned_a or not cleaned_b:
        return False

    return cleaned_a == cleaned_b


# ============================================================
# MATCHING EXACTO ENTRE SUPERMERCADOS
# ============================================================

# ============================================================
# ACENTOS/APÓSTROFES AL COMPARAR brand
# ============================================================
#
# La misma marca llega con grafía distinta según la cadena
# ("kellogg's" en dia vs "kelloggs" en eroski, "nestlé" vs "nestle")
# y brand nunca hace match si se compara tal cual. Solo para agrupar
# aquí (nunca se persiste ni se toca el brand guardado en cada
# producto): se pliegan acentos y se quitan apóstrofes/acentos
# tipográficos antes de comparar.
#
# Comprobado contra las 516 marcas reales distintas de la base de
# datos: solo colisionan 2 pares, los dos el mismo caso real de
# grafía (kellogg's/kelloggs, snatt's/snatts) - ninguna fusión falsa.
# ============================================================

_APOSTROPHE_CHARS = "'’`"


def _canonicalize_brand(brand):
    if not brand:
        return brand

    normalized = brand.lower()

    replacements = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
        "ü": "u", "ñ": "n"
    }

    for original, replacement in replacements.items():
        normalized = normalized.replace(original, replacement)

    for char in _APOSTROPHE_CHARS:
        normalized = normalized.replace(char, "")

    return normalized


def _is_eligible_for_matching(product):
    """
    Un producto solo puede entrar en el matching exacto si tiene los
    cinco campos que definen la equivalencia. Si falta alguno (marca
    no detectada, tamaño no detectado...) no se puede comparar de
    forma fiable.
    """

    return (
        product.get("brand")
        and product.get("brand_type")
        and product.get("normalized_unit_type")
        and product.get("normalized_size") is not None
        and product.get("product_core")
    )


def find_exact_matches(products, packaging_words=None):
    """
    Agrupa productos de distintos supermercados que comparten:
      - brand (comparado tras plegar acentos/apóstrofes - ver
        _canonicalize_brand - nunca se persiste, solo agrupa)
      - brand_type
      - normalized_unit_type
      - normalized_size
      - product_core compatible (ver clean_product_core, incluye
        singularizar plurales solo para comparar)

    products: lista de dicts con al menos supermarket_slug, brand,
    brand_type, normalized_size, normalized_unit_type, product_core.

    Devuelve (match_groups, unmatched):
      match_groups: lista de dicts
        {brand, brand_type, normalized_unit_type, normalized_size,
         product_core_clean, products: [...], cheapest_by_slug: {...}}
        - uno por cada grupo con productos de 2 o más supermercados
        distintos.
        cheapest_by_slug: {supermarket_slug: {"product": ..., "count": N}}
        - "product" es el SKU concreto con el precio más bajo de esa
        cadena dentro del grupo (el que habría que comprar). "count"
        es cuántos SKUs distintos tiene esa cadena en el grupo (p. ej.
        Eroski vendiendo la misma leche en brik y en botella). Cuando
        una cadena tiene más de un SKU en el grupo, el precio de esa
        cadena para comparar entre supermercados es SIEMPRE el más
        barato de los suyos, nunca uno arbitrario.
      unmatched: productos que no entraron en ningún match_group,
        bien por falta de datos (brand/tamaño/product_core sin
        detectar), bien porque nadie más comparte su combinación
        exacta.
    """

    eligible = [p for p in products if _is_eligible_for_matching(p)]
    unmatched = [p for p in products if not _is_eligible_for_matching(p)]

    groups_by_key = defaultdict(list)

    for product in eligible:
        cleaned_core = clean_product_core(
            product["product_core"],
            packaging_words
        )

        key = (
            _canonicalize_brand(product["brand"]),
            product["brand_type"],
            product["normalized_unit_type"],
            product["normalized_size"],
            cleaned_core
        )

        groups_by_key[key].append(product)

    match_groups = []

    for key, group_products in groups_by_key.items():
        slugs = {p["supermarket_slug"] for p in group_products}

        if len(slugs) >= 2:
            # key[0] es la marca canonicalizada (sin acentos ni
            # apostrofes, solo para agrupar) - se guarda en cambio
            # una grafia real observada, para no inventar ni persistir
            # una forma que no existe en ningun producto.
            match_groups.append({
                "brand": group_products[0]["brand"],
                "brand_type": key[1],
                "normalized_unit_type": key[2],
                "normalized_size": key[3],
                "product_core_clean": key[4],
                "products": group_products,
                "cheapest_by_slug": _cheapest_by_slug(group_products)
            })
        else:
            unmatched.extend(group_products)

    return match_groups, unmatched


def _cheapest_by_slug(group_products):
    """
    Para cada supermercado presente en el grupo, el SKU concreto con
    el precio más bajo (no solo la cifra: el producto entero, para
    saber qué comprar) y cuántos SKUs distintos tiene esa cadena en
    el grupo.

    Ignora productos sin current_price (no debería pasar en datos ya
    validados por los scrapers, pero se protege por si acaso).
    """

    by_slug = defaultdict(list)

    for product in group_products:
        if product.get("current_price") is not None:
            by_slug[product["supermarket_slug"]].append(product)

    result = {}

    for slug, slug_products in by_slug.items():
        cheapest = min(slug_products, key=lambda p: p["current_price"])

        result[slug] = {
            "product": cheapest,
            "count": len(slug_products)
        }

    return result


# ============================================================
# ALTERNATIVAS DE FORMATO MÁS BARATAS (INFORMATIVO)
# ============================================================

def find_format_alternatives(product, products):
    """
    Para un producto dado, busca otros productos (de cualquier
    supermercado, incluido el mismo) con:
      - mismo brand
      - mismo normalized_unit_type (para que normalized_unit_price
        sea comparable: no tiene sentido comparar €/L con €/kg)
      - product_core compatible (ver clean_product_core)
      - normalized_size distinto
      - normalized_unit_price MENOR que el del producto dado

    Es puramente informativo, separado del matching exacto (no exige
    mismo brand_type ni mismo normalized_size - todo lo contrario,
    busca tamaños distintos). Devuelve una lista ordenada de más
    barato a menos barato.
    """

    if (
        not product.get("brand")
        or not product.get("normalized_unit_type")
        or not product.get("product_core")
        or product.get("normalized_unit_price") is None
    ):
        return []

    alternatives = []
    product_brand_canon = _canonicalize_brand(product["brand"])

    for candidate in products:
        if candidate.get("id") == product.get("id"):
            continue

        if _canonicalize_brand(candidate.get("brand")) != product_brand_canon:
            continue

        if candidate.get("normalized_unit_type") != product["normalized_unit_type"]:
            continue

        if candidate.get("normalized_unit_price") is None:
            continue

        if candidate.get("normalized_size") == product.get("normalized_size"):
            continue

        if not product_cores_compatible(
            candidate.get("product_core"),
            product.get("product_core")
        ):
            continue

        if candidate["normalized_unit_price"] >= product["normalized_unit_price"]:
            continue

        alternatives.append(candidate)

    alternatives.sort(key=lambda p: p["normalized_unit_price"])

    return alternatives


# ============================================================
# CARGA DESDE MYSQL (SOLO LECTURA)
# ============================================================

def fetch_normalized_products(slugs=("dia", "eroski")):
    """
    Carga de raw_scraped_products los campos necesarios para el
    matching, ya normalizados. Solo lectura.
    """

    connection = get_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        placeholders = ",".join(["%s"] * len(slugs))

        cursor.execute(
            f"""
            SELECT
                s.slug AS supermarket_slug,
                r.id,
                r.external_product_id,
                r.external_name,
                r.current_price,
                r.brand,
                r.brand_type,
                r.normalized_size,
                r.normalized_unit_type,
                r.normalized_unit_price,
                r.product_core
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
# FORMATEO PARA EL INFORME
# ============================================================

def _format_price(value):
    if value is None:
        return "?"

    return f"{float(value):.2f}€"


def _format_size(size, unit_type):
    if size is None:
        return "?"

    return f"{size:g}{unit_type or ''}"


def _group_sort_key(group):
    return (group["brand"] or "", group["normalized_size"] or 0)


# ============================================================
# INFORME: MATCHING EXACTO
# ============================================================

def print_exact_matches_report(products):
    match_groups, unmatched = find_exact_matches(products)

    print("=" * 100)
    print("MATCHING EXACTO ENTRE SUPERMERCADOS")
    print("=" * 100)
    print("Productos analizados:", len(products))
    print("Grupos de equivalencia exacta encontrados:", len(match_groups))
    print("-" * 100)

    for group in sorted(match_groups, key=_group_sort_key):
        size_label = _format_size(
            group["normalized_size"],
            group["normalized_unit_type"]
        )

        print()
        print(
            f"[{group['brand']} | {group['brand_type']} | {size_label}] "
            f"core='{group['product_core_clean']}'"
        )

        for product in sorted(
            group["products"],
            key=lambda p: p["supermarket_slug"]
        ):
            print(
                f"    {product['supermarket_slug']:8} | "
                f"{_format_price(product['current_price']):>8} | "
                f"{product['external_name']}"
            )

        cheapest_by_slug = group["cheapest_by_slug"]

        summary_parts = []

        for slug in sorted(cheapest_by_slug):
            entry = cheapest_by_slug[slug]
            price_label = _format_price(entry["product"]["current_price"])

            if entry["count"] > 1:
                price_label += f" ({entry['count']} formatos)"

            summary_parts.append(f"{slug}: {price_label}")

        print("    -> " + " | ".join(summary_parts))

        if len(cheapest_by_slug) == 2:
            slugs = sorted(cheapest_by_slug)
            price_a = float(cheapest_by_slug[slugs[0]]["product"]["current_price"])
            price_b = float(cheapest_by_slug[slugs[1]]["product"]["current_price"])
            diff = abs(price_a - price_b)
            higher = max(price_a, price_b)
            pct = (diff / higher * 100) if higher else 0.0

            if price_a < price_b:
                cheaper_slug, cheaper_entry = slugs[0], cheapest_by_slug[slugs[0]]
            elif price_b < price_a:
                cheaper_slug, cheaper_entry = slugs[1], cheapest_by_slug[slugs[1]]
            else:
                cheaper_slug, cheaper_entry = None, None

            if cheaper_slug:
                print(
                    f"    -> diferencia: {diff:.2f}€ ({pct:.1f}%) "
                    f"más barato en {cheaper_slug} "
                    f"(\"{cheaper_entry['product']['external_name']}\")"
                )
            else:
                print("    -> mismo precio en las dos cadenas")

    print()
    print("-" * 100)

    incomplete = [p for p in unmatched if not _is_eligible_for_matching(p)]
    complete_but_alone = [p for p in unmatched if _is_eligible_for_matching(p)]

    print(
        "Productos sin pareja en la otra cadena:",
        len(unmatched),
        "de",
        len(products)
    )
    print(
        "  - sin datos suficientes para comparar "
        "(falta brand/tamaño/product_core):",
        len(incomplete)
    )
    print(
        "  - con datos completos pero sin equivalente en otra cadena:",
        len(complete_but_alone)
    )

    for slug in sorted({p["supermarket_slug"] for p in products}):
        count = sum(
            1 for p in complete_but_alone
            if p["supermarket_slug"] == slug
        )

        print(f"      · solo en {slug}: {count}")

    print("=" * 100)

    return match_groups, unmatched


# ============================================================
# INFORME: ALTERNATIVAS DE FORMATO MÁS BARATAS
# ============================================================

def print_format_alternatives_report(products, max_examples=10):
    examples = []

    for product in products:
        alternatives = find_format_alternatives(product, products)

        if alternatives:
            best = alternatives[0]
            savings = float(
                product["normalized_unit_price"] - best["normalized_unit_price"]
            )
            examples.append((savings, product, alternatives))

    examples.sort(key=lambda item: item[0], reverse=True)

    print()
    print("=" * 100)
    print("ALTERNATIVAS DE FORMATO MÁS BARATAS (informativo)")
    print("=" * 100)
    print(
        "Productos con al menos una alternativa de otro tamaño más "
        "barata por",
        "L/kg:",
        len(examples)
    )
    print("-" * 100)

    for savings, product, alternatives in examples[:max_examples]:
        size_label = _format_size(
            product["normalized_size"],
            product["normalized_unit_type"]
        )

        print()
        print(
            f"{product['supermarket_slug']} | {size_label} | "
            f"{_format_price(product['normalized_unit_price'])}/"
            f"{product['normalized_unit_type']} | "
            f"{product['external_name']}"
        )

        for alt in alternatives:
            alt_size_label = _format_size(
                alt["normalized_size"],
                alt["normalized_unit_type"]
            )

            print(
                f"    -> {alt['supermarket_slug']} | {alt_size_label} | "
                f"{_format_price(alt['normalized_unit_price'])}/"
                f"{alt['normalized_unit_type']} | "
                f"{alt['external_name']}"
            )

    if len(examples) > max_examples:
        print()
        print(f"... y {len(examples) - max_examples} casos más.")

    print("=" * 100)


# ============================================================
# DIAGNÓSTICO: IMPACTO DE LA LISTA COMPLETA DE PACKAGING_WORDS
# FRENTE A QUITAR SOLO "brik"
# ============================================================

def _group_product_id_sets(match_groups):
    return {
        frozenset(p["id"] for p in group["products"])
        for group in match_groups
    }


def print_packaging_words_diagnostic(products):
    brik_only_groups, _ = find_exact_matches(
        products,
        packaging_words={"brik"}
    )
    full_list_groups, _ = find_exact_matches(products)

    brik_only_id_sets = _group_product_id_sets(brik_only_groups)

    new_groups = [
        group for group in full_list_groups
        if frozenset(p["id"] for p in group["products"])
        not in brik_only_id_sets
    ]

    print()
    print("=" * 100)
    print(
        "DIAGNÓSTICO: PACKAGING_WORDS completo vs. quitar solo \"brik\""
    )
    print("=" * 100)
    print("Grupos quitando solo 'brik':", len(brik_only_groups))
    print("Grupos con la lista completa de PACKAGING_WORDS:", len(full_list_groups))
    print("Grupos NUEVOS gracias al resto de la lista:", len(new_groups))
    print("-" * 100)

    for group in sorted(new_groups, key=_group_sort_key):
        size_label = _format_size(
            group["normalized_size"],
            group["normalized_unit_type"]
        )

        print()
        print(
            f"[{group['brand']} | {size_label}] "
            f"core='{group['product_core_clean']}'"
        )

        for product in sorted(
            group["products"],
            key=lambda p: p["supermarket_slug"]
        ):
            print(
                f"    {product['supermarket_slug']:8} | "
                f"{product['external_name']}"
            )

    print("=" * 100)


# ============================================================
# EJECUCIÓN PRINCIPAL
# ============================================================

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    products = fetch_normalized_products(slugs=("dia", "eroski"))

    print_packaging_words_diagnostic(products)
    print_exact_matches_report(products)
    print_format_alternatives_report(products)


if __name__ == "__main__":
    main()
