/*
 * Fuente de datos estática para la versión publicada (GitHub Pages).
 *
 * Sustituye a la API: lee data.json (generado por export_static.py) y
 * replica en JavaScript la lógica de POST /optimize de main.py:
 *   - opción "todo en una cadena" para cada cadena que tenga todos los productos
 *   - mejor combinación usando al menos 2 cadenas (búsqueda exhaustiva)
 *   - envío por cadena con la primera regla de shipping_rules que aplique
 *   - ahorro frente a la opción de una sola cadena más cara
 */
(function () {
  "use strict";

  let dataPromise = null;

  function load() {
    if (!dataPromise) {
      dataPromise = fetch("data.json", { cache: "no-cache" }).then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status} al leer data.json`);
        return res.json();
      });
    }
    return dataPromise;
  }

  const round2 = (x) => Math.round((x + Number.EPSILON) * 100) / 100;

  // Igual que el f"{valor}€" de Python: 12.0 -> "12.0", 12.86 -> "12.86"
  const py = (x) => (Number.isInteger(x) ? `${x}.0` : String(x));

  function shippingCost(rules, market, productTotal, method = "home_delivery") {
    for (const rule of rules) {
      if (rule.supermarket !== market) continue;
      if (rule.delivery_method !== method) continue;
      const min = rule.min_order_amount || 0;
      const max = rule.max_order_amount;
      if (productTotal < min) continue;
      if (max !== null && max !== undefined && productTotal > max) continue;
      return rule.shipping_cost;
    }
    return 0;
  }

  function detailLine(catalog, prices, cartItem, market) {
    const product = catalog[cartItem.product];
    const unitPrice = prices[cartItem.product][market];
    return {
      product: cartItem.product,
      product_name: product.name,
      category: product.category,
      brand_type: product.brand_type,
      quantity: cartItem.quantity,
      unit_price: round2(unitPrice),
      line_total: round2(unitPrice * cartItem.quantity),
      supermarket: market
    };
  }

  function costOfCombo(combo, cart, prices, rules) {
    let productTotal = 0;
    const subtotal = {};

    for (let i = 0; i < cart.length; i++) {
      const price = (prices[cart[i].product] || {})[combo[i]];
      if (price === null || price === undefined) return null;
      const line = price * cart[i].quantity;
      productTotal += line;
      subtotal[combo[i]] = (subtotal[combo[i]] || 0) + line;
    }

    let shippingTotal = 0;
    for (const market of Object.keys(subtotal)) {
      shippingTotal += shippingCost(rules, market, subtotal[market]);
    }

    return {
      total: productTotal + shippingTotal,
      markets: Object.keys(subtotal),
      productTotal,
      shippingTotal
    };
  }

  // Recorre las combinaciones en el mismo orden que itertools.product
  function bestMixed(cart, markets, prices, rules) {
    const n = cart.length;
    const k = markets.length;
    const idx = new Array(n).fill(0);
    let best = null;

    while (true) {
      const combo = idx.map((i) => markets[i]);
      const cost = costOfCombo(combo, cart, prices, rules);
      if (cost && cost.markets.length >= 2 && (!best || cost.total < best.cost.total)) {
        best = { combo, cost };
      }

      let pos = n - 1;
      while (pos >= 0 && idx[pos] === k - 1) {
        idx[pos] = 0;
        pos--;
      }
      if (pos < 0) break;
      idx[pos]++;
    }

    return best;
  }

  async function optimize(items) {
    const data = await load();
    const catalog = Object.fromEntries(data.catalog.map((p) => [p.id, p]));
    const prices = data.prices.prices;
    const rules = data.shipping_rules || [];
    const markets = data.prices.supermarkets.map((m) => m.slug);
    const names = Object.fromEntries(data.prices.supermarkets.map((m) => [m.slug, m.name]));
    const marketName = (slug) => names[slug] || slug;

    const cart = [];
    for (const item of items) {
      const id = String(item.product_id || "").toLowerCase().trim();
      if (catalog[id]) cart.push({ product: id, quantity: Math.max(1, item.quantity || 1) });
    }

    if (!cart.length) return { error: "No se pudieron identificar productos" };

    // Opciones simples: toda la cesta en cada supermercado
    const singleOptions = [];
    for (const market of markets) {
      const cost = costOfCombo(cart.map(() => market), cart, prices, rules);
      if (!cost) continue;
      singleOptions.push({
        type: "single",
        supermarkets: [market],
        product_total: round2(cost.productTotal),
        shipping_total: round2(cost.shippingTotal),
        total_cost: round2(cost.total),
        details: cart.map((c) => detailLine(catalog, prices, c, market))
      });
    }

    if (!singleOptions.length) return { error: "No hay precios disponibles para esos productos" };

    const bestSingle = singleOptions.reduce((a, b) => (b.total_cost < a.total_cost ? b : a));
    const worstSingle = singleOptions.reduce((a, b) => (b.total_cost > a.total_cost ? b : a));

    // Mejor combinación usando al menos 2 cadenas
    const mixed = cart.length >= 2 ? bestMixed(cart, markets, prices, rules) : null;
    let bestMixedOption = null;

    if (mixed) {
      bestMixedOption = {
        type: "mixed",
        supermarkets: markets.filter((m) => mixed.cost.markets.includes(m)),
        product_total: round2(mixed.cost.productTotal),
        shipping_total: round2(mixed.cost.shippingTotal),
        total_cost: round2(mixed.cost.total),
        details: cart.map((c, i) => detailLine(catalog, prices, c, mixed.combo[i]))
      };
    }

    const allOptions = singleOptions.slice();
    if (bestMixedOption) allOptions.push(bestMixedOption);
    allOptions.sort((a, b) => a.total_cost - b.total_cost);

    const bestOption = allOptions[0];
    const savings = round2(worstSingle.total_cost - bestOption.total_cost);

    const bestSingleName = marketName(bestSingle.supermarkets[0]);
    let explanation;

    if (bestMixedOption) {
      const diff = round2(bestMixedOption.total_cost - bestSingle.total_cost);
      explanation = bestOption.type === "mixed"
        ? `La mejor opción es combinar supermercados. Comprar todo en ${bestSingleName} cuesta ` +
          `${py(bestSingle.total_cost)}€, mientras que la combinación óptima cuesta ` +
          `${py(bestMixedOption.total_cost)}€. En este caso, el ahorro en productos compensa los costes adicionales.`
        : `La mejor opción es comprar todo en ${bestSingleName} por ${py(bestSingle.total_cost)}€. ` +
          `La mejor combinación entre supermercados cuesta ${py(bestMixedOption.total_cost)}€, ` +
          `es decir, ${py(diff)}€ más. Por eso no recomendamos dividir la compra: ` +
          `los costes de envío/logística eliminan el ahorro potencial.`;
    } else {
      explanation = "No se ha encontrado una combinación válida entre varios supermercados. " +
        "Recomendamos comprar toda la cesta en el supermercado más barato disponible.";
    }

    const suggestions = [];
    if (bestOption.type === "single") {
      suggestions.push(`Compra toda la cesta en ${marketName(bestOption.supermarkets[0])}.`);
      if (bestMixedOption) {
        const diff = round2(bestMixedOption.total_cost - bestSingle.total_cost);
        suggestions.push(diff > 0
          ? `No recomendamos dividir la compra porque la mejor combinación cuesta ${py(diff)}€ más.`
          : "La opción combinada no mejora la compra completa en un único supermercado.");
      }
    } else {
      for (const line of bestOption.details) {
        suggestions.push(`${line.product_name} x${line.quantity} → ${marketName(line.supermarket)} (${py(line.line_total)}€)`);
      }
    }

    return {
      best_option: bestOption,
      best_single_option: bestSingle,
      best_mixed_option: bestMixedOption,
      all_options: allOptions,
      savings,
      explanation,
      suggestions
    };
  }

  window.SO_DATA_SOURCE = {
    catalog: () => load().then((d) => d.catalog),
    prices: () => load().then((d) => d.prices),
    alternatives: () => load().then((d) => d.alternatives),
    stats: () => load().then((d) => d.stats),
    meta: () => load().then((d) => ({ generated_at: d.generated_at })),
    optimize
  };
})();
