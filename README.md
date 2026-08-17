# Sistema de Concentrado de Ventas

Aplicación web local para construir un histórico confiable de ventas de cuatro
sucursales: Loma Linda, Solidaridad, Alianza y Unidad.

## Estado

Las **Etapas 0 a 12** están activas:

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
- índice de reportes preconfigurados en `/reports`;
- reportes diario, semanal, histórico semanal, histórico mensual, de productos
  y comparativo de sucursales;
- detalle individual de producto con equivalencias, evolución y cambios de
  precio observados.
- costos oficiales históricos asociados exclusivamente a productos maestros;
- importación trazable de costos desde Excel, con vigencia, homologación manual
  y detección de conflictos;
- consulta de costo vigente, historial y productos todavía sin costo.
- rentabilidad conocida basada en costos oficiales vigentes para cada venta;
- costo consumido, utilidad, margen y cobertura por producto, categoría y
  sucursal;
- indicadores financieros en dashboard y reportes seleccionados.
- exportación Excel de los seis reportes preconfigurados y del detalle de
  producto;
- archivos `.xlsx` en memoria, con filtros, orden, formatos, incidencias y
  advertencias consistentes con la pantalla.
- manejo comprensible de errores HTTP, revisión visual y validación integral
  del flujo desde una base nueva hasta la exportación.
- launcher local con health check, apertura automática del navegador, instancia
  única y apagado limpio desde la interfaz.

El constructor libre de reportes, el ejecutable final y el instalador permanecen
fuera del flujo activo hasta sus etapas correspondientes.

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

## Inicio normal

En macOS de desarrollo, hacer doble clic en `Iniciar Sistema.command`. También
puede ejecutarse el launcher directamente:

```bash
python scripts/launch.py
```

El launcher prepara los directorios locales, inicia el servidor únicamente en
`127.0.0.1`, espera a que `/health` responda y abre una sola pestaña en el
navegador predeterminado. `APP_PORT` permite cambiar el puerto desde `.env`; el
valor predeterminado es `8000`. Si ya existe una instancia de esta aplicación,
la reutiliza sin iniciar otro servidor.

Para terminar, utilizar **Apagar sistema** en la navegación y confirmar la
acción. La página final puede cerrarse una vez mostrado “Sistema apagado”.

## Inicio manual de desarrollo

Como alternativa para diagnóstico, un desarrollador puede iniciar Uvicorn:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

El apagado desde la interfaz solo está disponible cuando la aplicación fue
iniciada mediante el launcher; en modo manual se utiliza `Ctrl+C`.

Abrir <http://127.0.0.1:8000>. El diagnóstico está disponible en
<http://127.0.0.1:8000/health> y el catálogo en
<http://127.0.0.1:8000/products>. Los reportes se consultan desde
<http://127.0.0.1:8000/reports>.

La base de desarrollo se crea en `data/app.db` y los logs se escriben en
`data/logs/app.log`. Ambos son archivos locales ignorados por Git.

## Pruebas

```bash
pytest
```

No quedan pruebas congeladas. `tests/test_system.py` cubre el launcher local,
health check, instancia única, rutas runtime y apagado coordinado. El antiguo
procesador monolítico y su prueba se eliminaron en Etapa 11 porque fueron
reemplazados completamente por la importación cruda, la confirmación de
productos y el procesamiento normalizado.

## Flujo de uso

1. Abrir **Cargar ventas**, elegir la fecha de ventas y una de las cuatro
   sucursales, y procesar el archivo Excel.
2. Si aparecen productos nuevos, relacionarlos con un producto conocido o
   registrarlos con nombre y categoría.
3. Consultar las ventas normalizadas en el dashboard y los reportes.
4. Cargar costos oficiales con su fecha de vigencia desde **Costos**.
5. Revisar costo, utilidad, margen y cobertura; una cobertura incompleta se
   identifica como utilidad parcial.
6. Exportar el reporte visible a Excel cuando se necesite compartirlo.

El historial conserva cada carga y sus incidencias. Recargar una página no
repite el procesamiento, y un archivo duplicado para la misma fecha y sucursal
se rechaza explícitamente.

## Modelo de productos

`MasterProduct` representa el producto universal y no contiene un ID externo.
`BranchProduct` conserva el ID y los nombres recibidos dentro de una sucursal y
se relaciona con un producto maestro. La combinación `branch_id` más
`external_product_id` es única.

Un producto maestro puede tener múltiples equivalencias de distintas sucursales.

## Importación cruda

La carga conserva el valor recibido en cada columna confirmada y el
número de fila del Excel. Una incidencia no elimina la fila ni convierte valores
inválidos en cero. La importación no crea productos y no calcula unidades
de venta individual ni unidades combo.

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
y unidades de venta individual mayores que la cantidad se preservan y generan
incidencias.

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
negocio en las rutas, plantillas ni JavaScript.

## Dashboard

La página principal `/` abre por defecto el mes actual. Permite elegir periodos
rápidos o un rango personalizado y filtrar por sucursal, categoría y producto
maestro. Las cuatro métricas disponibles son unidades totales, unidades de venta
individual, unidades combo y Venta $.

Las gráficas se dibujan con un recurso JavaScript local para que el sistema siga
funcionando sin internet. Cada gráfica incluye también una tabla de valores
accesible. Los parámetros recibidos por URL se validan contra listas cerradas y
los valores inválidos regresan a una opción segura con un aviso visible.

## Reportes preconfigurados

`reporting_service.py` resuelve parámetros, compone DTOs y prepara las tablas,
pero delega todas las sumas y agrupaciones de ventas a
`aggregation_service.py`. De esta forma, un mismo rango y filtros produce los
mismos totales en el dashboard y en los reportes.

Los históricos semanal y mensual rellenan periodos sin ventas con cero. El
comparativo incluye siempre las cuatro sucursales activas. Las vistas muestran
un resumen explícito de filtros, avisan cuando existen incidencias o cargas por
homologar y nunca incorporan filas crudas sin `SaleRecord` en sus métricas.

## Exportaciones Excel

Los reportes de ventas diarias, semana lunes a domingo, histórico semanal,
histórico mensual, productos y comparativo de sucursales incluyen el botón
**Exportar a Excel**. El detalle individual de producto también puede exportarse
con hojas separadas de resumen, evolución, precios y costos.

`export_service.py` recibe los mismos DTO de `reporting_service.py` utilizados
por la interfaz. No consulta ventas por una ruta alternativa ni recalcula costo,
utilidad o margen. El archivo conserva los filtros y el orden elegidos, muestra
las métricas financieras conocidas y escribe “No disponible” cuando falta un
costo oficial. Si la cobertura está incompleta, el resumen identifica la
utilidad como parcial e incluye cobertura por importe y por registros.

Los archivos `.xlsx` se generan en memoria y se entregan directamente como
descarga; no se guardan ni acumulan en `data/exports/`. Cuando existen incidencias
del periodo se agrega una hoja comprensible sin IDs, hashes, rutas ni nombres
internos. Las cargas pendientes se mencionan en el resumen y no se incorporan a
los resultados.

## Costos oficiales históricos

`ProductCost` pertenece a `MasterProduct` y es común para las cuatro sucursales.
Cada registro tiene una fecha de vigencia. Para una venta se utiliza el costo
más reciente cuya vigencia sea anterior o igual a la fecha de venta; si no
existe, el costo oficial se considera no disponible. Los costos futuros, cero
implícito, `SaleRecord.original_cost` y el precio de venta nunca se usan como
sustitutos.

La importación en `/costs/import` acepta archivos `.xlsx` o `.xlsm`. La primera
hoja debe contener las columnas `Producto` y `Costo`; también se aceptan los
encabezados `Nombre Producto` y `Costo unitario`. La fecha de vigencia se elige
en la pantalla y no forma parte del archivo. El mapeo se mantiene concentrado en
`CostExcelMapping`, por lo que puede ajustarse cuando exista un formato real.

Una coincidencia exacta por nombre normalizado puede relacionarse de forma
automática. Los demás nombres quedan pendientes para relacionarlos con un
producto maestro existente u omitirlos. El archivo original y su SHA-256 se
conservan en `data/cost_uploads/`; un archivo repetido para la misma vigencia y
un costo repetido para el mismo producto y fecha generan conflicto, nunca una
sobrescritura silenciosa.

## Rentabilidad conocida

La fórmula oficial es `consumed_cost = total_units × effective_unit_cost` y
`known_profit = sales_amount - consumed_cost`. El costo se aplica a todas las
unidades físicas, incluidas las utilizadas en combos. Cada venta resuelve su
propio costo histórico; un cambio de vigencia dentro del periodo no se aplica
retroactivamente a ventas anteriores.

El margen se calcula sobre la venta con costo conocido como
`known_profit / sales_amount_with_known_cost × 100`. Nunca se suman ni se
promedian porcentajes de filas: cada grupo recalcula su margen a partir de sus
totales. Cuando la venta del grupo es cero, el margen no aplica.

Las ventas sin costo oficial permanecen visibles, pero no aportan costo, utilidad
ni margen conocidos. La cobertura por importe es
`sales_amount_with_known_cost / total_sales_amount × 100`; si la venta total es
cero, se muestra “No aplica” y se conserva además la cobertura por número de
registros. Con ventas negativas auditables, el porcentaje por importe conserva
los signos y puede quedar fuera del intervalo habitual de 0 a 100%.

La utilidad por producto representa la venta directa atribuida a ese producto
menos el costo físico de todas sus unidades consumidas. Los ingresos de combos
no se redistribuyen entre sus componentes porque no existe todavía una regla de
negocio aprobada para hacerlo; por ello, un componente de combo puede mostrar
utilidad negativa sin que el sistema la corrija artificialmente.

## Operación, respaldo y limitaciones

Para preservar el sistema se deben respaldar juntos:

- `data/app.db`, que contiene ventas, productos, equivalencias, incidencias y
  costos;
- `data/uploads/`, con los archivos originales de ventas;
- `data/cost_uploads/`, con los archivos originales de costos.

`data/logs/` es útil para diagnóstico, pero no es la fuente de verdad del
negocio. Los Excel exportados se generan en memoria y deben guardarse donde el
usuario elija; no forman parte del respaldo interno. Si se usa un `.env`, debe
resguardarse por separado y nunca publicarse porque puede contener configuración
sensible.

El sistema es local y no incluye todavía backup automático, sincronización en
la nube, usuarios, ejecutable final ni instalador. Tampoco asigna el ingreso de
un combo entre sus componentes. Cuando falta un costo oficial, la venta
permanece visible, pero costo, utilidad y margen se presentan como no disponibles
y la cobertura deja claro que el resultado financiero es parcial.
