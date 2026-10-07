"""Ventas GPIO con un único trabajador serial y registro de pagos Getnet."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import sqlite3
import threading

from getnet_serial import POS, UncertainResult, new_ticket


OPEN_STATES = ('pending', 'uncertain', 'approved', 'dispensing', 'pulses_failed')


class PaymentJournal:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS payments (
                ticket TEXT PRIMARY KEY, port TEXT NOT NULL, channel INTEGER NOT NULL,
                amount INTEGER NOT NULL, pulses INTEGER NOT NULL, created TEXT NOT NULL,
                state TEXT NOT NULL, response TEXT, note TEXT
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
            if row is None or row[0] not in OPEN_STATES:
                raise ValueError('El ticket no existe o ya está resuelto.')
            if row[0] in ('approved', 'dispensing', 'pulses_failed') and outcome == 'not-paid':
                raise ValueError('El pago fue aprobado; revisa y completa la entrega manualmente.')
            db.execute('UPDATE payments SET state=?, note=? WHERE ticket=?',
                       (outcome, note, ticket))


class PaymentController:
    """Todas las llamadas al POS y las entregas se ejecutan secuencialmente."""

    def __init__(self, port, baudrate, journal_path, deliver, *, on_selected=None,
                 events=None, pos_factory=POS, retry_interval=1, poll_interval=3,
                 sale_timeout=120, print_on_pos=False):
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
        self.lock = threading.Lock()
        self.jobs = queue.Queue()
        self.stop_event = threading.Event()
        self.pos = None
        self.connected = False
        self.busy = False
        self.blocked = self.journal.pending()
        self.thread = threading.Thread(target=self.run, daemon=True, name='GetnetPOS')

    def emit(self, kind, **data):
        self.events.put({'type': kind, **data})

    def log(self, message):
        self.emit('log', message=message)

    def state(self):
        with self.lock:
            self.emit('state', connected=self.connected, busy=self.busy, blocked=self.blocked)

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

    def disconnect(self):
        with self.lock:
            self.connected = False
        pos = self.pos
        if pos is not None:
            try:
                pos.close()
            except OSError:
                pass
        self.pos = None
        self.state()

    def receive_message(self, message):
        # Recibir mensajes de progreso no habilita entradas ni entrega pulsos.
        text = message.get('MessageText') or message.get('ResponseMessage')
        if text:
            self.log(f'Getnet: {text}')

    def run(self):
        try:
            while not self.stop_event.is_set():
                if self.pos is None:
                    try:
                        self.pos = self.pos_factory(self.port, baudrate=self.baudrate,
                                                    on_message=self.receive_message)
                        if not self.poll_ok(self.pos.poll()):
                            raise OSError('Getnet no confirmó conexión en el polling.')
                        with self.lock:
                            self.connected = True
                        self.log(f'POS Getnet conectado: {self.port}')
                        self.state()
                    except Exception as exc:
                        self.log(f'Esperando reconexión Getnet: {exc}')
                        self.disconnect()
                        self.stop_event.wait(self.retry_interval)
                        continue
                try:
                    job = self.jobs.get(timeout=self.poll_interval)
                except queue.Empty:
                    try:
                        if not self.poll_ok(self.pos.poll()):
                            raise OSError('El POS dejó de responder como conectado.')
                    except Exception as exc:
                        self.log(f'Conexión Getnet perdida: {exc}')
                        self.disconnect()
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
                    self.disconnect()
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
            self.log(f'Pago {ticket} sin resultado confirmado; revisar antes de otra venta.')
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


def main():
    parser = argparse.ArgumentParser(description='Consultar o resolver el registro local de pagos de la app.')
    parser.add_argument('--journal', default=str(Path(__file__).with_name('pagos_getnet.sqlite3')))
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('history')
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
            for row in db.execute('SELECT * FROM payments ORDER BY created DESC LIMIT 50'):
                print(json.dumps(dict(row), ensure_ascii=False))


if __name__ == '__main__':
    main()
