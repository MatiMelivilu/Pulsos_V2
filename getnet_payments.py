"""Ventas GPIO con un único trabajador serial y registro de pagos Getnet."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import sqlite3
import threading
import time

from getnet_serial import POS, UncertainResult, new_ticket
from getnet_serial.protocol import ProtocolError, unwrap


OPEN_STATES = ('pending', 'uncertain', 'approved', 'dispensing', 'pulses_failed')
REVIEW_STATES = OPEN_STATES + ('unresolved_released',)


class PaymentJournal:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS payments (
                ticket TEXT PRIMARY KEY, port TEXT NOT NULL, channel INTEGER NOT NULL,
                amount INTEGER NOT NULL, pulses INTEGER NOT NULL, created TEXT NOT NULL,
                state TEXT NOT NULL, response TEXT, note TEXT
            )''')
            db.execute('''CREATE TABLE IF NOT EXISTS recovery_actions (
                ticket TEXT NOT NULL, action TEXT NOT NULL, state TEXT NOT NULL,
                request TEXT, response TEXT, note TEXT, PRIMARY KEY(ticket, action)
            )''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def pending(self):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                'SELECT * FROM payments WHERE state IN (?,?,?,?,?) ORDER BY created LIMIT 1',
                OPEN_STATES,
            ).fetchone()
            return dict(row) if row else None

    def reserve(self, port, channel, amount, pulses):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM payments WHERE state IN (?,?,?,?,?)', OPEN_STATES).fetchone():
                raise RuntimeError('Hay un pago pendiente de revisión; no se envió otra venta.')
            for _ in range(10):
                ticket = new_ticket()
                if not db.execute('SELECT 1 FROM payments WHERE ticket=?', (ticket,)).fetchone():
                    break
            else:
                raise RuntimeError('No se pudo reservar un ticket único.')
            db.execute('INSERT INTO payments VALUES (?,?,?,?,?,?,?,?,?)', (
                ticket, port, channel, amount, pulses,
                datetime.now(timezone.utc).isoformat(), 'pending', None, None,
            ))
        return ticket

    def update(self, ticket, state, response=None, note=None):
        with self.connect() as db:
            db.execute('UPDATE payments SET state=?, response=COALESCE(?,response), note=? WHERE ticket=?',
                       (state, json.dumps(response, ensure_ascii=False) if response is not None else None,
                        note, ticket))

    def resolve(self, ticket, outcome, note):
        if outcome not in ('not-paid', 'served-manually') or not note.strip():
            raise ValueError('Indica un resultado y la evidencia de la revisión.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state FROM payments WHERE ticket=?', (ticket,)).fetchone()
            if row is None or row[0] not in REVIEW_STATES:
                raise ValueError('El ticket no existe o ya está resuelto.')
            if row[0] in ('approved', 'dispensing', 'pulses_failed') and outcome == 'not-paid':
                raise ValueError('El pago fue aprobado; revisa y completa la entrega manualmente.')
            db.execute('UPDATE payments SET state=?, note=? WHERE ticket=?',
                       (outcome, note, ticket))

    def recovery_action(self, ticket, action):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM recovery_actions WHERE ticket=? AND action=?',
                             (ticket, action)).fetchone()
            return dict(row) if row else None

    def known_completed_voucher(self, payment, voucher):
        """Identifica el comprobante de una venta anterior atendida en este POS."""
        if not isinstance(voucher, dict) or voucher.get('Ticket') == payment['ticket']:
            return False
        if (type(voucher.get('FunctionCode')) is not int or voucher['FunctionCode'] != 100
                or type(voucher.get('ResponseCode')) is not int or voucher['ResponseCode'] != 0):
            return False
        with self.connect() as db:
            row = db.execute('''SELECT response,amount FROM payments
                             WHERE ticket=? AND port=? AND state='completed' AND created<?''',
                             (voucher.get('Ticket'), payment['port'], payment['created'])).fetchone()
        if not row or not row[0]:
            return False
        original = json.loads(row[0])
        if (type(voucher.get('Amount')) is not int or voucher['Amount'] != row[1]
                or original.get('Amount') != row[1]
                or type(voucher.get('OperationId')) is not int or voucher['OperationId'] <= 0):
            return False
        identity = ('Ticket', 'TerminalId', 'OperationId', 'AuthorizationCode', 'AccountingDate')
        return all(voucher.get(key) not in (None, '') and voucher[key] == original.get(key)
                   for key in identity)

    def begin_recovery_action(self, ticket, action, request):
        # Persistir ANTES del comando; un reinicio no debe repetir una anulación.
        with self.connect() as db:
            cursor = db.execute('''INSERT OR IGNORE INTO recovery_actions
                                (ticket,action,state,request) VALUES (?,?,'sent',?)''',
                                (ticket, action, json.dumps(request, ensure_ascii=False)))
            return cursor.rowcount == 1

    def finish_recovery_action(self, ticket, action, state, response=None, note=None):
        with self.connect() as db:
            db.execute('UPDATE recovery_actions SET state=?,response=?,note=? WHERE ticket=? AND action=?',
                       (state, json.dumps(response, ensure_ascii=False) if response is not None else None,
                        note, ticket, action))


class PaymentController:
    """Todas las llamadas al POS y las entregas se ejecutan secuencialmente."""

    def __init__(self, port, baudrate, journal_path, deliver, *, on_selected=None,
                 events=None, pos_factory=POS, retry_interval=1, poll_interval=3,
                 sale_timeout=120, print_on_pos=False, auto_recovery=True, recovery_interval=30):
        self.port = port
        self.baudrate = baudrate
        self.journal = PaymentJournal(journal_path)
        self.deliver = deliver
        self.on_selected = on_selected or (lambda channel: None)
        self.events = events if events is not None else queue.Queue()
        self.pos_factory = pos_factory
        self.retry_interval = retry_interval
        self.poll_interval = poll_interval
        self.sale_timeout = sale_timeout
        self.print_on_pos = print_on_pos
        self.auto_recovery = auto_recovery
        self.recovery_interval = recovery_interval
        self._next_recovery = 0
        self._recovery_notice = None
        self._late_sale_response = None
        self.lock = threading.Lock()
        self.jobs = queue.Queue()
        self.stop_event = threading.Event()
        self.pos = None
        self.connected = False
        self._automatic_poll = False
        self._connection_notice = None
        self.busy = False
        self.recovering = False
        self.blocked = self.journal.pending()
        self.thread = threading.Thread(target=self.run, daemon=True, name='GetnetPOS')

    def emit(self, kind, **data):
        self.events.put({'type': kind, **data})

    def log(self, message):
        self.emit('log', message=message)

    def state(self):
        with self.lock:
            self.emit('state', connected=self.connected, busy=self.busy, blocked=self.blocked,
                      recovering=self.recovering)

    def start(self):
        self.state()
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        pos = self.pos
        if pos is not None:
            try:
                pos.close()  # Interrumpe la lectura si se cierra durante una venta.
            except OSError:
                pass
        if self.thread.is_alive():
            self.thread.join(timeout=5)

    def submit_sale(self, channel, amount, pulses):
        if type(channel) is not int or not 1 <= channel <= 4:
            raise ValueError('Canal inválido.')
        if type(amount) is not int or not 0 < amount <= 9007199254740991:
            raise ValueError('El monto debe ser un entero positivo en pesos CLP.')
        if type(pulses) is not int or pulses < 0:
            raise ValueError('La cantidad de pulsos debe ser un entero no negativo.')
        return self.submit(('sale', channel, amount, pulses))

    def submit_command(self, command):
        if command not in ('poll', 'last', 'close'):
            raise ValueError('Comando Getnet no admitido.')
        return self.submit((command,), allow_blocked=command in ('poll', 'last'))

    def submit_test(self, operation):
        return self.submit(('test', operation))

    def ready(self):
        with self.lock:
            return self.connected and not self.busy and not self.blocked and not self.stop_event.is_set()

    def submit(self, job, allow_blocked=False):
        with self.lock:
            if self.stop_event.is_set() or self.busy:
                return False
            if self.blocked and not allow_blocked:
                return False
            if not self.connected:
                return False
            self.busy = True
            self.jobs.put(job)
        self.state()
        return True

    @staticmethod
    def poll_ok(response):
        return (type(response.get('FunctionCode')) is int and response['FunctionCode'] == 106
                and type(response.get('ResponseCode')) is int and response['ResponseCode'] == 0
                and response.get('Connected') is True)

    def disconnect(self, reason=None):
        with self.lock:
            self.connected = False
        pos = self.pos
        if pos is not None:
            try:
                pos.close()
            except OSError:
                pass
        self.pos = None
        if reason is not None and self._connection_notice is not False:
            self.log(f'POS Getnet desconectado: {reason}. Reconectando automáticamente…')
            self._connection_notice = False
        self.state()

    def receive_raw(self, raw, outer):
        # Preservar el JSON exactamente entre sus llaves, incluyendo JsonSerialized,
        # Sign, espacios y saltos de línea; el polling automático no llena el log.
        try:
            message = unwrap(outer)
        except ProtocolError:
            message = outer
        if self._automatic_poll and (message.get('FunctionCode') == 106 or 'Received' in message):
            return
        self.log(f'Getnet RX: {raw}')

    def receive_message(self, message):
        # Recibir mensajes de progreso no habilita entradas ni entrega pulsos.
        if self._automatic_poll and message.get('FunctionCode') == 106:
            return
        if (self.blocked and self.blocked.get('state') in ('pending', 'uncertain')
                and message.get('FunctionCode') == 100
                and message.get('Ticket') == self.blocked.get('ticket')):
            self._late_sale_response = message
        self.log('Getnet respuesta: ' + json.dumps(message, ensure_ascii=False))

    def automatic_poll(self):
        self._automatic_poll = True
        try:
            return self.pos.poll()
        finally:
            self._automatic_poll = False

    def run(self):
        try:
            while not self.stop_event.is_set():
                if self.pos is None:
                    try:
                        self.pos = self.pos_factory(self.port, baudrate=self.baudrate,
                                                    on_message=self.receive_message,
                                                    on_raw=self.receive_raw)
                        response = self.automatic_poll()
                        if not self.poll_ok(response):
                            raise OSError('Getnet no confirmó conexión en el polling.')
                        with self.lock:
                            self.connected = True
                        if self._connection_notice is not True:
                            self.log(f'POS Getnet conectado: {self.port}. '
                                     + json.dumps(response, ensure_ascii=False))
                            self._connection_notice = True
                        self.state()
                    except Exception as exc:
                        self.disconnect(reason=str(exc))
                        self.stop_event.wait(self.retry_interval)
                        continue
                if (self.auto_recovery and self.blocked
                        and self.blocked.get('state') in ('pending', 'uncertain')
                        and time.monotonic() >= self._next_recovery):
                    self.recover_payment()
                    if self.pos is None:
                        continue
                try:
                    job = self.jobs.get(timeout=self.poll_interval)
                except queue.Empty:
                    try:
                        if not self.poll_ok(self.automatic_poll()):
                            raise OSError('El POS dejó de responder como conectado.')
                    except Exception as exc:
                        self.disconnect(reason=str(exc))
                    continue
                try:
                    if self.stop_event.is_set():
                        continue
                    if job[0] == 'sale':
                        self.sell(*job[1:])
                    elif job[0] == 'test':
                        job[1]()
                    else:
                        if job[0] == 'poll':
                            response = self.pos.poll()
                            if not self.poll_ok(response):
                                raise OSError('Getnet no confirmó conexión.')
                        elif job[0] == 'last':
                            response = self.pos.last_voucher()
                        else:
                            response = self.pos.request(103, PrintOnPos=True)
                        self.emit('command_result', command=job[0], response=response)
                except Exception as exc:
                    self.log(f'Error Getnet: {exc}')
                    self.disconnect(reason=str(exc))
                finally:
                    try:
                        pending = self.journal.pending()
                    except sqlite3.Error as exc:
                        pending = {'ticket': 'registro inaccesible', 'state': 'uncertain'}
                        self.log(f'No se pudo comprobar el registro de pagos: {exc}')
                    with self.lock:
                        self.busy = False
                        self.blocked = pending
                    self.state()
        finally:
            self.disconnect()

    def sell(self, channel, amount, pulses):
        self.on_selected(channel)
        ticket = self.journal.reserve(self.port, channel, amount, pulses)
        with self.lock:
            self.blocked = {'ticket': ticket, 'channel': channel, 'amount': amount, 'state': 'pending'}
        self.log(f'Venta Getnet CH{channel}: ${amount}, {pulses} pulsos, ticket {ticket}')
        try:
            response = self.pos.sale(amount, ticket, print_on_pos=self.print_on_pos,
                                     timeout=self.sale_timeout)
            if (type(response.get('FunctionCode')) is not int or response['FunctionCode'] != 100
                    or type(response.get('ResponseCode')) is not int):
                raise UncertainResult('Respuesta no corresponde a una venta final.', response=response)
            if response.get('Ticket') not in (None, '', ticket):
                raise UncertainResult('Ticket diferente al de la venta.', response=response)
            approved = response['ResponseCode'] == 0
            if approved and (type(response.get('Amount')) is not int or response['Amount'] != amount):
                raise UncertainResult('Monto diferente al de la venta.', response=response)
        except Exception as exc:
            self.journal.update(ticket, 'uncertain', getattr(exc, 'response', None), str(exc))
            with self.lock:
                self.blocked = self.journal.pending()
            self.log(f'Pago {ticket} sin resultado confirmado; intentando cancelar automáticamente al reconectar.')
            raise

        if not approved:
            self.journal.update(ticket, 'rejected', response)
            with self.lock:
                self.blocked = None
            self.log(f'Pago rechazado/cancelado: {response.get("ResponseMessage", response["ResponseCode"])}')
            return

        # Persistir la aprobación ANTES de activar salidas. Tras un corte de energía
        # no se repite el cobro ni se repite una entrega posiblemente parcial.
        self.journal.update(ticket, 'approved', response)
        self.journal.update(ticket, 'dispensing')
        try:
            if self.stop_event.is_set():
                raise RuntimeError('La aplicación se cerró antes de entregar los pulsos.')
            self.deliver(channel, pulses)
        except Exception as exc:
            self.journal.update(ticket, 'pulses_failed', note=str(exc))
            with self.lock:
                self.blocked = self.journal.pending()
            self.log(f'Pago {ticket} aprobado; revisar entrega incompleta de pulsos: {exc}')
            return
        self.journal.update(ticket, 'completed')
        with self.lock:
            self.blocked = None
        self.log(f'Pago aprobado: CH{channel}, {pulses} pulsos entregados, ticket {ticket}')

    @staticmethod
    def matching_sale(response, payment):
        if not isinstance(response, dict):
            return False
        if (type(response.get('FunctionCode')) is not int or response['FunctionCode'] != 100
                or type(response.get('ResponseCode')) is not int
                or response.get('Ticket') != payment['ticket']):
            return False
        amount = response.get('Amount')
        if response['ResponseCode'] == 0:
            return type(amount) is int and amount == payment['amount']
        return amount is None or (type(amount) is int and amount in (0, payment['amount']))

    @staticmethod
    def confirmed_void(response, payment):
        return (isinstance(response, dict)
                and type(response.get('FunctionCode')) is int and response['FunctionCode'] == 102
                and type(response.get('ResponseCode')) is int and response['ResponseCode'] == 0
                and response.get('Ticket') == payment['ticket']
                and type(response.get('Amount')) is int and response['Amount'] == payment['amount'])

    def recovery_wait(self, text):
        if text != self._recovery_notice:
            self.log(text)
            self._recovery_notice = text

    def resolve_recovery(self, payment, state, response):
        self.journal.update(payment['ticket'], state, response,
                            'Resultado confirmado durante la recuperación automática.')
        with self.lock:
            self.blocked = None
        self.pos.confirm_reconciled()
        self.log(f"Pago {payment['ticket']} cancelado/anulado confirmado. POS habilitado sin enviar pulsos.")

    def void_recovered_sale(self, payment, sale):
        ticket = payment['ticket']
        previous = self.journal.recovery_action(ticket, 'void')
        if previous and previous['state'] == 'confirmed':
            self.resolve_recovery(payment, 'voided', json.loads(previous['response']))
            return
        operation_id = sale.get('OperationId')
        if type(operation_id) is not int or operation_id <= 0:
            self.recovery_wait(f'Pago {ticket} cobrado sin OperationId válido; no se puede anular automáticamente.')
            return
        request = {'Command': 102, 'OperationId': operation_id, 'PrintOnPos': False}
        if not self.journal.begin_recovery_action(ticket, 'void', request):
            self.recovery_wait(f'Anulación {ticket} ya enviada sin confirmación; no se repite el comando.')
            return
        self.log(f'Anulando automáticamente pago {ticket}, operación {operation_id}.')
        try:
            result = self.pos.request(102, OperationId=operation_id, PrintOnPos=False, timeout=30)
            confirmed = (type(result.get('FunctionCode')) is int and result['FunctionCode'] == 102
                         and type(result.get('ResponseCode')) is int and result['ResponseCode'] == 0
                         and result.get('Ticket') in (None, '', ticket)
                         and (result.get('Amount') is None or
                              (type(result.get('Amount')) is int
                               and result['Amount'] in (0, payment['amount']))))
            self.journal.finish_recovery_action(ticket, 'void', 'confirmed' if confirmed else 'failed', result)
        except Exception as exc:
            self.journal.finish_recovery_action(ticket, 'void', 'uncertain',
                                                 getattr(exc, 'response', None), str(exc))
            raise
        if confirmed:
            self.resolve_recovery(payment, 'voided', result)
        else:
            self.recovery_wait(f'El POS no confirmó la anulación de {ticket}; se mantiene pendiente.')

    def apply_recovery_evidence(self, payment, response):
        if self.matching_sale(response, payment):
            if response['ResponseCode'] == 0:
                self.void_recovered_sale(payment, response)
            else:
                self.resolve_recovery(payment, 'cancelled', response)
            return True
        if self.confirmed_void(response, payment):
            self.resolve_recovery(payment, 'voided', response)
            return True
        return False

    def release_idle_terminal(self, payment, voucher):
        action = self.journal.recovery_action(payment['ticket'], 'cancel')
        if not action or not action['response']:
            return False
        cancellation = json.loads(action['response'])
        if (type(cancellation.get('FunctionCode')) is not int or cancellation['FunctionCode'] != 116
                or type(cancellation.get('ResponseCode')) is not int or cancellation['ResponseCode'] != 999
                or 'no hay venta en curso' not in str(cancellation.get('ResponseMessage', '')).casefold()
                or not self.journal.known_completed_voucher(payment, voucher)):
            return False
        # Política de disponibilidad de la expendedora: liberar el bloqueo operativo
        # sin afirmar que el intento no fue cobrado. La incertidumbre queda persistida.
        self.journal.update(payment['ticket'], 'unresolved_released', {
            'Cancellation': cancellation, 'LastVoucher': voucher,
        }, 'POS sin venta en curso y último comprobante de una venta anterior completada. '
           'Bloqueo operativo liberado; resultado financiero todavía incierto, requiere conciliación.')
        with self.lock:
            self.blocked = None
        self.log(f"Pago {payment['ticket']}: POS sin venta en curso y comprobante anterior verificado. "
                 'Bloqueo operativo liberado SIN pulsos; pago conservado como incierto para conciliación.')
        # Abrir una sesión limpia antes de habilitar otra selección. No se declara
        # confirm_reconciled(), porque no se ha confirmado el resultado financiero.
        self.disconnect()
        return True

    def recover_payment(self):
        with self.lock:
            if self.busy or self.stop_event.is_set():
                return
            self.busy = True
            self.recovering = True
        self.state()
        self._next_recovery = time.monotonic() + self.recovery_interval
        try:
            payment = self.journal.pending()
            if not payment or payment['state'] not in ('pending', 'uncertain'):
                return
            if payment['port'] != self.port:
                self.recovery_wait('Recuperación pendiente: conecta el mismo POS y puerto del intento original.')
                return
            self.recovery_wait(f"Recuperando automáticamente pago {payment['ticket']}: consultando/cancelando en el POS.")
            action = self.journal.recovery_action(payment['ticket'], 'void')
            if action and action['state'] == 'confirmed':
                self.resolve_recovery(payment, 'voided', json.loads(action['response']))
                return
            if action:
                # Tras una anulación sin respuesta, consultar antes de considerar
                # evidencia vieja de aprobación. Nunca reenviar la anulación.
                voucher = self.pos.last_voucher(timeout=15)
                self.apply_recovery_evidence(payment, voucher)
                return
            if self.apply_recovery_evidence(payment, self._late_sale_response):
                return
            if self.journal.begin_recovery_action(payment['ticket'], 'cancel', {'Command': 116}):
                self.log(f"Cancelando automáticamente operación pendiente {payment['ticket']} (Getnet 116).")
                try:
                    response = self.pos.cancel_sale(timeout=15)
                    ok = (type(response.get('FunctionCode')) is int and response['FunctionCode'] == 116
                          and type(response.get('ResponseCode')) is int and response['ResponseCode'] == 0)
                    self.journal.finish_recovery_action(payment['ticket'], 'cancel',
                                                         'confirmed' if ok else 'failed', response)
                except Exception as exc:
                    self.journal.finish_recovery_action(payment['ticket'], 'cancel', 'uncertain',
                                                         getattr(exc, 'response', None), str(exc))
                    raise
                if self.apply_recovery_evidence(payment, self._late_sale_response):
                    return
            voucher = self.pos.last_voucher(timeout=15)
            if self.apply_recovery_evidence(payment, voucher):
                return
            if self.release_idle_terminal(payment, voucher):
                return
            self.recovery_wait(f"Pago {payment['ticket']}: cancelación solicitada, pero falta confirmar "
                               'el resultado de ese ticket. Otro comprobante no confirma ausencia de cobro.')
        except Exception as exc:
            self.recovery_wait(f'Recuperación automática sin confirmar: {exc}')
            self.disconnect(reason=str(exc))
        finally:
            try:
                pending = self.journal.pending()
            except sqlite3.Error:
                pending = {'ticket': 'registro inaccesible', 'state': 'uncertain'}
            with self.lock:
                self.blocked = pending
                self.busy = False
                self.recovering = False
            self.state()


def main():
    parser = argparse.ArgumentParser(description='Consultar o resolver el registro local de pagos de la app.')
    parser.add_argument('--journal', default=str(Path(__file__).with_name('pagos_getnet.sqlite3')))
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('history')
    commands.add_parser('review', help='Listar resultados financieros inciertos liberados operativamente')
    resolve = commands.add_parser('resolve')
    resolve.add_argument('--ticket', required=True)
    resolve.add_argument('--outcome', choices=('not-paid', 'served-manually'), required=True)
    resolve.add_argument('--note', required=True)
    args = parser.parse_args()
    journal = PaymentJournal(args.journal)
    if args.command == 'resolve':
        journal.resolve(args.ticket, args.outcome, args.note)
        print('Revisión registrada. No se enviaron cobros ni pulsos. Reinicia la app.')
    else:
        with journal.connect() as db:
            db.row_factory = sqlite3.Row
            query = ("SELECT * FROM payments WHERE state='unresolved_released' ORDER BY created"
                     if args.command == 'review' else 'SELECT * FROM payments ORDER BY created DESC LIMIT 50')
            for row in db.execute(query):
                print(json.dumps(dict(row), ensure_ascii=False))


if __name__ == '__main__':
    main()
