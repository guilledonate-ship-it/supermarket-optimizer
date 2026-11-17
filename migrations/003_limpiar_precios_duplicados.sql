-- Limpia duplicados exactos en `prices` creados el 2026-08-28 al
-- reejecutar `promoter.py --apply` (para el fix de category_id)
-- antes de que existiera la comprobación de duplicados
-- (_price_already_exists_today en promoter.py). A partir de ahora
-- promoter.py ya no vuelve a crear este tipo de duplicado.
--
-- Duplicado = mismas product_offer_id, price y fecha
-- (DATE(scraped_at)). Se conserva la fila de id más bajo de cada
-- grupo (la más antigua) y se borran las demás.
--
-- NO se ha ejecutado. Revisar el resultado del SELECT antes de
-- correr el DELETE: en el momento de escribir esto había 48 filas de
-- sobra (48 grupos con exactamente 2 filas idénticas cada uno).

-- ============================================================
-- 1) Verificación: cuántas filas se borrarían
--    (debe dar 48 - si da un número distinto, parar y revisar antes
--    de seguir)
-- ============================================================

SELECT COUNT(DISTINCT p1.id) AS filas_a_borrar
FROM prices p1
INNER JOIN prices p2
    ON p1.product_offer_id = p2.product_offer_id
   AND p1.price = p2.price
   AND DATE(p1.scraped_at) = DATE(p2.scraped_at)
   AND p1.id > p2.id;

-- ============================================================
-- 2) Borrado: solo ejecutar después de comprobar el SELECT anterior
-- ============================================================

DELETE p1
FROM prices p1
INNER JOIN prices p2
    ON p1.product_offer_id = p2.product_offer_id
   AND p1.price = p2.price
   AND DATE(p1.scraped_at) = DATE(p2.scraped_at)
   AND p1.id > p2.id;
