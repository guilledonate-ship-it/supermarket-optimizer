"""
Consultas de SOLO LECTURA para los endpoints del MVP de portfolio:
  - get_prices()          -> GET /prices
  - get_all_alternatives() -> GET /alternatives y /alternatives/{product_key}
  - get_stats()           -> GET /stats

No modifica matcher.py, normalizer.py, promoter.py, los scrapers ni la
base de datos. Reutiliza data_loader.py y las funciones de matcher.py
tal cual están.

Los resultados se cachean en memoria unos minutos: los datos solo
cambian cuando se ejecuta run_pipeline.py, y así /alternatives no
recalcula el matching en cada petición.
"""

import time
from decimal import Decimal

from db import get_connection
from data_loader import load_latest_prices, load_supermarket_names
from matcher import fetch_normalized_products, find_format_alternatives


CACHE_TTL_SECONDS = 600
MAX_ALTERNATIVES = 3

_cache = {}


def _cached(key, builder):
    now = time.time()
    hit = _cache.get(key)

    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]

    value = builder()
    _cache[key] = (now, value)

    return value


def _num(value, digits=None):
    if value is None:
        return None

    value = float(value) if isinstance(value, Decimal) else value

    return round(value, digits) if digits is not None else value


def _iso(value):
    return value.isoformat() if value is not None else None


# ============================================================
# /prices
# ============================================================

def _priced_supermarkets(prices, names):
    """
    Solo las cadenas que tienen al menos un precio real. Una cadena
    activa en `supermarkets` pero sin precios (p. ej. un scraper aún
    no integrado) no debe aparecer como columna vacía en la interfaz.
    """

    slugs = {slug for by_market in prices.values() for slug in by_market}

    return sorted(slugs, key=lambda slug: names.get(slug, slug).lower())


def _build_prices():
    prices = load_latest_prices()
    names = load_supermarket_names()
    slugs = _priced_supermarkets(prices, names)

    return {
        "supermarkets": [
            {"slug": slug, "name": names.get(slug, slug)} for slug in slugs
        ],
        "prices": {
            product_key: {
                slug: _num(by_market.get(slug), 2) for slug in slugs
            }
            for product_key, by_market in prices.items()
        }
    }


def get_prices():
    return _cached("prices", _build_prices)


# ============================================================
# /alternatives
# ============================================================

def _load_offer_links():
    """
    Relación entre el catálogo (products.product_key) y las filas de
    raw_scraped_products, a través de product_offers.

    Se enlaza por (supermercado, external_product_id) y, si la oferta
    no tiene external_product_id, por (supermercado, external_name).
    """

    connection = get_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                p.product_key,
                s.slug AS supermarket_slug,
                po.external_product_id,
                po.external_name
            FROM product_offers po
            JOIN products p ON p.id = po.product_id
            JOIN supermarkets s ON s.id = po.supermarket_id
            WHERE p.active = TRUE
              AND s.active = TRUE
              AND po.available = TRUE;
            """
        )

        return cursor.fetchall()

    finally:
        cursor.close()
        connection.close()


def _raw_lookup_keys(row):
    keys = []

    if row.get("external_product_id"):
        keys.append(("id", row["supermarket_slug"], str(row["external_product_id"])))

    if row.get("external_name"):
        keys.append(("name", row["supermarket_slug"], row["external_name"].strip().lower()))

    return keys


def _serialize_alternative(raw, reference_unit_price, product_key, names):
    unit_price = _num(raw["normalized_unit_price"], 2)

    return {
        "supermarket": raw["supermarket_slug"],
        "supermarket_name": names.get(raw["supermarket_slug"], raw["supermarket_slug"]),
        "name": raw["external_name"],
        "size": _num(raw["normalized_size"], 3),
        "unit": raw["normalized_unit_type"],
        "price": _num(raw["current_price"], 2),
        "unit_price": unit_price,
        "saving_pct": round((1 - float(raw["normalized_unit_price"]) / reference_unit_price) * 100),
        "product_key": product_key
    }


def _build_alternatives():
    names = load_supermarket_names()
    slugs = tuple(names.keys())

    raw_products = fetch_normalized_products(slugs=slugs) if slugs else []
    links = _load_offer_links()

    raw_by_key = {}

    for raw in raw_products:
        for key in _raw_lookup_keys(raw):
            raw_by_key.setdefault(key, raw)

    # product_key -> filas raw que lo componen (una por cadena)
    raws_by_product = {}
    # id de fila raw -> product_key (para saber si una alternativa
    # se puede añadir a la cesta)
    product_by_raw_id = {}

    for link in links:
        raw = next(
            (raw_by_key[k] for k in _raw_lookup_keys(link) if k in raw_by_key),
            None
        )

        if raw is None:
            continue

        raws_by_product.setdefault(link["product_key"], []).append(raw)
        product_by_raw_id[raw["id"]] = link["product_key"]

    result = {}

    for product_key, references in raws_by_product.items():
        priced = [r for r in references if r.get("normalized_unit_price") is not None]

        if not priced:
            continue

        best_reference = min(priced, key=lambda r: r["normalized_unit_price"])
        reference_unit_price = float(best_reference["normalized_unit_price"])
        reference_ids = {r["id"] for r in references}

        candidates = {}

        for reference in priced:
            for alt in find_format_alternatives(reference, raw_products):
                if alt["id"] in reference_ids:
                    continue

                # Solo interesa si mejora el €/L o €/kg más barato que
                # ya ofrece el producto en alguna cadena.
                if float(alt["normalized_unit_price"]) >= reference_unit_price:
                    continue

                candidates[alt["id"]] = alt

        if not candidates:
            continue

        ordered = sorted(candidates.values(), key=lambda r: r["normalized_unit_price"])

        result[product_key] = {
            "reference": {
                "supermarket": best_reference["supermarket_slug"],
                "unit": best_reference["normalized_unit_type"],
                "unit_price": round(reference_unit_price, 2)
            },
            "alternatives": [
                _serialize_alternative(
                    raw,
                    reference_unit_price,
                    product_by_raw_id.get(raw["id"]),
                    names
                )
                for raw in ordered[:MAX_ALTERNATIVES]
            ]
        }

    return result


def get_all_alternatives():
    return _cached("alternatives", _build_alternatives)


# ============================================================
# /stats
# ============================================================

def _fetch_all(cursor, query):
    cursor.execute(query)
    return cursor.fetchall()


def _build_stats():
    names = load_supermarket_names()
    prices = load_latest_prices()

    connection = get_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        raw_rows = _fetch_all(
            cursor,
            """
            SELECT
                s.slug,
                COUNT(*) AS raw_products,
                SUM(
                    r.brand IS NOT NULL
                    AND r.normalized_size IS NOT NULL
                    AND r.product_core IS NOT NULL
                ) AS normalized_products,
                MAX(r.last_seen_at) AS last_seen_at
            FROM raw_scraped_products r
            JOIN supermarkets s ON s.id = r.supermarket_id
            WHERE s.active = TRUE
            GROUP BY s.slug;
            """
        )

        # raw_data puede contener JSON no válido en alguna fila antigua:
        # si la consulta falla, la métrica se omite en vez de romper /stats.
        try:
            category_rows = _fetch_all(
                cursor,
                """
                SELECT
                    s.slug,
                    COUNT(DISTINCT JSON_UNQUOTE(
                        JSON_EXTRACT(r.raw_data, '$.category_name')
                    )) AS categories
                FROM raw_scraped_products r
                JOIN supermarkets s ON s.id = r.supermarket_id
                WHERE s.active = TRUE
                GROUP BY s.slug;
                """
            )
        except Exception:
            category_rows = []

        catalog_row = _fetch_all(
            cursor,
            """
            SELECT
                COUNT(*) AS catalog_products,
                COUNT(DISTINCT category_id) AS categories
            FROM products
            WHERE active = TRUE;
            """
        )[0]

        offers_by_slug = {
            row["slug"]: row["offers"]
            for row in _fetch_all(
                cursor,
                """
                SELECT s.slug, COUNT(*) AS offers
                FROM product_offers po
                JOIN products p ON p.id = po.product_id
                JOIN supermarkets s ON s.id = po.supermarket_id
                WHERE p.active = TRUE AND s.active = TRUE AND po.available = TRUE
                GROUP BY s.slug;
                """
            )
        }

        last_price_row = _fetch_all(
            cursor,
            "SELECT MAX(scraped_at) AS last_price_at FROM prices;"
        )[0]

    finally:
        cursor.close()
        connection.close()

    categories_by_slug = {row["slug"]: row["categories"] for row in category_rows}
    priced_slugs = set(_priced_supermarkets(prices, names))

    supermarkets = []

    for row in sorted(raw_rows, key=lambda r: names.get(r["slug"], r["slug"]).lower()):
        if row["slug"] not in priced_slugs:
            continue

        supermarkets.append({
            "slug": row["slug"],
            "name": names.get(row["slug"], row["slug"]),
            "raw_products": int(row["raw_products"] or 0),
            "normalized_products": int(row["normalized_products"] or 0),
            "matched_products": int(offers_by_slug.get(row["slug"], 0)),
            "categories": categories_by_slug.get(row["slug"]),
            "last_seen_at": _iso(row["last_seen_at"])
        })

    equivalence_groups = sum(
        1 for by_market in prices.values()
        if len([s for s in by_market if s in priced_slugs]) >= 2
    )

    return {
        "supermarkets": supermarkets,
        "totals": {
            "raw_products": sum(s["raw_products"] for s in supermarkets),
            "normalized_products": sum(s["normalized_products"] for s in supermarkets),
            "matched_offers": sum(s["matched_products"] for s in supermarkets),
            "catalog_products": int(catalog_row["catalog_products"] or 0),
            "equivalence_groups": equivalence_groups,
            "categories": int(catalog_row["categories"] or 0)
        },
        "last_updated": _iso(last_price_row["last_price_at"])
    }


def get_stats():
    return _cached("stats", _build_stats)
