# Pruebas con el POS conectado a la Raspberry

Ejecuta los comandos desde `/home/ideasdigitales/getnet`, o desde la ruta donde
hayas copiado el proyecto a la Raspberry. El POS debe estar en modo integrado.
Cierra otros programas que estén usando el puerto.

## 1. Preparar Python

```bash
cd /home/ideasdigitales/getnet
python3 -m venv .venv
.venv/bin/python -m pip install -r getnet_serial/requirements.txt
```

Si `venv` no está disponible en Raspberry Pi OS/Debian:

```bash
sudo apt install python3-venv
```

## 2. Identificar el puerto (no cobra)

```bash
.venv/bin/python -m getnet_serial.demo ports
```

Ejemplo de salida: `/dev/ttyACM0 | ...`. También puede ser `/dev/ttyUSB0`.
Usa el puerto que aparezca, no el del ejemplo si es distinto. Para identificar
una ruta estable si el dispositivo la proporciona:

```bash
ls -l /dev/serial/by-id/
```

Si no aparece ningún puerto, comprueba que el cable transmite datos y que el POS
está configurado para integración USB. Revisa la detección del sistema con:

```bash
sudo dmesg --ctime | tail -n 40
```

Si recibes `Permission denied`, en Raspberry Pi OS/Debian comprueba los permisos
con `ls -l /dev/ttyACM0` (sustituye el puerto). Si pertenece a `dialout`:

```bash
sudo usermod -aG dialout "$USER"
```

Cierra la sesión e ingresa nuevamente para activar el grupo. No necesitas
ejecutar el script de pagos como root.

## 3. Comprobar comunicación (no cobra)

```bash
.venv/bin/python -m getnet_serial.demo poll --port /dev/ttyACM0
```

Esperado: `FunctionCode: 106`, `ResponseCode: 0`, `Connected: true`.
La velocidad predeterminada es 115200; se puede configurar con `--baudrate`.
Si hay timeout, verifica puerto, velocidad configurada y modo integrado.
No pases a la venta hasta que `poll` confirme conexión.

## 4. Consultar el último comprobante (no inicia un pago)

```bash
.venv/bin/python -m getnet_serial.demo last --port /dev/ttyACM0
```

Devuelve la última transacción disponible; puede ser venta, anulación o devolución.
Si no hay comprobante, puede devolver una respuesta de error. No imprime en el POS.
Aunque la solicitud es `101`, el `FunctionCode` del comprobante puede ser `100`
(venta), `102` (anulación) o `108` (devolución), según el manual, páginas 47–49.
Para diagnóstico, muestra todos los mensajes procesados y amplía la espera:

```bash
.venv/bin/python -m getnet_serial.demo last --port /dev/ttyACM0 --timeout 60 --verbose
```

## 5. Probar una venta

Este comando inicia un cobro REAL si el terminal opera en producción. Solo es
simulado si estás conectado al simulador/ambiente de prueba habilitado por Getnet.
El monto es en pesos CLP enteros, sin multiplicar por 100. El ejemplo inicia $1.000:

```bash
.venv/bin/python -m getnet_serial.demo sale \
  --port /dev/ttyACM0 \
  --amount 1000
```

Acerca/inserta la tarjeta y completa la operación en el POS. El script muestra
mensajes intermedios y finalmente `PAGO APROBADO` o `PAGO RECHAZADO / CANCELADO`.
La llamada permanece bloqueada esperando la respuesta final `FunctionCode: 100`
con `ResponseCode`. Una confirmación de recepción, un mensaje intermedio o un
contenido firmado que no sea un objeto no termina la venta. Esos contenidos se
conservan como eventos de diagnóstico; no se interpretan como aprobaciones.
Los JSON codificados como string dentro de otro JSON se decodifican una vez más
si el resultado es un objeto. Se verifica la firma antes de procesar el contenido.
Para imprimir el comprobante agrega `--print-on-pos`. El tiempo de espera de venta
predeterminado es 120 segundos; puede cambiarse con `--timeout 180`.

Con `--verbose` también se muestra el JSON de solicitud antes de firmarlo.
Si el firmware devuelve `FunctionCode: 0` con un `ResponseCode`, se termina la
espera y se conserva esa respuesta en el registro como resultado incierto.
En particular, `ResponseCode: 0` junto a “Error durante el procesamiento” no
es una aprobación: no corresponde a una respuesta de venta `FunctionCode: 100`.

Cada nuevo intento genera un ticket con `uuid.uuid4().hex[:10].upper()`: 10
caracteres hexadecimales, sin caracteres de Base64. Es un identificador derivado
de UUID4, no el UUID completo. Usa la longitud del ticket manual que funcionó en
el POS. La longitud como causa del rechazo anterior aún no está confirmada.
El identificador recortado tiene 40 bits; se comprueba su unicidad en el registro.
El formato Base64 anterior no funcionó en las pruebas reportadas con el POS;
la restricción exacta del firmware aún no está confirmada. El nuevo formato
debe comprobarse en el mismo terminal.
El ticket se imprime y se guarda ANTES del envío. Se comprueba su
unicidad en el registro; si hay una colisión se genera otro antes de enviar.
Puedes especificar `--ticket` manualmente si lo necesitas; se rechazan los ya
registrados. Usa el ticket mostrado por `history` en los comandos `resolve` de
abajo; `PRUEBA-001` es solo un ejemplo para intentos antiguos con ese nombre.
No incluye anulaciones/devoluciones automáticas.
Si quieres probar cancelación, inicia una venta y cancélala desde el propio POS
antes de completarla. Este cliente aún no cancela concurrentemente desde la caja.

## 6. Recuperación tras timeout, desconexión o Ctrl+C

No vuelvas a iniciar una venta hasta determinar si el POS cobró. Consulta:

```bash
.venv/bin/python -m getnet_serial.demo history
.venv/bin/python -m getnet_serial.demo last --port /dev/ttyACM0
```

El archivo `.getnet-tests.sqlite3` guarda monto, ticket, estado y respuesta final.
Un intento pendiente o incierto bloquea nuevas ventas incluso después de reiniciar
Python. Mantén el mismo archivo/ruta entre ejecuciones; no lo borres para desbloquear.
Es un registro de pruebas para un único terminal; no sustituye la gestión de
pedidos/conciliación de tu aplicación de producción.

Coteja el comprobante con terminal, tipo de operación, ticket, monto, fecha y
autorización disponibles. Si `last` devuelve otra operación, eso NO demuestra
que la venta pendiente no se cobró; verifica el resultado con Getnet si no puedes
resolverlo con el POS.

Solo después de confirmar el resultado, registra la conciliación manual:

```bash
.venv/bin/python -m getnet_serial.demo resolve \
  --ticket PRUEBA-001 \
  --outcome approved \
  --note "Pago confirmado: terminal ..., operación ..., autorización ..."
```

Usa `--outcome not-paid` únicamente si confirmaste que no hubo pago. `resolve`
solo actualiza el registro local; no cobra, cancela ni devuelve dinero.

## Pruebas del código sin POS

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_serial*offline.py' -v
```

No ejecutes todos los tests del repositorio para este diagnóstico: algunos tests
HTTP preexistentes pueden iniciar ventas al importarse.
