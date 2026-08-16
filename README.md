# Sistema de Concentrado de Ventas

Aplicación web local para construir un histórico confiable de ventas de cuatro
sucursales: Loma Linda, Solidaridad, Alianza y Unidad.

## Estado

Las **Etapas 0, 1, 2 y 3** están activas:

- fundación FastAPI, Jinja2, SQLite, SQLAlchemy, logging y pytest;
- sucursales persistentes con seed idempotente;
- categorías maestras;
- productos maestros independientes de los IDs externos;
- equivalencias de producto por sucursal;
- página de inspección del catálogo en `/products`.
- carga manual de reportes `.xlsx` y `.xlsm` en `/imports/new`;
- preservación del archivo original, SHA-256 y todas las filas crudas;
- incidencias básicas de estructura y contenido;
- detección de duplicados por fecha, sucursal y archivo;
- historial mínimo de cargas en `/imports`.
- detección de productos locales todavía desconocidos;
- sugerencias deterministas y búsqueda manual de productos existentes;
- confirmación persistente de equivalencias por sucursal;
- creación manual y confirmada de productos maestros;
- auditoría de cambios de nombre y categoría recibidos.

Los cálculos, consolidación, reportes analíticos, exportación, launcher y
empaquetado permanecen fuera del flujo activo hasta sus etapas correspondientes.
Algunos archivos del prototipo se conservan congelados como referencia.

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

## Importación cruda

La carga de Etapa 2 conserva el valor recibido en cada columna confirmada y el
número de fila del Excel. Una incidencia no elimina la fila ni convierte valores
inválidos en cero. La importación no crea productos y no calcula unidades
cobradas ni unidades combo.

## Homologación

Una carga con IDs locales desconocidos queda en “Productos por confirmar”. El
usuario puede relacionar cada producto con uno existente o crear un producto
maestro nuevo. La decisión se guarda por sucursal e ID local, por lo que futuras
cargas reutilizan la equivalencia sin volver a preguntar. Las sugerencias nunca
crean relaciones automáticamente.
