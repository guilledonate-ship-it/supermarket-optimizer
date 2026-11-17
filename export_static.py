"""
Exporta la versión estática del comparador para publicarla sin backend
(GitHub Pages).

Genera la carpeta docs/ con:
  - data.json            catálogo, precios, alternativas de formato,
                         métricas del pipeline y reglas de envío
  - static_datasource.js lógica de /optimize replicada en JavaScript
  - index.html           el mismo index.html del proyecto, conectado a
                         data.json en lugar de a la API
  - .nojekyll            para que GitHub Pages sirva los archivos tal cual

Solo lee de MySQL (reutiliza data_loader.py y portfolio_queries.py).

Uso:
    python export_static.py

Vista previa local (fetch no funciona abriendo el archivo con doble clic):
    python -m http.server 8080 --directory docs
    -> http://127.0.0.1:8080
"""

import json
import shutil
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from data_loader import load_product_catalog, load_shipping_rules
from portfolio_queries import get_all_alternatives, get_prices, get_stats


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "docs"
MARKER = "<!-- STATIC_DATA_SOURCE -->"


def _json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"No serializable: {type(value).__name__}")


def build_catalog():
    """Mismo formato que GET /catalog."""
    return [
        {
            "id": product_id,
            "name": data["name"],
            "category": data["category"],
            "brand_type": data["brand_type"],
            "brand": data["brand"],
            "unit_size": data["unit_size"],
            "unit_type": data["unit_type"],
            "equivalence_group": data["equivalence_group"]
        }
        for product_id, data in load_product_catalog().items()
    ]


def build_shipping_rules(priced_slugs):
    """Solo las reglas de cadenas con precios; se conserva el orden de MySQL
    porque /optimize aplica la primera regla que encaja."""
    return [
        rule for rule in load_shipping_rules()
        if rule["supermarket"] in priced_slugs
    ]


def main():
    prices = get_prices()
    priced_slugs = {market["slug"] for market in prices["supermarkets"]}

    data = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "catalog": build_catalog(),
        "prices": prices,
        "alternatives": get_all_alternatives(),
        "stats": get_stats(),
        "shipping_rules": build_shipping_rules(priced_slugs)
    }

    OUTPUT_DIR.mkdir(exist_ok=True)

    (OUTPUT_DIR / "data.json").write_text(
        json.dumps(data, ensure_ascii=False, default=_json_default, separators=(",", ":")),
        encoding="utf-8"
    )

    shutil.copyfile(ROOT / "static_datasource.js", OUTPUT_DIR / "static_datasource.js")

    html = (ROOT / "index.html").read_text(encoding="utf-8")

    if MARKER not in html:
        raise SystemExit(f"No se encuentra {MARKER} en index.html: no se puede generar la versión estática.")

    html = html.replace(MARKER, '<script src="static_datasource.js"></script>')
    (OUTPUT_DIR / "index.html").write_text(html, encoding="utf-8")

    (OUTPUT_DIR / ".nojekyll").write_text("", encoding="utf-8")

    size_kb = (OUTPUT_DIR / "data.json").stat().st_size / 1024
    print("Versión estática generada en", OUTPUT_DIR)
    print(f"  productos en catálogo: {len(data['catalog'])}")
    print(f"  cadenas con precios:   {', '.join(sorted(priced_slugs))}")
    print(f"  productos con formato alternativo: {len(data['alternatives'])}")
    print(f"  reglas de envío:       {len(data['shipping_rules'])}")
    print(f"  precios del:           {data['stats'].get('last_updated')}")
    print(f"  data.json:             {size_kb:.1f} KB")


if __name__ == "__main__":
    main()
