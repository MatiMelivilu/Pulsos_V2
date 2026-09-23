# Pulsos V2: instalación en otra Raspberry Pi

La aplicación principal es `interfaz_pulsos_optoacoplada.py`. Lee precios y cantidades de pulsos desde `valores.txt`, recibe entradas por GPIO, solicita ventas al POS por USB serial y genera los pulsos por GPIO. El engranaje abre la selección de redes Wi-Fi con teclado táctil.

No se valida el número de serie de la Raspberry. No se necesita el conversor serial de pulsos ni ejecutar el programa Node.js para este flujo. El puerto serial del **POS sí es necesario**.

## 1. Preparar el sistema y copiar los archivos

Usa Raspberry Pi OS **con escritorio**, Bookworm o posterior, con pantalla táctil configurada y una interfaz Wi-Fi. La app requiere una sesión gráfica local; Raspberry Pi OS Lite sin escritorio no basta. Configura el país Wi-Fi correspondiente durante la instalación del sistema. NetworkManager es el administrador predeterminado desde Bookworm según la [documentación de Raspberry Pi](https://www.raspberrypi.com/documentation/computers/configuration.html).

Para instalar las dependencias, conecta inicialmente la Raspberry a Internet por Ethernet o mediante el escritorio. Inicia sesión como el usuario que ejecutará la app.

Copia el proyecto a `~/Pulsos_V2`. Estos archivos deben quedar juntos:

```text
Pulsos_V2/
├── interfaz_pulsos_optoacoplada.py
├── wifi_touch.py
├── gear.png
├── pencil2.png
├── valores.txt
├── requirements.txt
└── test_wifi_touch.py          # Opcional: pruebas sin hardware
```

Transfiere los archivos por USB, SCP o tu repositorio, incluyendo los archivos nuevos. No copies `.venv` ni `__pycache__` desde otra Raspberry: recrea el entorno. Conserva una copia de `valores.txt` de la instalación que quieras replicar.

## 2. Instalar dependencias

En una terminal de la Raspberry:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip python3-tk python3-dev build-essential swig liblgpio-dev network-manager
cd ~/Pulsos_V2
/usr/bin/python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Se usa explícitamente `/usr/bin/python3` para evitar que `pyenv` seleccione otro intérprete. `tkinter` se instala con `apt`; `pyserial` proporciona el módulo `serial` y `Pillow` proporciona `PIL`. No instales un paquete llamado `serial` en su lugar.

El backend GPIO utilizado es `lgpio`; no hace falta instalar `RPi.GPIO`, `pigpio` ni usar `NativeFactory`. Los paquetes de compilación siguen las [instrucciones oficiales de GPIO Zero](https://gpiozero.readthedocs.io/en/latest/installing.html). `requirements.txt` no fija todas las versiones: una vez validada la instalación física, puedes registrar las versiones utilizadas con:

```bash
.venv/bin/python -m pip freeze > requirements-instalacion.txt
```

### Si decides seguir usando pyenv

Como alternativa al entorno anterior, selecciona tu Python de pyenv y ejecuta:

```bash
python -m tkinter
python -m pip install -r requirements.txt
```

El primer comando debe abrir una ventana de demostración. Si falta `_tkinter`, ese Python debe reconstruirse con soporte Tcl/Tk o puedes usar la ruta `/usr/bin/python3` indicada arriba. Instalar `python3-tk` no agrega automáticamente soporte a un Python ya compilado por pyenv. Instala y ejecuta siempre con el mismo intérprete; evita mezclar `pip`, `python` y `sudo python` de entornos distintos.

## 3. Permisos de GPIO y POS

```bash
sudo usermod -aG gpio,dialout "$(id -un)"
```

Cierra sesión y vuelve a entrar para aplicar los grupos, o reinicia la Raspberry. Luego verifica:

```bash
id
ls -l /dev/gpiochip*
systemctl is-active NetworkManager
nmcli device status
```

El usuario necesita acceso de lectura/escritura a los dispositivos GPIO y al puerto del POS. NetworkManager debe estar activo y mostrar la interfaz Wi-Fi. Si está inactivo en una instalación nueva preparada con esta guía:

```bash
sudo systemctl enable --now NetworkManager
```

Ejecuta la app como el usuario de la sesión gráfica, **sin `sudo`**. La app Wi-Fi utiliza los permisos de NetworkManager de esa sesión. Para diagnosticar autorizaciones usa `nmcli general permissions`; no es necesario dar permisos globales con `chmod 777`.

## 4. Cableado GPIO y configuración del POS

La numeración del código es **BCM**. Para el conector estándar de 40 pines:

| Función | GPIO BCM | Pin físico |
| --- | ---: | ---: |
| Entrada CH1 | 2 | 3 |
| Entrada CH2 | 3 | 5 |
| Entrada CH3 | 4 | 7 |
| Entrada CH4 | 17 | 11 |
| Salida CH1 | 19 | 35 |
| Salida CH2 | 26 | 37 |
| Salida CH3 | 20 | 38 |
| Salida CH4 | 21 | 40 |
| Salida de pulsos | 16 | 36 |

Las entradas usan `Button(..., bounce_time=0.05)` con pull-up por defecto y activación a nivel bajo. Las cuatro salidas CH se inicializan en alto y la salida de pulsos en bajo. El pulso actual tiene aproximadamente 100 ms en alto y 100 ms en bajo, sin garantía de tiempo real.

Replica la placa optoacoplada y el cableado de la instalación existente. Los GPIO son de **3,3 V**; no conectes señales de 5 V/12 V directamente. Consulta la [documentación eléctrica oficial](https://pip-assets.raspberrypi.com/categories/685-whitepapers-app-notes/documents/RP-006553-WP/A-history-of-GPIO-usage-on-Raspberry-Pi-devices-and-current-best-practices). GPIO2 y GPIO3 también se usan para I²C: no reserves esos mismos pines para otro periférico mientras se utilicen como entradas de la app.

Conecta el POS y localiza su puerto:

```bash
ls -l /dev/serial/by-id/
.venv/bin/python -m serial.tools.list_ports
```

Algunos dispositivos no publican una ruta `by-id`; usa el puerto que detecte la segunda orden. Al inicio de `interfaz_pulsos_optoacoplada.py` configura:

```python
SERIAL_PORT = '/dev/ttyACM0'
BAUD_RATE = 115200
n_inputs = 4
```

Si existe un identificador estable en `/dev/serial/by-id/`, puedes usar su ruta completa como `SERIAL_PORT` para evitar cambios de `ttyACM0` al conectar otros dispositivos. El POS debe tener la misma configuración de integración/protocolo que en la instalación original. Espera el mensaje de conexión del POS antes de accionar ventas o comandos del terminal.

Aunque existe `n_inputs`, el código configura **cuatro entradas y cuatro callbacks GPIO fijos**. Mantén `n_inputs = 4` para replicar esta versión; cambiar ese valor por sí solo no adapta el hardware. El engranaje ahora configura Wi-Fi.

## 5. Precios y cantidad de pulsos

`valores.txt` debe contener exactamente cuatro líneas, una por CH1 a CH4, con dos enteros separados por espacio: `monto pulsos`. No agregues encabezados, comentarios, símbolos de moneda ni separadores de miles.

```text
100 1
200 2
300 3
400 4
```

Son valores de ejemplo: reemplázalos por los montos de la instalación. Desde la interfaz también puedes editar los precios y pulsos y presionar **Guardar**. El usuario necesita permisos para escribir este archivo y crear la carpeta `log/`.

## 6. Comprobar y ejecutar

Desde una terminal del escritorio de la Raspberry:

```bash
cd ~/Pulsos_V2
.venv/bin/python -c "import tkinter, serial, PIL, gpiozero, lgpio; print('Dependencias OK')"
.venv/bin/python -m unittest -v test_wifi_touch
.venv/bin/python interfaz_pulsos_optoacoplada.py
```

Las pruebas Wi-Fi usan simulaciones y no conectan redes ni activan GPIO. La ejecución real inicializa las salidas GPIO. Inicia siempre desde la carpeta del proyecto: las imágenes, `valores.txt` y `log/` se resuelven respecto de la carpeta actual.

La app abre a pantalla completa. Los registros se guardan en `log/log_AAAAMMDD.txt`; los errores de arranque también aparecen en la terminal. La tecla Escape de la ventana principal permite salir de pantalla completa: el modo actual no es un bloqueo completo del escritorio.

### Conectarse a Wi-Fi

1. Pulsa el engranaje. Se activa Wi-Fi y se buscan redes visibles.
2. Elige una red; se muestra su intensidad y la red activa lleva una marca.
3. Escribe la contraseña con el teclado táctil incluido. Usa **Mayús**, **Símbolos**, **Espacio** y **Borrar** según corresponda.
4. Pulsa **Conectar** y espera el resultado. Para una red abierta o una conexión guardada puedes dejar la contraseña vacía.
5. Pulsa **Volver** para regresar a pagos, o **Buscar redes** para actualizar la lista.

Esta ventana usa `nmcli`; no requiere `nm-connection-editor` ni un teclado virtual externo. No ofrece alta de redes ocultas ni configuración de redes empresariales 802.1X/EAP. Esas conexiones deben configurarse por fuera de esta ventana. La conexión Wi-Fi de la Raspberry no configura la conectividad propia del POS.

## 7. Inicio automático opcional

Primero valida el arranque manual. Si el escritorio admite autoinicio XDG, crea `~/.config/autostart/pulsos.desktop` con este contenido, sustituyendo **USUARIO** por el usuario real en ambas rutas:

```ini
[Desktop Entry]
Type=Application
Name=Pulsos V2
Exec=/home/USUARIO/Pulsos_V2/.venv/bin/python /home/USUARIO/Pulsos_V2/interfaz_pulsos_optoacoplada.py
Path=/home/USUARIO/Pulsos_V2
Terminal=false
```

Crea antes la carpeta con `mkdir -p ~/.config/autostart`. Las rutas deben ser absolutas: no uses `~` ni `$HOME` en el archivo `.desktop`. Este método arranca **al iniciar la sesión gráfica**, no antes. Para operación sin teclado, configura el inicio de sesión automático al escritorio en la Raspberry. Comprueba el resultado tras reiniciar; si tu sesión no procesa autoinicio XDG, configura el mismo comando y directorio de trabajo en el mecanismo de inicio de tu escritorio. No lances una segunda instancia si la app ya está abierta.

## 8. Problemas frecuentes

| Síntoma | Revisar |
| --- | --- |
| `No module named lgpio`, `serial` o `PIL` | Instalar `requirements.txt` con el mismo Python que ejecuta la app. |
| `No module named _tkinter` | Usar Python del sistema con `python3-tk`, o reconstruir Python de pyenv con Tcl/Tk. |
| `NativeFactory`, `/sys/class/gpio/...` o `serial_handler` en el error | Se está ejecutando una copia antigua; copiar de nuevo el archivo principal actualizado. |
| No puede abrir `gpiochip` / permiso denegado | Revisar `/dev/gpiochip*`, grupo `gpio`, sesión renovada y versiones de `gpiozero`/`lgpio`. No asumir que el mismo número de chip sirve en todos los modelos. |
| GPIO ocupado | Cerrar otra instancia o proceso que utilice esos pines y revisar periféricos habilitados. |
| POS no encontrado o permiso denegado | Revisar cable USB, `SERIAL_PORT`, puerto detectado y grupo `dialout`. |
| No encuentra `wifi_touch`, imágenes o `valores.txt` | Copiar todos los archivos y ejecutar desde `~/Pulsos_V2`. |
| El engranaje abre el editor de entradas o el editor externo | Revisar la ruta de la copia ejecutada y reiniciar la aplicación actualizada. |
| No hay redes Wi-Fi | Revisar `nmcli device status`, `nmcli radio`, `rfkill list`, país Wi-Fi, alcance y compatibilidad de banda. |
| Error de permisos al conectar Wi-Fi | Usar la sesión gráfica local y revisar `nmcli general permissions`. |
| `no display name` / `$DISPLAY` | Iniciar desde el escritorio; una sesión SSH o un servicio de arranque sin sesión gráfica no basta. |
| Ventas o pulsos no funcionan | Confirmar conexión POS, cuatro líneas válidas en `valores.txt` y cableado BCM. |

Antes de poner la nueva Raspberry en servicio, verifica en el equipo físico cada entrada, el número de pulsos tras una venta aprobada y la ausencia de pulsos tras una venta rechazada. El botón **test venta** envía una solicitud al POS por monto 500 y **test pulso** activa la salida: no son simulaciones. La validación por software no sustituye esta comprobación física.
