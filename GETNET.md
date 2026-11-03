# Operación de Pulsos V2 con Getnet

`interfaz_pulsos_optoacoplada.py` utiliza el paquete local `getnet_serial` para la comunicación Getnet USB. `getnet_payments.py` coordina la conexión, las ventas y la entrega de pulsos. No se envían los mensajes Transbank `0200`, `0210` ni sus ACK/LRC desde esta aplicación.

## Preparación

1. Copia también `getnet_payments.py` y la carpeta `getnet_serial/` junto a la aplicación.
2. Instala las dependencias siguiendo [README.md](README.md): `python -m pip install -r requirements.txt` con el Python que ejecutará la app.
3. Configura el POS Getnet en modo integrado USB y ajusta `SERIAL_PORT` y `BAUD_RATE` al inicio de la aplicación. Usa `/dev/serial/by-id/...` si el terminal publica una ruta estable.
4. Mantén `n_inputs = 4` y cuatro líneas `monto pulsos` en `valores.txt`. Los montos son pesos CLP enteros, sin multiplicar por 100.
5. Inicia la aplicación desde el escritorio y espera **POS Getnet conectado**.

Puedes comprobar el puerto sin iniciar una venta, con la aplicación cerrada para liberar el USB:

```bash
cd ~/Pulsos_V2
.venv/bin/python -m getnet_serial.demo ports
.venv/bin/python -m getnet_serial.demo poll --port /dev/ttyACM0
```

Sustituye el puerto del ejemplo por el real. La respuesta de conexión esperada es `FunctionCode: 106`, `ResponseCode: 0` y `Connected: true`. La implementación del protocolo y sus detalles están en [getnet_serial/README.md](getnet_serial/README.md).

## Flujo de venta

- Una entrada GPIO elige CH1 a CH4. Se capturan el monto y los pulsos de esa línea de `valores.txt`; editar el archivo después no altera la operación en curso.
- Se bloquean las demás selecciones y comandos mientras hay una operación. Se mantiene la señal de selección del canal que usaba la aplicación anterior.
- Se genera un ticket único de 10 caracteres y se guarda el intento antes de enviar `POS.sale()`, comando Getnet 100.
- Los mensajes de recepción y progreso se muestran en el log y no entregan pulsos.
- Una respuesta final de venta con `FunctionCode: 100` y `ResponseCode: 0`, validada contra el monto y el ticket disponibles, produce la señal de salida del canal y los pulsos configurados en GPIO16. Se registra la entrega y se habilita otra selección.
- Una respuesta final rechazada/cancelada no produce pulsos en GPIO16 y vuelve a habilitar las entradas.

La selección, el canal de entrega y la cantidad de pulsos permanecen asociados al mismo intento. La espera de respuesta final se configura con `SALE_TIMEOUT` (120 segundos por defecto); la librería también aplica su timeout de confirmación de recepción. `PRINT_ON_POS = False` evita imprimir la venta en el terminal; cámbialo si necesitas esa impresión.

El estado del POS se consulta aproximadamente cada tres segundos cuando no hay una operación. Si el USB desaparece o el POS deja de responder, se cierra la conexión y se vuelve a abrir con reintentos. Una desconexión entre ventas permite recuperar el funcionamiento sin reiniciar la app. La ruta configurada debe volver a corresponder al mismo terminal.

## Respuestas del POS en el log

La pantalla y `log/log_AAAAMMDD.txt` incluyen estos registros para los mensajes de las operaciones:

- `Getnet RX: {...}` contiene el objeto JSON original recibido por USB, conservando campos, espacios, saltos de línea y escapes dentro del objeto. Cuando está firmado incluye `JsonSerialized` y `Sign`. Las lecturas USB fragmentadas se unen antes de registrarlo. Es una captura de recepción previa a la validación de firma, por lo que su presencia no significa que el pago esté aprobado.
- `Getnet respuesta: {...}` contiene todos los campos del mensaje decodificado después de su validación, incluyendo campos adicionales del terminal. Permite extraer monto, ticket, códigos y demás datos sin limitar el registro a `ResponseMessage`.

El polling **automático** no imprime cada respuesta 106 ni sus confirmaciones de recepción: se informa la conexión cuando se establece y la desconexión una sola vez mientras se reintenta. Al recuperar la comunicación se registra nuevamente la conexión. Un `ResponseMessage` de “Aprobado” en un polling no es una venta aprobada. El botón **Poll**, solicitado manualmente, sí registra la respuesta para diagnóstico. Los mensajes de otras operaciones que lleguen durante un polling se conservan.

## Controles de la interfaz

| Botón | Operación |
| --- | --- |
| Cierre de caja | Getnet 103 con impresión en POS. No entrega pulsos. |
| Último comprobante | Getnet 101; muestra los datos retornados. No cobra ni entrega pulsos. |
| Poll | Getnet 106; comprueba conexión. |
| test venta | Venta real de $500 por CH1; un pulso si se aprueba. Comparte el registro y el bloqueo de ventas. |
| test pulso | Un pulso GPIO16 sin iniciar pago, cuando el POS está disponible y no hay operación pendiente. |
| CH1_ON a CH4_ON | Prueba de la salida del canal, serializada con las operaciones. |

Se reemplazó **Carga llaves** por **Último comprobante**: la librería recibida no ofrece un equivalente a la carga de llaves Transbank.

## Resultado incierto o entrega interrumpida

Un timeout o una desconexión durante el cobro no permite saber si hubo pago. La app reconecta el puerto y, con `AUTO_RECOVERY = True` (predeterminado), **intenta cancelar o anular automáticamente el intento**, sin repetir la venta ni entregar pulsos. Mientras confirma el resultado mantiene bloqueadas las entradas.

La recuperación funciona también para intentos pendientes recuperados después de reiniciar:

1. Tras conectar, se envía Getnet 116 para cancelar la operación pendiente. Se capturan respuestas finales tardías y se consulta el último comprobante.
2. Si se confirma el rechazo/cancelación de una venta con el mismo ticket, se registra el resultado y se habilita otra selección.
3. Si aparece una venta aprobada con el mismo ticket y monto, se solicita su anulación mediante Getnet 102 y su `OperationId`. Solo la confirmación de anulación habilita otra selección. No se envían pulsos para ese pago.
4. Si no hay confirmación, se vuelve a consultar a intervalos de `RECOVERY_INTERVAL` (30 segundos por defecto). Cada comando de cancelación o anulación se registra antes de enviarse; no se reenvía automáticamente después de un timeout o reinicio. Una anulación sin respuesta puede confirmarse después con un comprobante 102 que coincida en ticket y monto.

Un ACK de cancelación 116, un polling exitoso o un comprobante de otro ticket no demuestran que el intento original no se cobró. En esos casos se mantiene pendiente y se necesita la revisión que se describe abajo. La rutina no utiliza coincidencias solo por monto ni anula operaciones de otro ticket. La comunicación y la respuesta real de 116/102 deben validarse en el firmware del terminal: podrían exigir interacción en el POS o rechazar la anulación.

Para mantener la disponibilidad de la expendedora existe una excepción operativa: si Getnet responde a 116 con código 999 y **“No hay venta en curso”**, y el último comprobante coincide con una venta anterior **completada** del registro local, se retira el bloqueo operativo. Se verifican ticket, puerto, terminal, operación, autorización, fecha contable y monto de esa venta anterior. No basta un ticket desconocido, un código 999 genérico o una coincidencia de monto.

El intento queda en estado `unresolved_released`: **no está confirmado como impago ni anulado**, no entrega pulsos y requiere conciliación financiera posterior. La conexión se vuelve a abrir antes de permitir nuevas selecciones. Esta política prioriza la disponibilidad del equipo y permite que exista un cobro sin resolver; el registro no se borra ni se marca falsamente como cancelación confirmada. Para listar todos esos casos:

```bash
.venv/bin/python getnet_payments.py review
```

Después de verificar el resultado real, se pueden conciliar con `resolve` igual que los demás intentos. No se anula el comprobante anterior, que corresponde a un cliente ya atendido.

También conserva el bloqueo si el pago fue aprobado pero falló la entrega de pulsos o se cortó la energía antes de registrar su finalización. No hay un sensor que confirme la cantidad físicamente entregada después de un corte: revisa la máquina antes de repetir o completar la entrega.

El registro está en `pagos_getnet.sqlite3`, junto a `getnet_payments.py`. No es el archivo `.getnet-tests.sqlite3` de las pruebas CLI de la librería. Para revisar la operación:

1. Anota el ticket, monto y canal mostrados en el estado/log.
2. Una vez reconectado, usa **Último comprobante** y verifica tipo de operación, ticket, monto, terminal y demás datos disponibles. Un comprobante de otra operación no demuestra que el intento pendiente no se cobró.
3. Confirma el resultado con el terminal/Getnet. Si hubo pago, verifica la entrega y atiende o completa el servicio manualmente, sin volver a cobrar.
4. Si la recuperación automática no pudo confirmar el resultado, cierra la aplicación y registra la revisión con una nota de evidencia.

Consultar el registro local no envía comandos al POS:

```bash
cd ~/Pulsos_V2
.venv/bin/python getnet_payments.py history
```

Solo si confirmaste que no hubo pago:

```bash
.venv/bin/python getnet_payments.py resolve \
  --ticket TICKET_REAL \
  --outcome not-paid \
  --note "Cancelación confirmada en el terminal; referencia y fecha ..."
```

Si confirmaste el pago y el cliente ya recibió el servicio completo manualmente o verificaste que la entrega ya estaba completa:

```bash
.venv/bin/python getnet_payments.py resolve \
  --ticket TICKET_REAL \
  --outcome served-manually \
  --note "Pago confirmado, operación ..., autorización ..., servicio completado ..."
```

Sustituye `TICKET_REAL` y la nota por los datos verificados. La resolución solo actualiza el registro; no cobra, devuelve dinero ni envía pulsos. Un pago registrado como aprobado o con entrega interrumpida no admite `not-paid`. Luego reinicia la aplicación. No borres el registro ni uses `getnet_serial.demo resolve` para desbloquear la app: ese comando actúa sobre otro registro.

## Validación antes de cambiar el equipo en servicio

```bash
.venv/bin/python -m unittest -v test_getnet_recovery test_getnet_payments test_wifi_touch
```

Las pruebas simulan el transporte USB y usan el cliente Getnet real para construir y procesar mensajes firmados, incluso fragmentados. Cubren aprobación, rechazo, progreso, respuesta inválida, entradas simultáneas, registro previo al envío, desconexión, reconexión, recuperación después de reiniciar y fallo de entrega. No requieren POS ni GPIO físicos.

La librería recibida y esta integración aún requieren validar el comportamiento del firmware del terminal real. En la Raspberry comprueba, con el ambiente de prueba acordado para el POS, cada CH, los pulsos exactos en aprobación, ausencia de pulsos en rechazo y reconexión al retirar/reinsertar el cable entre operaciones. Durante un cobro, una desconexión debe dejar el intento pendiente de revisión, sin reenvío automático.
