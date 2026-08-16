# Lista de validación en Windows

Registrar el resultado en Windows 10 y Windows 11, idealmente en un equipo o
usuario que no tenga Python instalado.

## Aplicación portable

- [ ] `build.ps1` termina sin errores.
- [ ] `ConcentradoVentas.exe` muestra el icono CV.
- [ ] El ejecutable abre el navegador sin mostrar consola.
- [ ] Una segunda apertura reutiliza el servidor existente.
- [ ] El botón `Apagar sistema` cierra el proceso.
- [ ] Windows Defender no bloquea el ejecutable.

## Procesamiento

- [ ] Procesa un archivo válido.
- [ ] Procesa cuatro sucursales en una operación.
- [ ] Detecta un archivo duplicado.
- [ ] Conserva las incidencias de `PrecioVenta = 0`.
- [ ] Descarga y abre el concentrado en Microsoft Excel.
- [ ] Histórico y catálogo permanecen después de reiniciar.

## Datos y permisos

- [ ] Crea `%LOCALAPPDATA%\ConcentradoVentas\app.db`.
- [ ] Crea las carpetas `uploads`, `exports` y `logs`.
- [ ] No escribe datos dentro de `Program Files` ni de la carpeta instalada.
- [ ] Acepta nombres con espacios, `ñ` y acentos.
- [ ] Funciona con una cuenta estándar sin permisos de administrador.

## Instalador

- [ ] Inno Setup genera el instalador.
- [ ] El acceso directo del menú Inicio funciona.
- [ ] El acceso directo opcional del escritorio funciona.
- [ ] La desinstalación elimina el programa sin borrar el histórico del usuario.
- [ ] Una actualización conserva el histórico existente.
