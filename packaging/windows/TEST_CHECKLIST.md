# Validación de release en Windows limpio

Registrar fecha, versión de Windows, versión del artefacto, tamaño, tiempo de
build y resultado. Usar una cuenta estándar sin Python ni dependencias del
proyecto instaladas.

## Build e instalación

- [ ] `build.ps1` termina y aprueba `smoke-test.ps1`.
- [ ] El resultado es `onedir` y contiene `ConcentradoVentas.exe`.
- [ ] El ejecutable y los accesos directos muestran el icono CV.
- [ ] Inno Setup genera el instalador versionado.
- [ ] Instala sin requerir privilegios para el uso cotidiano.
- [ ] Funcionan el menú Inicio y el acceso opcional del escritorio.
- [ ] No aparece una consola durante el uso normal.

## Inicio y servidor local

- [ ] Abre una sola pestaña después de responder `/health`.
- [ ] Escucha únicamente en `127.0.0.1`.
- [ ] Una segunda apertura reutiliza el backend existente.
- [ ] Un puerto ocupado por otra app muestra un mensaje amigable.
- [ ] Cerrar el navegador no termina el backend.
- [ ] “Apagar sistema” entrega la página final, libera el puerto y termina el proceso.

## Flujo funcional empaquetado

- [ ] Primera ejecución crea cuatro sucursales y estado vacío comprensible.
- [ ] Importa un Excel de ventas válido.
- [ ] Detecta un archivo duplicado.
- [ ] Permite relacionar un producto existente y crear uno nuevo.
- [ ] Conserva incidencias, incluido `PrecioVenta = 0`.
- [ ] Importa costos y respeta su vigencia histórica.
- [ ] Dashboard, gráficas, filtros, reportes y rentabilidad funcionan offline.
- [ ] Las siete exportaciones abren en Excel sin reparación.

## Datos, reinicio y actualización

- [ ] Crea `%LOCALAPPDATA%\ConcentradoVentas\app.db`.
- [ ] Crea `uploads`, `cost_uploads` y `logs` en esa carpeta.
- [ ] `PRAGMA integrity_check` devuelve `ok` y foreign keys están activas.
- [ ] Reiniciar conserva ventas, productos, equivalencias, costos y archivos.
- [ ] Copiar una base y uploads existentes permite migrar la instalación.
- [ ] Actualizar binarios conserva la carpeta persistente.
- [ ] Desinstalar elimina el programa sin borrar los datos del negocio.

## Compatibilidad y cierre externo

- [ ] Funciona con nombres con espacios, `ñ` y acentos.
- [ ] Funciona sin conexión a internet.
- [ ] Cerrar externamente el proceso no corrompe SQLite al siguiente arranque.
- [ ] Se documentó cualquier aviso real de Defender o SmartScreen, sin intentar evadirlo.
