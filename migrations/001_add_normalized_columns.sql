-- Añade las columnas de normalización de producto a raw_scraped_products.
--
-- Estas columnas las rellena normalizer.py (función apply_normalization_to_db)
-- a partir de external_name / current_price. Todas son NULLABLE: un producto
-- sin normalizar todavía simplemente tiene estos campos a NULL.
--
-- Compatible con MySQL 8.0 (usa ADD COLUMN IF NOT EXISTS para poder
-- reejecutar el script sin que falle si ya se aplicó).

ALTER TABLE raw_scraped_products
    ADD COLUMN IF NOT EXISTS brand VARCHAR(120) NULL,
    ADD COLUMN IF NOT EXISTS brand_type VARCHAR(20) NULL,
    ADD COLUMN IF NOT EXISTS pack_units INT NULL,
    ADD COLUMN IF NOT EXISTS unit_size DECIMAL(10,3) NULL,
    ADD COLUMN IF NOT EXISTS unit_type VARCHAR(10) NULL,
    ADD COLUMN IF NOT EXISTS normalized_size DECIMAL(10,3) NULL,
    ADD COLUMN IF NOT EXISTS normalized_unit_type VARCHAR(10) NULL,
    ADD COLUMN IF NOT EXISTS normalized_unit_price DECIMAL(10,4) NULL,
    ADD COLUMN IF NOT EXISTS product_core VARCHAR(255) NULL,
    ADD COLUMN IF NOT EXISTS normalized_at DATETIME NULL;
