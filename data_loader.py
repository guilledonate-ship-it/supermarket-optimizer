from db import get_connection


def load_product_catalog():
    """
    Carga el catálogo de productos desde MySQL.
    Devuelve un diccionario parecido al product_catalog mock que teníamos antes.
    """

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        SELECT
            p.product_key,
            p.name,
            c.name AS category,
            p.brand,
            p.brand_type,
            p.unit_size,
            p.unit_type,
            eg.slug AS equivalence_group
        FROM products p
        LEFT JOIN categories c ON p.category_id = c.id
        LEFT JOIN equivalence_groups eg ON p.equivalence_group_id = eg.id
        WHERE p.active = TRUE;
    """

    cursor.execute(query)
    rows = cursor.fetchall()

    catalog = {}

    for row in rows:
        catalog[row["product_key"]] = {
            "name": row["name"],
            "category": row["category"],
            "brand": row["brand"],
            "brand_type": row["brand_type"],
            "unit_size": float(row["unit_size"]) if row["unit_size"] is not None else None,
            "unit_type": row["unit_type"],
            "equivalence_group": row["equivalence_group"]
        }

    cursor.close()
    conn.close()

    return catalog


def load_latest_prices():
    """
    Carga el último precio disponible de cada producto en cada supermercado.
    Devuelve un diccionario:
    
    {
        "leche_pascual_entera_1l": {
            "carrefour": 1.55,
            "dia": 1.49
        }
    }
    """

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        SELECT
            p.product_key,
            s.slug AS supermarket_slug,
            pr.price
        FROM prices pr
        JOIN product_offers po ON pr.product_offer_id = po.id
        JOIN products p ON po.product_id = p.id
        JOIN supermarkets s ON po.supermarket_id = s.id
        JOIN (
            SELECT
                product_offer_id,
                MAX(scraped_at) AS latest_scraped_at
            FROM prices
            GROUP BY product_offer_id
        ) latest
            ON latest.product_offer_id = pr.product_offer_id
            AND latest.latest_scraped_at = pr.scraped_at
        WHERE
            p.active = TRUE
            AND s.active = TRUE
            AND po.available = TRUE;
    """

    cursor.execute(query)
    rows = cursor.fetchall()

    prices = {}

    for row in rows:
        product_key = row["product_key"]
        supermarket = row["supermarket_slug"]
        price = float(row["price"])

        if product_key not in prices:
            prices[product_key] = {}

        prices[product_key][supermarket] = price

    cursor.close()
    conn.close()

    return prices


def load_supermarket_names():
    """
    Carga nombres bonitos de supermercados.
    """

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT slug, name
        FROM supermarkets
        WHERE active = TRUE;
    """)

    rows = cursor.fetchall()

    supermarket_names = {}

    for row in rows:
        supermarket_names[row["slug"]] = row["name"]

    cursor.close()
    conn.close()

    return supermarket_names


def load_shipping_rules():
    """
    Carga reglas de envío desde MySQL.
    De momento las dejamos como lista, porque luego el cálculo dependerá
    del total del pedido y del método de entrega.
    """

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        SELECT
            s.slug AS supermarket_slug,
            dm.slug AS delivery_method,
            sr.postal_code,
            sr.min_order_amount,
            sr.max_order_amount,
            sr.shipping_cost,
            sr.free_shipping_threshold
        FROM shipping_rules sr
        JOIN supermarkets s ON sr.supermarket_id = s.id
        JOIN delivery_methods dm ON sr.delivery_method_id = dm.id
        WHERE
            sr.active = TRUE
            AND s.active = TRUE
            AND dm.active = TRUE;
    """

    cursor.execute(query)
    rows = cursor.fetchall()

    rules = []

    for row in rows:
        rules.append({
            "supermarket": row["supermarket_slug"],
            "delivery_method": row["delivery_method"],
            "postal_code": row["postal_code"],
            "min_order_amount": float(row["min_order_amount"]) if row["min_order_amount"] is not None else None,
            "max_order_amount": float(row["max_order_amount"]) if row["max_order_amount"] is not None else None,
            "shipping_cost": float(row["shipping_cost"]) if row["shipping_cost"] is not None else 0,
            "free_shipping_threshold": float(row["free_shipping_threshold"]) if row["free_shipping_threshold"] is not None else None
        })

    cursor.close()
    conn.close()

    return rules