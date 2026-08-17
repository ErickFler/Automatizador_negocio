# Sistema de Concentrado de Ventas

Aplicación web local para construir un histórico confiable de ventas de cuatro
sucursales: Loma Linda, Solidaridad, Alianza y Unidad.

## Estado

Las **Etapas 0, 1, 2, 3, 4, 5 y 6** están activas:

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
- conversión numérica explícita con `Decimal`, sin sustituir errores por cero;
- normalización trazable de cada fila cruda a `SaleRecord`;
- cálculo centralizado de unidades de venta individual y unidades combo;
- incidencias bloqueantes y anomalías auditables sin correcciones silenciosas;
- reprocesamiento idempotente de ventas e incidencias de normalización;
- totales básicos de verificación por carga.
- motor histórico neutral para filtros y agregaciones por fecha, sucursal,
  producto maestro y categoría;
- series diarias, semanales ISO y mensuales;
- comparativos por sucursal, producto maestro y categoría;
- resumen separado de incidencias e histórico derivado de precios.
- dashboard local con periodos rápidos y rango personalizado;
- filtros por sucursal, categoría y producto maestro;
- tarjetas, gráficas temporal y por sucursal, y tabla de productos;
- avisos visibles de incidencias y cargas pendientes.

Los reportes analíticos independientes, costos administrados, exportación,
launcher y empaquetado permanecen fuera del flujo activo hasta sus etapas
correspondientes.
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

## Normalización de ventas

Al quedar homologado el último producto, el lote se normaliza automáticamente;
el servicio también puede reejecutarse de forma independiente. Cada venta apunta
a su lote, fila cruda, sucursal, producto local y producto maestro, y existe una
restricción única sobre `raw_sale_record_id`.

La fórmula oficial es `individual_sale_units = sales_amount / sale_price` y
`combo_units = total_units - individual_sale_units`. `total_units` conserva
`Cantidad` y `sales_amount` conserva `ImporteTotal`. `Costo` e `Importe` se conservan como
datos originales normalizados cuando son válidos, pero no participan en esas
fórmulas. Los campos monetarios y de unidades usan `Decimal`; SQLite conserva
hasta 12 decimales y no se aplica tolerancia ni redondeo de negocio.

Cantidad, precio de venta o importe total ilegibles, así como precio de venta
cero, omiten únicamente la venta normalizada de esa fila. Costo o importe
original ilegibles no bloquean la venta. Resultados no enteros, valores negativos
y unidades cobradas mayores que la cantidad se preservan y generan incidencias.

Al reprocesar se sustituyen exclusivamente las incidencias calculadas de Etapa 4;
las incidencias de importación y homologación permanecen intactas.

## Motor histórico

`aggregation_service.py` consulta únicamente `SaleRecord` normalizados y usa
`MasterProduct` como identidad universal. Las métricas permitidas se encuentran
en una lista cerrada: `total_units`, `individual_sale_units`, `combo_units` y
`sales_amount`. Las sumas se mantienen como `Decimal` y no incluyen filas crudas
que no hayan producido una venta normalizada.

Los rangos de fecha son inclusivos. Las semanas siguen ISO 8601: comienzan en
lunes, terminan en domingo y el año ISO puede diferir del año calendario cerca
de enero y diciembre. La consulta semanal de datos existentes devuelve solo
semanas con ventas; al solicitar una serie anual completa se incluyen todas las
semanas ISO faltantes con cero. Las series mensuales anuales completas devuelven
enero a diciembre y rellenan meses sin datos con cero.

La capa devuelve dataclasses independientes de HTML y Excel. El dashboard
consume exclusivamente esta API para sus métricas; no replica fórmulas de
negocio en las rutas, plantillas ni JavaScript. Costos, utilidad, margen y
exportación continúan fuera del flujo activo.

## Dashboard

La página principal `/` abre por defecto el mes actual. Permite elegir periodos
rápidos o un rango personalizado y filtrar por sucursal, categoría y producto
maestro. Las cuatro métricas disponibles son unidades totales, unidades de venta
individual, unidades combo y Venta $.

Las gráficas se dibujan con un recurso JavaScript local para que el sistema siga
funcionando sin internet. Cada gráfica incluye también una tabla de valores
accesible. Los parámetros recibidos por URL se validan contra listas cerradas y
los valores inválidos regresan a una opción segura con un aviso visible.
