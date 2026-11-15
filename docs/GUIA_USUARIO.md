# Guía rápida — Concentrado de Ventas

## 1. Abrir el sistema

Abra **Concentrado de Ventas** desde el menú Inicio o el acceso directo del
escritorio. Espere unos segundos; el sistema abrirá su navegador automáticamente.

## 2. Cargar ventas

Seleccione **Cargar ventas** y elija una opción:

- **Carga diaria:** indique la fecha y agregue los archivos disponibles. Puede
  cargar de una a cuatro sucursales; los archivos faltantes se agregan después.
- **Carga semanal:** indique una fecha dentro de la semana y seleccione juntos
  hasta 28 archivos. El nombre de cada archivo debe incluir su fecha y sucursal.

Revise que los archivos indiquen **Listo** y presione **Procesar día** o
**Procesar semana**. Si un archivo ya fue cargado, el sistema lo indicará y no
duplicará la información.

## 3. Confirmar productos

Cuando aparezcan productos nuevos, indique si corresponden a un producto ya
conocido. Si realmente es diferente, regístrelo usando únicamente nombre y
categoría. No relacione productos si no está seguro.

Si aparece el botón **Confirmar coincidencias exactas**, puede utilizarlo para
guardar juntas las opciones con el mismo nombre y categoría. Los productos sin
precio de venta se guardan para revisión y no detienen el resto de la carga.

## 4. Consultar el dashboard

En **Inicio** puede elegir periodo, sucursal, categoría y producto. Allí verá
ventas, unidades, costo, utilidad y cobertura. Si faltan costos, la utilidad se
identifica como parcial.

## 5. Cargar costos

Abra **Costos**, elija **Importar costos**, indique la fecha desde la cual son
vigentes y cargue el Excel. Confirme manualmente los productos que el sistema no
reconozca.

## 6. Revisar reportes

En **Reportes** encontrará ventas diarias, semana, históricos, productos y
sucursales. Aplique los filtros antes de interpretar o compartir los resultados.

## 7. Exportar

Use **Exportar a Excel** dentro del reporte deseado. Guarde el archivo descargado
en la carpeta que prefiera.

## 8. Apagar correctamente

Presione **Apagar sistema** en la navegación, confirme la acción y espere la
página “Sistema apagado”. Después puede cerrar la pestaña.

## 9. Respaldar los datos

Cierre primero el sistema. Copie a un medio seguro:

```text
%LOCALAPPDATA%\ConcentradoVentas\app.db
%LOCALAPPDATA%\ConcentradoVentas\uploads\
%LOCALAPPDATA%\ConcentradoVentas\cost_uploads\
```

La carpeta `logs\` es opcional, pero ayuda a diagnosticar problemas. No borre ni
edite directamente `app.db`.
