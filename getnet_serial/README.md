# Getnet USB serial para Python / Linux ARM

Port del transporte USB y firma del SDK fuente en `Node.JS/getnet_posintegrado`.
No requiere DLL, API HTTP ni el archivo Windows `pos.config`. Configura el puerto
Linux explícitamente; preferir `/dev/serial/by-id/...` cuando esté disponible.
El POS debe estar configurado en modo integrado. No se ha validado con un POS
físico: las pruebas usan transporte simulado y una firma del manual.

Para probar con un POS conectado, sigue [PRUEBAS.md](PRUEBAS.md). Incluye CLI
para listar puertos, consultar conexión/comprobante y probar una venta con un
registro persistente que bloquea reintentos mientras exista un resultado incierto.

## Instalación y prueba de conexión

Desde la raíz del repositorio:

```bash
python3 -m venv .venv
.venv/bin/pip install -r getnet_serial/requirements.txt
ls -l /dev/serial/by-id/
```

El usuario que ejecuta Python necesita permiso para abrir el puerto serial.
En Debian/Raspberry Pi OS normalmente se concede mediante el grupo `dialout`.

```python
from getnet_serial import POS

with POS("/dev/ttyACM0") as pos:  # Sustituir por el puerto real.
    response = pos.poll()
    print(response)
    assert response.get("ResponseCode") == 0 and response.get("Connected") is True
```

## Venta (inicia un cobro real)

Registra y persiste el intento y bloquea dobles clics en tu aplicación ANTES de
enviar. Usa un ticket distinto para cada intento; Getnet no documenta idempotencia
por ticket. Mantén una conexión y una operación por terminal.

El CLI genera y persiste automáticamente un ticket hexadecimal basado en UUID4 de 10 caracteres
si omites `--ticket`. En integraciones Python puedes usar `new_ticket()` y guardar
su resultado antes de llamar `sale()`. Si omites el ticket en `POS.sale()` se genera
uno, pero la persistencia previa sigue siendo responsabilidad de tu aplicación.

```python
from getnet_serial import POS, UncertainResult

with POS("/dev/ttyACM0", on_message=print) as pos:
    # Ejemplo: completar estos datos con un intento persistido por la aplicación.
    try:
        result = pos.sale(25000, "A1234", print_on_pos=False)
    except UncertainResult:
        # Persistir estado incierto. No repetir sale().
        last = pos.last_voucher()
        # Verificar tipo de transacción, ticket, monto, terminal y datos disponibles.
        # Un último comprobante diferente NO demuestra que el intento falló.
        # Persistir la resolución antes de llamar confirm_reconciled().
        print("Conciliar con el comprobante:", last)
    else:
        # Guardar el resultado y completar el pedido una sola vez.
        print("Aprobada" if result["ResponseCode"] == 0 else "Rechazada", result)
```

La protección `needs_reconciliation` vive en memoria: después de un reinicio,
la aplicación debe recuperar intentos pendientes de su base de datos ANTES de
permitir nuevas ventas. `last_voucher()` no desbloquea ventas automáticamente.
Los callbacks reciben mensajes intermedios y respuestas ajenas/tardías; no deben
bloquear, lanzar errores ni iniciar comandos. Solo el retorno de `sale()` es su
resultado final. Un checksum SHA-256 comprueba integridad; no autentica al emisor.

## Otros comandos del SDK

`request()` devuelve el objeto de respuesta sin perder campos adicionales:

```python
pos.request(102, OperationId=1234, PrintOnPos=False)  # Anulación antes del cierre
pos.request(103, PrintOnPos=False)                    # Cierre
pos.request(104, PrintOnPos=False)                    # Totales
pos.request(105, PrintOnPos=False)                    # Detalles
pos.request(107)                                      # Modo normal
pos.request(108, AuthorizationCode="ABC", Amount=25000, PrintOnPos=False)
pos.request(109, OperationId=1234, PrintOnPos=False)   # Duplicado
pos.request(110, EmployeeId=1, PrintOnPos=False)       # Ventas vendedor
pos.request(111, EmployeeId=1, PrintOnPos=False)       # Propinas
pos.request(113, SaleType=1)                          # Tipo predeterminado
pos.request(114, PrintOnPos=False)                    # Parámetros
pos.request(115)                                      # Reporte SIM
```

Estos ejemplos no deben ejecutarse como un lote. Los campos de comandos genéricos
deben validarse en la aplicación conforme al manual. La implementación es síncrona:
`cancel_sale()` (116) no puede ejecutarse mientras `sale()` está esperando. Para
cancelar en ese momento usa el POS; cancelación desde la caja requiere ampliar el
cliente para multiplexar venta y cancelación. No es una traducción de TCP/IP ni del
Agente POS de Windows.

## Cambios respecto del Node.js recibido

- Firma equivalente: JSON compacto UTF-8 → SHA-256 hexadecimal mayúscula →
  envoltura `JsonSerialized`/`Sign`, sin salto de línea.
- Recepción con buffer para objetos JSON fragmentados o concatenados y UTF-8
  dividido; no presupone un objeto completo por lectura serial.
- Confirmación `Received` separada del resultado final; mensajes intermedios no
  detienen el timeout del resultado. Respuestas firmadas se verifican.
- Puerto configurable sin dependencia de `C:/Program Files/Getnet/pos.config`.
- El comando de devolución es 108: el SDK Node recibido referencia por error
  `POSCommands.Function.authorizationCode`, que no existe.

## Pruebas offline

```bash
python3 -m unittest discover -s tests -p test_serial_offline.py -v
```

No ejecutar los tests HTTP preexistentes para comprobar este módulo: algunos
inician ventas al importarse. Antes de usar en producción, verificar contra el
simulador/POS real: conexión, aprobación, rechazo, mensajes intermedios, timeout,
desconexión y recuperación del último comprobante. El SDK fuente determina el
protocolo implementado; faltan esas comprobaciones en hardware.
