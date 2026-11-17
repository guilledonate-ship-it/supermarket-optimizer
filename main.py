from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
import itertools

from data_loader import (
    load_product_catalog,
    load_latest_prices,
    load_shipping_rules,
    load_supermarket_names
)

app = FastAPI(title="Supermarket Optimizer API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CartItem(BaseModel):
    product_id: str
    quantity: int = 1


class OptimizeRequest(BaseModel):
    items: List[CartItem]


def match_product(user_input: str, product_catalog):
    """
    Convierte lo que llega desde el frontend en un product_id válido.
    Ahora acepta IDs reales cargados desde MySQL.
    """
    user_input = user_input.lower().strip()

    if user_input in product_catalog:
        return user_input

    return None


def get_shipping_cost(
    supermarket,
    product_total,
    shipping_rules,
    delivery_method="home_delivery",
    postal_code=None
):
    """
    Calcula el coste de envío aplicable para un supermercado según:
    - supermercado
    - método de entrega
    - código postal si aplica
    - importe del pedido en ese supermercado
    """

    applicable_rules = []

    for rule in shipping_rules:
        if rule["supermarket"] != supermarket:
            continue

        if rule["delivery_method"] != delivery_method:
            continue

        # Si en el futuro usamos CP, priorizaremos reglas específicas
        if rule["postal_code"] is not None and postal_code is not None:
            if rule["postal_code"] != postal_code:
                continue

        min_amount = rule["min_order_amount"] or 0
        max_amount = rule["max_order_amount"]

        if product_total < min_amount:
            continue

        if max_amount is not None and product_total > max_amount:
            continue

        applicable_rules.append(rule)

    if not applicable_rules:
        # Fallback provisional si no hay regla
        return 0.0

    # Si hay varias, cogemos la más específica / primera válida
    selected_rule = applicable_rules[0]

    return selected_rule["shipping_cost"]


def calculate_cost(combo, cart_items, prices, shipping_rules):
    """
    Calcula coste total:
    productos + envío/logística por supermercado usado.
    """

    product_total = 0
    used_supermarkets = set()
    subtotal_by_market = {}

    for cart_item, market in zip(cart_items, combo):
        product = cart_item["product"]
        quantity = cart_item["quantity"]

        unit_price = prices[product][market]
        line_total = unit_price * quantity

        product_total += line_total
        used_supermarkets.add(market)

        if market not in subtotal_by_market:
            subtotal_by_market[market] = 0

        subtotal_by_market[market] += line_total

    shipping_total = 0

    for market in used_supermarkets:
        shipping_total += get_shipping_cost(
            supermarket=market,
            product_total=subtotal_by_market[market],
            shipping_rules=shipping_rules
        )

    total = product_total + shipping_total

    return total, used_supermarkets, product_total, shipping_total


def optimize_cart(cart_items, prices, shipping_rules):
    supermarkets = list(load_supermarket_names().keys())

    best_cost = float("inf")
    best_combo = None
    best_markets = None
    best_product_total = None
    best_shipping_total = None

    combinations = list(itertools.product(supermarkets, repeat=len(cart_items)))

    for combo in combinations:
        try:
            cost, markets, product_total, shipping_total = calculate_cost(
                combo,
                cart_items,
                prices,
                shipping_rules
            )

            if cost < best_cost:
                best_cost = cost
                best_combo = combo
                best_markets = markets
                best_product_total = product_total
                best_shipping_total = shipping_total

        except KeyError:
            continue

    return best_cost, best_combo, best_markets, best_product_total, best_shipping_total


def optimize_mixed_cart(cart_items, prices, shipping_rules):
    """
    Busca la mejor combinación usando al menos 2 supermercados.
    Sirve para comparar si realmente compensa dividir la compra.
    """

    supermarkets = list(load_supermarket_names().keys())

    best_cost = float("inf")
    best_combo = None
    best_markets = None
    best_product_total = None
    best_shipping_total = None

    combinations = list(itertools.product(supermarkets, repeat=len(cart_items)))

    for combo in combinations:
        try:
            cost, markets, product_total, shipping_total = calculate_cost(
                combo,
                cart_items,
                prices,
                shipping_rules
            )

            if len(markets) < 2:
                continue

            if cost < best_cost:
                best_cost = cost
                best_combo = combo
                best_markets = markets
                best_product_total = product_total
                best_shipping_total = shipping_total

        except KeyError:
            continue

    return best_cost, best_combo, best_markets, best_product_total, best_shipping_total

@app.post("/optimize")
def optimize(data: OptimizeRequest):

    # 1. Cargar datos desde MySQL
    product_catalog = load_product_catalog()
    prices = load_latest_prices()
    shipping_rules = load_shipping_rules()
    supermarket_names = load_supermarket_names()

    user_items = data.items

    # 2. Matching productos + cantidades
    cart_items = []

    for item in user_items:
        product_id = match_product(item.product_id, product_catalog)

        if product_id:
            quantity = max(1, item.quantity)

            cart_items.append({
                "product": product_id,
                "quantity": quantity
            })

    if not cart_items:
        return {"error": "No se pudieron identificar productos"}

    supermarkets = list(supermarket_names.keys())

    # 3. Opciones simples: toda la cesta en cada supermercado
    single_options = []

    for market in supermarkets:
        try:
            product_total = 0
            details = []

            for cart_item in cart_items:
                product = cart_item["product"]
                quantity = cart_item["quantity"]

                unit_price = prices[product][market]
                line_total = unit_price * quantity

                product_total += line_total

                details.append({
                    "product": product,
                    "product_name": product_catalog[product]["name"],
                    "category": product_catalog[product]["category"],
                    "brand_type": product_catalog[product]["brand_type"],
                    "quantity": quantity,
                    "unit_price": round(unit_price, 2),
                    "line_total": round(line_total, 2),
                    "supermarket": market
                })

            shipping_total = get_shipping_cost(
                supermarket=market,
                product_total=product_total,
                shipping_rules=shipping_rules
            )

            total = product_total + shipping_total

            single_options.append({
                "type": "single",
                "supermarkets": [market],
                "product_total": round(product_total, 2),
                "shipping_total": round(shipping_total, 2),
                "total_cost": round(total, 2),
                "details": details
            })

        except KeyError:
            continue

    if not single_options:
        return {"error": "No hay precios disponibles para esos productos"}

    # 4. Mejor opción simple
    best_single_option = min(single_options, key=lambda x: x["total_cost"])
    worst_single_option = max(single_options, key=lambda x: x["total_cost"])

    # 5. Mejor opción combinada obligatoria
    mixed_cost, mixed_combo, mixed_markets, mixed_product_total, mixed_shipping_total = optimize_mixed_cart(
        cart_items,
        prices,
        shipping_rules
    )

    best_mixed_option = None

    if mixed_combo is not None:
        mixed_details = []

        for cart_item, market in zip(cart_items, mixed_combo):
            product = cart_item["product"]
            quantity = cart_item["quantity"]

            unit_price = prices[product][market]
            line_total = unit_price * quantity

            mixed_details.append({
                "product": product,
                "product_name": product_catalog[product]["name"],
                "category": product_catalog[product]["category"],
                "brand_type": product_catalog[product]["brand_type"],
                "quantity": quantity,
                "unit_price": round(unit_price, 2),
                "line_total": round(line_total, 2),
                "supermarket": market
            })

        best_mixed_option = {
            "type": "mixed",
            "supermarkets": list(mixed_markets),
            "product_total": round(mixed_product_total, 2),
            "shipping_total": round(mixed_shipping_total, 2),
            "total_cost": round(mixed_cost, 2),
            "details": mixed_details
        }

    # 6. Todas las opciones
    all_options = single_options.copy()

    if best_mixed_option:
        all_options.append(best_mixed_option)

    all_options = sorted(all_options, key=lambda x: x["total_cost"])

    best_option = all_options[0]

    # 7. Ahorro frente al supermercado más caro
    savings = round(worst_single_option["total_cost"] - best_option["total_cost"], 2)

    # 8. Explicación
    if best_mixed_option:
        difference_vs_single = round(
            best_mixed_option["total_cost"] - best_single_option["total_cost"],
            2
        )

        best_single_market = best_single_option["supermarkets"][0]
        best_single_market_name = supermarket_names.get(best_single_market, best_single_market)

        if best_option["type"] == "mixed":
            explanation = (
                f"La mejor opción es combinar supermercados. "
                f"Comprar todo en {best_single_market_name} cuesta "
                f"{best_single_option['total_cost']}€, mientras que la combinación óptima cuesta "
                f"{best_mixed_option['total_cost']}€. "
                f"En este caso, el ahorro en productos compensa los costes adicionales."
            )
        else:
            explanation = (
                f"La mejor opción es comprar todo en {best_single_market_name} "
                f"por {best_single_option['total_cost']}€. "
                f"La mejor combinación entre supermercados cuesta {best_mixed_option['total_cost']}€, "
                f"es decir, {difference_vs_single}€ más. "
                f"Por eso no recomendamos dividir la compra: los costes de envío/logística eliminan el ahorro potencial."
            )
    else:
        explanation = (
            "No se ha encontrado una combinación válida entre varios supermercados. "
            "Recomendamos comprar toda la cesta en el supermercado más barato disponible."
        )

    # 9. Recomendaciones
    suggestions = []

    if best_option["type"] == "single":
        best_market = best_option["supermarkets"][0]
        best_market_name = supermarket_names.get(best_market, best_market)

        suggestions.append(
            f"Compra toda la cesta en {best_market_name}."
        )

        if best_mixed_option:
            difference_vs_single = round(
                best_mixed_option["total_cost"] - best_single_option["total_cost"],
                2
            )

            if difference_vs_single > 0:
                suggestions.append(
                    f"No recomendamos dividir la compra porque la mejor combinación cuesta {difference_vs_single}€ más."
                )
            else:
                suggestions.append(
                    "La opción combinada no mejora la compra completa en un único supermercado."
                )

    else:
        for item in best_option["details"]:
            suggestions.append(
                f"{item['product_name']} x{item['quantity']} → {supermarket_names.get(item['supermarket'], item['supermarket'])} "
                f"({item['line_total']}€)"
            )

    # 10. Respuesta final
    return {
        "best_option": best_option,
        "best_single_option": best_single_option,
        "best_mixed_option": best_mixed_option,
        "all_options": all_options,
        "savings": savings,
        "explanation": explanation,
        "suggestions": suggestions
    }

@app.get("/catalog")
def get_catalog():
    product_catalog = load_product_catalog()

    products = []

    for product_id, data in product_catalog.items():
        products.append({
            "id": product_id,
            "name": data["name"],
            "category": data["category"],
            "brand_type": data["brand_type"],
            "brand": data["brand"],
            "unit_size": data["unit_size"],
            "unit_type": data["unit_type"],
            "equivalence_group": data["equivalence_group"]
        })

    return products

@app.get("/")
def root():
    return {"message": "API funcionando con MySQL ✅"}


# ============================================================
# ENDPOINTS DEL MVP DE PORTFOLIO (solo lectura)
# Ver portfolio_queries.py. No alteran /catalog ni /optimize.
# ============================================================

from portfolio_queries import get_prices, get_all_alternatives, get_stats


@app.get("/prices")
def prices():
    """Último precio de cada producto en cada cadena con precios (null si no está)."""
    return get_prices()


@app.get("/alternatives")
def alternatives_all():
    """Formatos más baratos por L/kg de la misma marca, para todo el catálogo."""
    return get_all_alternatives()


@app.get("/alternatives/{product_key}")
def alternatives_for_product(product_key: str):
    """Formatos más baratos por L/kg para un producto concreto."""
    data = get_all_alternatives().get(product_key)

    if data is None:
        return {"product_key": product_key, "reference": None, "alternatives": []}

    return {"product_key": product_key, **data}


@app.get("/stats")
def stats():
    """Métricas del pipeline: scraping, normalización, matching y actualización."""
    return get_stats()
