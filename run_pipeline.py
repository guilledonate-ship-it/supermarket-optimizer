"""
Orquesta el pipeline completo: scraping -> normalizacion -> promocion.

Pasos, en orden:
    1. scrapers/scraper_dia_catalog.py
    2. scrapers/scraper_eroski_catalog.py
    3. normalizer.py --apply
    4. promoter.py --apply

Cada paso se ejecuta como proceso aparte (subprocess), tal cual se
ejecutaria a mano desde la terminal - este archivo no importa ni
modifica el codigo de ninguno de los cuatro scripts, solo los llama.

Si un paso termina con codigo de salida distinto de 0 (scraper
bloqueado, normalizer o promoter con un error, etc.), el pipeline se
detiene ahi mismo: no tiene sentido normalizar si el scraping fallo,
ni promover si la normalizacion fallo.

--dry-run: los scrapers se ejecutan igual (no tienen modo dry-run
propio, y raw_scraped_products es una tabla de staging), pero
normalizer.py y promoter.py se llaman SIN --apply, en su propio
modo de solo lectura.
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_step(name, script_args):
    """
    Ejecuta un paso del pipeline como subprocess (python -u <script> [args]).

    -u fuerza salida sin buffer en el proceso hijo, para que si se
    redirige la salida de run_pipeline.py a un archivo (p. ej. para
    vigilarlo con un monitor en tiempo real) las lineas de los
    scrapers/normalizer/promoter no se queden atascadas en el buffer
    de Python hasta que el proceso termine.

    Devuelve (ok, elapsed_seconds): ok=True si el proceso salio con
    codigo 0.
    """

    print("=" * 100)
    print("PASO:", name)
    print("Comando:", " ".join(str(part) for part in script_args))
    print("Inicio:", _now())
    print("=" * 100)

    start = time.monotonic()

    result = subprocess.run(
        [sys.executable, "-u"] + [str(part) for part in script_args],
        cwd=str(PROJECT_ROOT)
    )

    elapsed = time.monotonic() - start

    print("-" * 100)
    print(
        "Fin:", _now(),
        "| duracion:", f"{elapsed:.1f}s",
        "| codigo de salida:", result.returncode
    )
    print("-" * 100)

    return result.returncode == 0, elapsed


def print_summary(step_times, total_elapsed, dry_run):
    """
    Resumen final: productos por cadena, cuantos sin normalizar,
    grupos de equivalencia exacta y productos maestros.

    Importa db.py y matcher.py directamente (no como subprocess) para
    leer el estado actual - no modifica ninguno de los dos, solo usa
    sus funciones publicas ya existentes.
    """

    sys.path.insert(0, str(PROJECT_ROOT))

    from db import get_connection
    from matcher import fetch_normalized_products, find_exact_matches

    connection = get_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                s.slug,
                COUNT(*) AS total,
                SUM(
                    CASE WHEN r.normalized_unit_type IS NULL THEN 1 ELSE 0 END
                ) AS sin_normalizar
            FROM raw_scraped_products r
            JOIN supermarkets s ON s.id = r.supermarket_id
            WHERE s.slug IN ('dia', 'eroski')
            GROUP BY s.slug
            ORDER BY s.slug;
            """
        )

        per_chain = cursor.fetchall()

        cursor.execute("SELECT COUNT(*) AS c FROM products;")

        products_total = cursor.fetchone()["c"]

    finally:
        cursor.close()
        connection.close()

    products = fetch_normalized_products(("dia", "eroski"))
    match_groups, _ = find_exact_matches(products)

    print()
    print("=" * 100)

    title = "RESUMEN DEL PIPELINE"

    if dry_run:
        title += " (DRY-RUN: normalizer y promoter no escribieron nada)"

    print(title)
    print("=" * 100)

    for name, elapsed, ok in step_times:
        print(
            f"  {name:20} | {elapsed:8.1f}s | "
            + ("OK" if ok else "FALLO")
        )

    print(
        "Tiempo total:", f"{total_elapsed:.1f}s",
        f"({total_elapsed / 60:.1f} min)"
    )
    print("-" * 100)

    for row in per_chain:
        print(
            f"  {row['slug']:8} -> productos: {row['total']:5} | "
            f"sin normalizar: {row['sin_normalizar']}"
        )

    print(
        "  Grupos de equivalencia exacta (dia/eroski):",
        len(match_groups)
    )
    print("  Productos maestros en 'products':", products_total)
    print("=" * 100)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Orquesta scraping (DIA, Eroski) + normalizer.py + "
            "promoter.py, en ese orden. Para en el primer paso que "
            "falle."
        )
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Ejecuta los scrapers igualmente (no tienen modo dry-run "
            "propio), pero llama a normalizer.py y promoter.py sin "
            "--apply, en modo de solo lectura."
        )
    )

    args = parser.parse_args()

    apply_flag = [] if args.dry_run else ["--apply"]

    steps = [
        (
            "Scraper DIA",
            [PROJECT_ROOT / "scrapers" / "scraper_dia_catalog.py"]
        ),
        (
            "Scraper Eroski",
            [PROJECT_ROOT / "scrapers" / "scraper_eroski_catalog.py"]
        ),
        (
            "Normalizer",
            [PROJECT_ROOT / "normalizer.py"] + apply_flag
        ),
        (
            "Promoter",
            [PROJECT_ROOT / "promoter.py"] + apply_flag
        ),
    ]

    print("#" * 100)
    print("PIPELINE COMPLETO -", "DRY-RUN" if args.dry_run else "APPLY")
    print("Inicio:", _now())
    print("#" * 100)

    pipeline_start = time.monotonic()
    step_times = []

    for name, script_args in steps:
        ok, elapsed = run_step(name, script_args)
        step_times.append((name, elapsed, ok))

        if not ok:
            total_elapsed = time.monotonic() - pipeline_start

            print()
            print("!" * 100)
            print(
                "PIPELINE DETENIDO: el paso '" + name + "' termino con "
                "un codigo de salida distinto de 0."
            )
            print("No se ejecutan los pasos siguientes.")
            print(
                "Tiempo transcurrido hasta el fallo:",
                f"{total_elapsed:.1f}s"
            )
            print("!" * 100)

            sys.exit(1)

    total_elapsed = time.monotonic() - pipeline_start

    print_summary(step_times, total_elapsed, args.dry_run)


if __name__ == "__main__":
    main()
