from data_loader import (
    load_product_catalog,
    load_latest_prices,
    load_supermarket_names,
    load_shipping_rules
)

catalog = load_product_catalog()
prices = load_latest_prices()
supermarket_names = load_supermarket_names()
shipping_rules = load_shipping_rules()

print("CATÁLOGO:")
for key, value in catalog.items():
    print(key, "=>", value)

print("\nPRECIOS:")
for key, value in prices.items():
    print(key, "=>", value)

print("\nSUPERMERCADOS:")
print(supermarket_names)

print("\nREGLAS DE ENVÍO:")
for rule in shipping_rules:
    print(rule)