# Sistema de Concentrado de Ventas

Aplicación web local para concentrar reportes Excel de ventas de varias sucursales.

## Estado

Las **Etapas 0 a 8** dejan preparada la base técnica, el catálogo maestro, la
lectura segura, las reglas de cálculo, la persistencia transaccional y el
consolidado histórico con exportación Excel e interfaz web local. Cada venta queda
vinculada con su lote, producto, fecha, sucursal, archivo y fila de origen. Las
incidencias también permanecen en SQLite.

## Requisitos

- Python 3.12 o superior

## Instalación

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

La configuración disponible está documentada en `.env.example`. Para cambiarla:

```bash
cp .env.example .env
```

## Ejecución

Con el entorno virtual activo:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Abrir <http://127.0.0.1:8000>. El diagnóstico está disponible en
<http://127.0.0.1:8000/health>.

Durante el desarrollo, la base se crea en `data/app.db` y los logs se escriben en
`data/logs/app.log`. En la aplicación empaquetada para Windows, los datos se
guardan en `%LOCALAPPDATA%\ConcentradoVentas`.

## Pruebas

```bash
pytest
```

## Estructura

```text
app/
  core/          Configuración y logging
  db/            Conexión y modelos SQLAlchemy
  domain/        Normalización y futuras reglas de negocio
  services/      Servicios de catálogo y futuros procesos
  static/        CSS y futuros recursos del navegador
  templates/     Plantillas HTML
  main.py        Aplicación FastAPI y endpoints iniciales
data/
  exports/       Archivos generados en etapas posteriores
  logs/          Logs locales
  uploads/       Copias de archivos cargados en etapas posteriores
tests/           Pruebas automatizadas
```

## Próximas etapas

El empaquetado como ejecutable se implementará por separado en la etapa final.

## Formato de entrada preparado

El lector admite `.xlsx` y `.xlsm` con estos encabezados exactos:

`NombreCategoria`, `IDProducto`, `NombreProducto`, `Cantidad`, `PrecioVenta`,
`Costo`, `Importe` e `ImporteTotal`.

## Reglas de cálculo aprobadas

```text
UnidadesReales = ImporteTotal / PrecioVenta
UnidadesCombo = Cantidad - UnidadesReales
```

Los resultados no enteros, negativos o inconsistentes se conservan y generan
advertencias. `Costo` e `Importe` no intervienen en estas fórmulas.

## Persistencia y duplicados

El procesamiento de cada archivo crea un `ImportBatch` y persiste sus
`SaleRecord` e incidencias de manera transaccional. Una combinación repetida de
SHA-256, fecha y sucursal se rechaza antes de copiar o insertar nuevamente el
archivo. Una falla inesperada revierte productos y ventas parciales y marca el
lote como `FAILED`.

## Consolidado

El histórico se agrupa en SQLite por fecha, sucursal, categoría, ID de producto y
producto. Se suman Cantidad, UnidadesReales, UnidadesCombo e ImporteTotal. Los
lotes fallidos se excluyen y los totales completamente desconocidos permanecen
como `NULL`, sin convertirse artificialmente en cero.

## Exportación Excel

La exportación genera un archivo `.xlsx` nuevo con las hojas `Concentrado`,
`Detalle`, `Incidencias` y `Productos Nuevos`. Incluye encabezados destacados,
filtros, fila superior congelada, anchos limitados y formatos de fechas, cantidades
y moneda. Las exportaciones existentes no se sobrescriben.

## Interfaz web

La pantalla principal permite seleccionar una fecha y asociar entre uno y cuatro
archivos con sucursales distintas. Después del procesamiento muestra resultados,
totales e incidencias y permite descargar el concentrado. Las vistas de histórico
y catálogo muestran la información almacenada localmente.

Los nombres de las cuatro sucursales se configuran con `APP_BRANCHES`, separados
por comas.

## Inicio y apagado local

En macOS, después de instalar las dependencias, se puede iniciar con doble clic en
`Iniciar Sistema.command`. El launcher espera a que FastAPI responda y abre
automáticamente `http://127.0.0.1:8000`. Si el sistema ya está activo, reutiliza la
instancia existente.

El botón `Apagar sistema` solicita un cierre limpio del servidor. Si la aplicación
se inició manualmente con Uvicorn, el botón informa que debe utilizarse el launcher.

## Empaquetado para Windows

La configuración de PyInstaller, los metadatos del ejecutable y los scripts de
Inno Setup se encuentran en `packaging/windows`. La compilación final debe hacerse
y probarse en Windows; consulta `packaging/windows/README.md`.
