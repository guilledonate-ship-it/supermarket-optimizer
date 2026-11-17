-- ============================================================================
-- 004: Fusionar duplicado Oroweat (producto 28 vs producto 75)
--
-- Contexto: ambos productos apuntan a las MISMAS ofertas reales (mismo
-- external_product_id en dia y en eroski). Se separaron en dos filas de
-- `products` porque product_key deriva de product_core, y este cambio de
-- texto por singular/plural ("cereales_y_semillas" vs "cereale_y_semilla")
-- produjo dos claves distintas para el mismo producto.
--
-- Detectado por la reconciliacion por SKU de promoter.py (ver informe:
-- "oroweat--pan_de_molde_12_cereales_y_semillas--0_55kg" sin tocar en la
-- ultima pasada). Verificado manualmente: es el UNICO caso de SKU
-- compartido por mas de un producto en toda la base (escaneadas las 130
-- filas de product_offers).
--
-- Decision: conservar el producto 75 (product_key con singularizacion
-- aplicada, el criterio vigente en matcher.py) y eliminar el 28, moviendo
-- antes su historico de precios al 75 para no perder datos.
--
-- No se ejecuta automaticamente. Revisar el resultado del SELECT inicial
-- antes de lanzar el resto en Workbench.
-- ============================================================================

-- --------------------------------------------------------------------------
-- PASO 0: verificacion previa -- revisa esto antes de seguir
-- --------------------------------------------------------------------------
SELECT
    p.id            AS product_id,
    p.product_key,
    p.name,
    po.id           AS offer_id,
    po.supermarket_id,
    po.external_product_id,
    pr.id           AS price_id,
    pr.price,
    pr.scraped_at
FROM products p
JOIN product_offers po ON po.product_id = p.id
LEFT JOIN prices pr ON pr.product_offer_id = po.id
WHERE p.id IN (28, 75)
ORDER BY p.id, po.supermarket_id, pr.scraped_at;


-- --------------------------------------------------------------------------
-- PASO 1: mover el historico de precios de las ofertas del producto 28
-- a las ofertas equivalentes (mismo supermarket_id) del producto 75.
-- Se salta cualquier precio que ya exista en destino para el mismo dia
-- y mismo importe, para no crear duplicados en el historico.
-- --------------------------------------------------------------------------
START TRANSACTION;

INSERT INTO prices (product_offer_id, price, price_per_unit, currency, data_source, scraped_at)
SELECT
    dest.id,
    src_price.price,
    src_price.price_per_unit,
    src_price.currency,
    src_price.data_source,
    src_price.scraped_at
FROM prices src_price
JOIN product_offers src_offer
    ON src_offer.id = src_price.product_offer_id
    AND src_offer.product_id = 28
JOIN product_offers dest
    ON dest.product_id = 75
    AND dest.supermarket_id = src_offer.supermarket_id
WHERE NOT EXISTS (
    SELECT 1
    FROM prices existing
    WHERE existing.product_offer_id = dest.id
        AND existing.price = src_price.price
        AND DATE(existing.scraped_at) = DATE(src_price.scraped_at)
);

-- --------------------------------------------------------------------------
-- PASO 2: verificacion de la comprobacion de seguridad -- debe devolver 0
-- filas (todo el historico de precios del producto 28 debe seguir
-- presente, ya sea en su forma original o ya migrado al 75). Si esto
-- devuelve filas, PARA aqui y no sigas al ROLLBACK/COMMIT sin revisar.
-- --------------------------------------------------------------------------
SELECT src_price.*
FROM prices src_price
JOIN product_offers src_offer
    ON src_offer.id = src_price.product_offer_id
    AND src_offer.product_id = 28
JOIN product_offers dest
    ON dest.product_id = 75
    AND dest.supermarket_id = src_offer.supermarket_id
WHERE NOT EXISTS (
    SELECT 1
    FROM prices existing
    WHERE existing.product_offer_id = dest.id
        AND existing.price = src_price.price
        AND DATE(existing.scraped_at) = DATE(src_price.scraped_at)
);

-- --------------------------------------------------------------------------
-- PASO 3: eliminar el historico de precios y las ofertas del producto 28
-- (ya migrado o ya duplicado con lo que tiene el 75), y despues el
-- producto 28 en si.
-- --------------------------------------------------------------------------
DELETE pr FROM prices pr
JOIN product_offers po ON po.id = pr.product_offer_id
WHERE po.product_id = 28;

DELETE FROM product_offers WHERE product_id = 28;

DELETE FROM products WHERE id = 28;

COMMIT;
-- Si algo se ve mal en el PASO 2, usa ROLLBACK; en vez de COMMIT;

-- --------------------------------------------------------------------------
-- PASO 4: verificacion final
-- --------------------------------------------------------------------------
SELECT id, product_key FROM products WHERE id IN (28, 75);

SELECT
    po.id AS offer_id, po.product_id, po.supermarket_id, po.external_product_id,
    pr.id AS price_id, pr.price, pr.scraped_at
FROM product_offers po
LEFT JOIN prices pr ON pr.product_offer_id = po.id
WHERE po.product_id = 75
ORDER BY po.supermarket_id, pr.scraped_at;
