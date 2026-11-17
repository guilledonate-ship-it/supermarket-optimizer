from db_writer_backup import (
    start_scraping_log,
    finish_scraping_log,
    insert_price
)

log_id = start_scraping_log(
    supermarket_slug="carrefour",
    scraper_name="test_writer_carrefour"
)

try:
    insert_price(
        product_key="leche_pascual_entera_1l",
        supermarket_slug="carrefour",
        price=1.23,
        price_per_unit=1.23,
        data_source="test_writer"
    )

    finish_scraping_log(
        log_id=log_id,
        status="success",
        products_found=1,
        products_updated=1,
        errors_count=0
    )

    print("Test de escritura completado correctamente")

except Exception as e:
    finish_scraping_log(
        log_id=log_id,
        status="error",
        products_found=0,
        products_updated=0,
        errors_count=1,
        error_message=str(e)
    )

    print("Error en test_writer:", e)