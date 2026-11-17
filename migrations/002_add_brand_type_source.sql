-- Añade brand_type_source a raw_scraped_products.
--
-- La rellena normalizer.py (función apply_normalization_to_db) junto con
-- brand/brand_type. Indica de dónde salió la clasificación de brand_type
-- para cada producto:
--   "dia_json"   -> item_brand_type del JSON de dia.es era "d" o "n"
--   "sin_marca"  -> item_brand del JSON de dia.es era "sin marca" (o
--                   equivalente); brand_type = "generico"
--   "heuristica" -> item_brand_type era "r", otro valor no reconocido, o
--                   no había datos del JSON (fallback HTML, u otro
--                   supermercado); brand_type sale de PRIVATE_LABEL_BRANDS
--
-- NULLABLE: un producto sin normalizar todavía simplemente tiene este
-- campo a NULL, igual que el resto de columnas de 001.
--
-- Compatible con MySQL 8.0 (usa ADD COLUMN IF NOT EXISTS para poder
-- reejecutar el script sin que falle si ya se aplicó).

ALTER TABLE raw_scraped_products
    ADD COLUMN IF NOT EXISTS brand_type_source VARCHAR(20) NULL;
