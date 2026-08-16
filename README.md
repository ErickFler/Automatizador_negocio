# Sistema de Concentrado de Ventas

Aplicación web local para construir un histórico confiable de ventas de cuatro
sucursales: Loma Linda, Solidaridad, Alianza y Unidad.

## Estado

Las **Etapas 0 y 1** están activas:

- fundación FastAPI, Jinja2, SQLite, SQLAlchemy, logging y pytest;
- sucursales persistentes con seed idempotente;
- categorías maestras;
- productos maestros independientes de los IDs externos;
- equivalencias de producto por sucursal;
- página de inspección del catálogo en `/products`.

La carga de Excel, cálculos, consolidación, exportación, launcher y empaquetado
permanecen fuera del flujo activo hasta sus etapas correspondientes. Algunos
archivos del prototipo se conservan congelados como referencia.

## Requisitos

- Python 3.12 o superior

## Instalación

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

La configuración disponible está documentada en `.env.example`.

## Ejecución

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Abrir <http://127.0.0.1:8000>. El diagnóstico está disponible en
<http://127.0.0.1:8000/health> y el catálogo en
<http://127.0.0.1:8000/products>.

La base de desarrollo se crea en `data/app.db` y los logs se escriben en
`data/logs/app.log`. Ambos son archivos locales ignorados por Git.

## Pruebas

```bash
pytest
```

Las pruebas de servicios pertenecientes a etapas futuras se mantienen congeladas
en sus archivos hasta adaptar esos servicios a la arquitectura V2.

## Modelo de productos

`MasterProduct` representa el producto universal y no contiene un ID externo.
`BranchProduct` conserva el ID y los nombres recibidos dentro de una sucursal y
se relaciona con un producto maestro. La combinación `branch_id` más
`external_product_id` es única.

Un producto maestro puede tener múltiples equivalencias de distintas sucursales.
