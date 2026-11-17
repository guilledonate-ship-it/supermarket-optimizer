# Supermarket Optimizer

Comparador de cestas de la compra entre supermercados españoles (DIA y Eroski). Haces tu lista y te dice en qué cadena sale más barata la compra completa, con los gastos de envío incluidos.

**Demo:** https://guilledonate-ship-it.github.io/supermarket-optimizer/

## Qué hay detrás

Un pipeline de datos completo, de la web de cada supermercado a la recomendación:

| Etapa | Qué hace | Código |
|---|---|---|
| Extracción | Scrapers que leen las APIs internas de cada tienda online, con paginación, reintentos con backoff exponencial y pausas aleatorias. ~3.600 productos. | `scrapers/` |
| Normalización | Extrae marca, tipo de marca (fabricante / marca blanca) y formato, y convierte todo a L o kg para comparar precio por unidad. | `normalizer.py` |
| Matching | Empareja como equivalentes solo los productos con la misma marca, tipo de marca, tamaño y descripción. 65 productos presentes en ambas cadenas. | `matcher.py`, `promoter.py` |
| Optimización | Prueba todas las formas de repartir la cesta entre cadenas y aplica las reglas de envío de cada una. | `main.py` (`POST /optimize`) |
| Publicación | Exporta los datos a JSON y genera una versión estática de la web, sin servidor. | `export_static.py` |

Todo el pipeline se ejecuta con `python run_pipeline.py`.

## Stack

Python · MySQL · FastAPI · HTML, CSS y JavaScript sin frameworks · GitHub Pages

## API

| Endpoint | Descripción |
|---|---|
| `GET /catalog` | Catálogo de productos comparables |
| `POST /optimize` | Mejor forma de comprar una cesta, con envío |
| `GET /prices` | Último precio de cada producto en cada cadena |
| `GET /alternatives` | Formatos de la misma marca más baratos por L/kg |
| `GET /stats` | Métricas del pipeline |

## Ejecutarlo en local

```
pip install fastapi uvicorn mysql-connector-python python-dotenv requests
uvicorn main:app --reload
```

Requiere una base de datos MySQL con el esquema del proyecto y un archivo `.env` con `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD` y `DB_NAME`. Después, abre `index.html` en el navegador.

Para regenerar la versión estática: `python export_static.py` (se crea en `docs/`).

## Estado de los datos

Los precios publicados son del 28 de agosto de 2026. Desde noviembre de 2026, DIA responde con 403 a las peticiones automatizadas, tanto en su API como en su web, por lo que la demo usa la última extracción completa de ambas cadenas para que la comparación sea coherente.
