import json
from db import get_connection

def get_supermarket_id(supermarket_slug):
    """
    Devuelve el ID interno de un supermercado a partir de su slug.

    Ejemplo:
        get_supermarket_id("dia") -> 2
    """
    connection = get_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        query = """
            SELECT id
            FROM supermarkets
            WHERE slug = %s
              AND active = TRUE
            LIMIT 1;
        """

        cursor.execute(query, (supermarket_slug,))
        supermarket = cursor.fetchone()

        if supermarket is None:
            raise ValueError(
                f"No existe un supermercado activo con slug '{supermarket_slug}'."
            )

        return supermarket["id"]

    finally:
        cursor.close()
        connection.close()


def start_scraping_log(supermarket_slug, scraper_name):
    """
    Crea un registro de scraping con estado 'running'.

    Devuelve el ID del log creado.
    """
    supermarket_id = get_supermarket_id(supermarket_slug)

    connection = get_connection()
    cursor = connection.cursor()

    try:
        query = """
            INSERT INTO scraping_logs (
                supermarket_id,
                scraper_name,
                status,
                started_at,
                products_found,
                products_updated,
                errors_count
            )
            VALUES (
                %s,
                %s,
                'running',
                NOW(),
                0,
                0,
                0
            );
        """

        cursor.execute(
            query,
            (
                supermarket_id,
                scraper_name
            )
        )

        connection.commit()

        return cursor.lastrowid

    except Exception:
        connection.rollback()
        raise

    finally:
        cursor.close()
        connection.close()


def finish_scraping_log(
    log_id,
    status,
    products_found=0,
    products_updated=0,
    errors_count=0,
    error_message=None
):
    """
    Finaliza un registro de scraping.

    Valores habituales de status:
        success
        partial_success
        error
    """
    connection = get_connection()
    cursor = connection.cursor()

    try:
        query = """
            UPDATE scraping_logs
            SET
                status = %s,
                finished_at = NOW(),
                products_found = %s,
                products_updated = %s,
                errors_count = %s,
                error_message = %s
            WHERE id = %s;
        """

        cursor.execute(
            query,
            (
                status,
                products_found,
                products_updated,
                errors_count,
                error_message,
                log_id
            )
        )

        if cursor.rowcount == 0:
            raise ValueError(
                f"No existe ningún scraping_log con id {log_id}."
            )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        cursor.close()
        connection.close()


def get_product_offer_id(product_key, supermarket_slug):
    """
    Busca el product_offer que conecta un producto maestro
    con un supermercado concreto.

    Ejemplo:
        leche_pascual_entera_1l + dia
    """
    connection = get_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        query = """
            SELECT
                po.id
            FROM product_offers po
            INNER JOIN products p
                ON p.id = po.product_id
            INNER JOIN supermarkets s
                ON s.id = po.supermarket_id
            WHERE p.product_key = %s
              AND s.slug = %s
            LIMIT 1;
        """

        cursor.execute(
            query,
            (
                product_key,
                supermarket_slug
            )
        )

        product_offer = cursor.fetchone()

        if product_offer is None:
            raise ValueError(
                "No existe product_offer para "
                f"producto='{product_key}' y "
                f"supermercado='{supermarket_slug}'."
            )

        return product_offer["id"]

    finally:
        cursor.close()
        connection.close()


def update_product_offer_metadata(
    product_key,
    supermarket_slug,
    external_name=None,
    product_url=None,
    image_url=None,
    external_id=None
):
    """
    Actualiza la información específica del producto en un supermercado.

    Los campos que lleguen como None no sustituyen los valores existentes.
    """
    product_offer_id = get_product_offer_id(
        product_key,
        supermarket_slug
    )

    connection = get_connection()
    cursor = connection.cursor()

    try:
        query = """
            UPDATE product_offers
            SET
                external_name = COALESCE(%s, external_name),
                product_url = COALESCE(%s, product_url),
                image_url = COALESCE(%s, image_url),
                external_id = COALESCE(%s, external_id),
                available = TRUE,
                last_seen_at = NOW()
            WHERE id = %s;
        """

        cursor.execute(
            query,
            (
                external_name,
                product_url,
                image_url,
                external_id,
                product_offer_id
            )
        )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        cursor.close()
        connection.close()


def insert_price(
    product_key,
    supermarket_slug,
    price,
    price_per_unit=None,
    data_source="manual",
    external_name=None,
    product_url=None,
    image_url=None,
    external_id=None
):
    """
    Inserta un nuevo precio histórico para un producto y supermercado.

    También actualiza:
        product_offers.last_seen_at
        product_offers.available

    Devuelve el ID del precio insertado.
    """
    if price is None:
        raise ValueError("El precio no puede ser None.")

    price = float(price)

    if price <= 0:
        raise ValueError(
            f"El precio debe ser superior a cero. Precio recibido: {price}"
        )

    if price_per_unit is not None:
        price_per_unit = float(price_per_unit)

        if price_per_unit <= 0:
            price_per_unit = None

    product_offer_id = get_product_offer_id(
        product_key,
        supermarket_slug
    )

    connection = get_connection()
    cursor = connection.cursor()

    try:
        insert_query = """
            INSERT INTO prices (
                product_offer_id,
                price,
                price_per_unit,
                currency,
                data_source,
                scraped_at
            )
            VALUES (
                %s,
                %s,
                %s,
                'EUR',
                %s,
                NOW()
            );
        """

        cursor.execute(
            insert_query,
            (
                product_offer_id,
                price,
                price_per_unit,
                data_source
            )
        )

        price_id = cursor.lastrowid

        update_offer_query = """
            UPDATE product_offers
            SET
                external_name = COALESCE(%s, external_name),
                product_url = COALESCE(%s, product_url),
                image_url = COALESCE(%s, image_url),
                external_id = COALESCE(%s, external_id),
                available = TRUE,
                last_seen_at = NOW()
            WHERE id = %s;
        """

        cursor.execute(
            update_offer_query,
            (
                external_name,
                product_url,
                image_url,
                external_id,
                product_offer_id
            )
        )

        connection.commit()

        return price_id

    except Exception:
        connection.rollback()
        raise

    finally:
        cursor.close()
        connection.close()
def upsert_raw_scraped_product(
    supermarket_slug,
    scraper_name,
    external_name,
    current_price,
    external_product_id=None,
    normalized_name=None,
    original_price=None,
    price_per_unit=None,
    unit_price_text=None,
    product_url=None,
    image_url=None,
    available=True,
    raw_data=None
):
    """
    Inserta o actualiza un producto obtenido mediante scraping
    en la tabla raw_scraped_products.

    El producto se mantiene pendiente de matching hasta que se vincule
    con un producto maestro de la tabla products.
    """

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT id
            FROM supermarkets
            WHERE slug = %s
            LIMIT 1;
            """,
            (supermarket_slug,)
        )

        supermarket = cursor.fetchone()

        if not supermarket:
            raise ValueError(
                f"No existe el supermercado: {supermarket_slug}"
            )

        supermarket_id = supermarket["id"]

        if raw_data is not None:
            raw_json = json.dumps(
                raw_data,
                ensure_ascii=False,
                default=str
            )
        else:
            raw_json = None

        if external_product_id:
            cursor.execute(
                """
                INSERT INTO raw_scraped_products (
                    supermarket_id,
                    scraper_name,
                    external_product_id,
                    external_name,
                    normalized_name,
                    product_url,
                    image_url,
                    current_price,
                    original_price,
                    price_per_unit,
                    unit_price_text,
                    available,
                    matching_status,
                    raw_data
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, 'pending', %s
                )
                ON DUPLICATE KEY UPDATE
                    scraper_name = VALUES(scraper_name),
                    external_name = VALUES(external_name),
                    normalized_name = VALUES(normalized_name),
                    product_url = COALESCE(
                        VALUES(product_url),
                        product_url
                    ),
                    image_url = COALESCE(
                        VALUES(image_url),
                        image_url
                    ),
                    current_price = VALUES(current_price),
                    original_price = VALUES(original_price),
                    price_per_unit = VALUES(price_per_unit),
                    unit_price_text = VALUES(unit_price_text),
                    available = VALUES(available),
                    raw_data = VALUES(raw_data),
                    last_seen_at = CURRENT_TIMESTAMP;
                """,
                (
                    supermarket_id,
                    scraper_name,
                    external_product_id,
                    external_name,
                    normalized_name,
                    product_url,
                    image_url,
                    current_price,
                    original_price,
                    price_per_unit,
                    unit_price_text,
                    available,
                    raw_json
                )
            )

            raw_product_id = cursor.lastrowid

            if raw_product_id == 0:
                cursor.execute(
                    """
                    SELECT id
                    FROM raw_scraped_products
                    WHERE supermarket_id = %s
                      AND external_product_id = %s
                    LIMIT 1;
                    """,
                    (
                        supermarket_id,
                        external_product_id
                    )
                )

                existing_product = cursor.fetchone()

                if not existing_product:
                    raise ValueError(
                        "No se pudo recuperar el producto bruto "
                        f"con identificador {external_product_id}"
                    )

                raw_product_id = existing_product["id"]

        else:
            cursor.execute(
                """
                SELECT id
                FROM raw_scraped_products
                WHERE supermarket_id = %s
                  AND external_name = %s
                LIMIT 1;
                """,
                (
                    supermarket_id,
                    external_name
                )
            )

            existing_product = cursor.fetchone()

            if existing_product:
                raw_product_id = existing_product["id"]

                cursor.execute(
                    """
                    UPDATE raw_scraped_products
                    SET
                        scraper_name = %s,
                        normalized_name = %s,
                        product_url = COALESCE(%s, product_url),
                        image_url = COALESCE(%s, image_url),
                        current_price = %s,
                        original_price = %s,
                        price_per_unit = %s,
                        unit_price_text = %s,
                        available = %s,
                        raw_data = %s,
                        last_seen_at = CURRENT_TIMESTAMP
                    WHERE id = %s;
                    """,
                    (
                        scraper_name,
                        normalized_name,
                        product_url,
                        image_url,
                        current_price,
                        original_price,
                        price_per_unit,
                        unit_price_text,
                        available,
                        raw_json,
                        raw_product_id
                    )
                )

            else:
                cursor.execute(
                    """
                    INSERT INTO raw_scraped_products (
                        supermarket_id,
                        scraper_name,
                        external_name,
                        normalized_name,
                        product_url,
                        image_url,
                        current_price,
                        original_price,
                        price_per_unit,
                        unit_price_text,
                        available,
                        matching_status,
                        raw_data
                    )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, 'pending', %s
                    );
                    """,
                    (
                        supermarket_id,
                        scraper_name,
                        external_name,
                        normalized_name,
                        product_url,
                        image_url,
                        current_price,
                        original_price,
                        price_per_unit,
                        unit_price_text,
                        available,
                        raw_json
                    )
                )

                raw_product_id = cursor.lastrowid

        conn.commit()

        return raw_product_id

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()