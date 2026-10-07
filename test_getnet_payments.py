import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from getnet_payments import PaymentController, PaymentJournal
from getnet_serial import POS, UncertainResult
from getnet_serial.protocol import sign_message, unwrap
from interfaz_pulsos_optoacoplada import App


class SerialPOS:
    """Simula bytes seriales; usa la firma y el parser reales de la librería."""

    def __init__(self, code=0, *, fail_sale=False, amount_delta=0, final_code=100):
        self.buffer = bytearray()
        self.writes = []
        self.code = code
        self.fail_sale = fail_sale
        self.amount_delta = amount_delta
        self.final_code = final_code
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.buffer)

    def write(self, data):
        if self.closed:
            raise OSError('USB desconectado')
        message = unwrap(json.loads(data))
        if 'Command' in message:
            self.writes.append(message)
            if message['Command'] == 106:
                self.buffer.extend(sign_message({'FunctionCode': 106, 'ResponseCode': 0, 'Connected': True}))
            elif message['Command'] == 100:
                if self.fail_sale:
                    raise OSError('USB desconectado durante el envío')
                self.buffer.extend(b'{"Received":true}')
                self.buffer.extend(sign_message({'FunctionCode': 112, 'MessageText': 'Acerca tarjeta'}))
                self.buffer.extend(sign_message({
                    'FunctionCode': self.final_code, 'ResponseCode': self.code,
                    'Amount': message['Amount'] + self.amount_delta,
                    'Ticket': message['TicketNumber'], 'ResponseMessage': 'Resultado',
                }))
            elif message['Command'] == 101:
                self.buffer.extend(sign_message({'FunctionCode': 100, 'ResponseCode': 0,
                                                'Amount': 100, 'Ticket': 'ANTERIOR'}))
            elif message['Command'] == 103:
                self.buffer.extend(sign_message({'FunctionCode': 103, 'ResponseCode': 0}))
        return len(data)

    def flush(self):
        pass

    def read(self, size):
        if self.closed:
            raise OSError('USB desconectado')
        # Fragmentar las respuestas como puede ocurrir con USB real.
        size = min(size, 13)
        chunk = bytes(self.buffer[:size])
        del self.buffer[:size]
        return chunk

    def close(self):
        self.closed = True


class PaymentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'pagos.sqlite3'
        self.delivered = []

    def controller(self, pos_factory=None, deliver=None):
        controller = PaymentController(
            '/dev/POS', 115200, self.path,
            deliver or (lambda channel, pulses: self.delivered.append((channel, pulses))),
            pos_factory=pos_factory or Mock(), retry_interval=0.01, poll_interval=0.01,
        )
        self.addCleanup(controller.stop)
        return controller

    def attached(self, **transport_options):
        controller = self.controller()
        transport = SerialPOS(**transport_options)
        controller.pos = POS('/dev/POS', transport=transport,
                             on_message=controller.receive_message)
        controller.connected = True
        return controller, transport

    def payment_state(self):
        with PaymentJournal(self.path).connect() as db:
            return db.execute('SELECT state FROM payments ORDER BY created DESC LIMIT 1').fetchone()[0]

    def wait_for(self, condition):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.005)
        self.fail('No se alcanzó el estado esperado')

    def test_approved_sale_delivers_selected_channel_and_exact_pulses(self):
        controller, wire = self.attached()
        controller.sell(3, 1200, 7)
        self.assertEqual(self.delivered, [(3, 7)])
        self.assertEqual(self.payment_state(), 'completed')
        self.assertIsNone(controller.blocked)
        sale = wire.writes[0]
        self.assertEqual(sale['Command'], 100)
        self.assertEqual(sale['Amount'], 1200)
        self.assertEqual(len(sale['TicketNumber']), 10)
        self.assertFalse(sale['PrintOnPos'])
        self.assertTrue(sale['SendMessage'])

    def test_progress_event_never_delivers_product(self):
        controller, wire = self.attached()
        controller.receive_message({'FunctionCode': 112, 'ResponseCode': 0, 'MessageText': 'Esperando'})
        self.assertEqual(self.delivered, [])

    def test_rejection_does_not_deliver_and_allows_next_sale(self):
        controller, wire = self.attached(code=5)
        controller.sell(1, 1000, 2)
        self.assertEqual(self.delivered, [])
        self.assertEqual(self.payment_state(), 'rejected')
        self.assertTrue(controller.ready())
        wire.code = 0
        controller.sell(2, 2000, 5)
        self.assertEqual(self.delivered, [(2, 5)])
        self.assertNotEqual(wire.writes[0]['TicketNumber'], wire.writes[1]['TicketNumber'])

    def test_unassigned_response_zero_is_not_approval(self):
        controller, wire = self.attached(final_code=0)
        with self.assertRaises(UncertainResult):
            controller.sell(1, 1000, 3)
        self.assertEqual(self.delivered, [])
        self.assertEqual(self.payment_state(), 'uncertain')

    def test_mismatched_amount_cannot_deliver(self):
        controller, wire = self.attached(amount_delta=1)
        with self.assertRaises(UncertainResult):
            controller.sell(1, 1000, 3)
        self.assertEqual(self.delivered, [])
        self.assertEqual(self.payment_state(), 'uncertain')

    def test_disconnect_during_sale_survives_restart_without_resend(self):
        controller, wire = self.attached(fail_sale=True)
        with self.assertRaises(UncertainResult):
            controller.sell(4, 4000, 9)
        restarted = self.controller()
        restarted.connected = True
        self.assertFalse(restarted.submit_sale(2, 2000, 2))
        self.assertEqual(self.payment_state(), 'uncertain')
        self.assertEqual(len(wire.writes), 1)
        self.assertEqual(self.delivered, [])
        self.assertTrue(restarted.submit_command('last'))

    def test_timeout_is_persisted_and_cannot_deliver(self):
        controller = self.controller()
        controller.pos = Mock()
        controller.pos.sale.side_effect = UncertainResult('Tiempo agotado')
        with self.assertRaises(UncertainResult):
            controller.sell(2, 2000, 4)
        self.assertEqual(self.payment_state(), 'uncertain')
        self.assertEqual(self.delivered, [])

    def test_approval_is_persisted_before_delivery_and_failure_blocks_restart(self):
        controller, wire = self.attached()

        def failed_delivery(channel, pulses):
            self.assertEqual(self.payment_state(), 'dispensing')
            raise OSError('GPIO desconectado después del primer pulso')

        controller.deliver = failed_delivery
        controller.sell(2, 2000, 4)
        self.assertEqual(self.payment_state(), 'pulses_failed')
        self.assertTrue(self.controller().blocked)
        with self.assertRaises(ValueError):
            controller.journal.resolve(controller.blocked['ticket'], 'not-paid', 'sin pago')

    def test_two_threads_cannot_queue_two_sales_or_another_pos_operation(self):
        entered, release = threading.Event(), threading.Event()
        wire = SerialPOS()
        pos = POS('/dev/POS', transport=wire)
        original_sale = pos.sale

        def wait_sale(*args, **kwargs):
            entered.set()
            release.wait(2)
            return original_sale(*args, **kwargs)

        pos.sale = wait_sale
        controller = self.controller(lambda *args, **kwargs: pos)
        controller.start()
        self.wait_for(controller.ready)
        self.assertTrue(controller.submit_sale(3, 3000, 6))
        self.assertTrue(entered.wait(1))
        self.assertFalse(controller.submit_sale(1, 1000, 1))
        self.assertFalse(controller.submit_command('close'))
        self.assertFalse(controller.submit_test(Mock()))
        release.set()
        self.wait_for(controller.ready)
        self.assertEqual(self.delivered, [(3, 6)])
        self.assertEqual(sum(x['Command'] == 100 for x in wire.writes), 1)

    def test_idle_disconnect_reconnects_without_restart(self):
        wires = []

        def connect(*args, **kwargs):
            wire = SerialPOS()
            wires.append(wire)
            return POS('/dev/POS', transport=wire)

        controller = self.controller(connect)
        controller.start()
        self.wait_for(controller.ready)
        wires[0].close()
        self.wait_for(lambda: len(wires) >= 2 and controller.ready())
        self.assertTrue(controller.submit_sale(1, 1000, 3))
        self.wait_for(lambda: self.delivered == [(1, 3)] and controller.ready())

    def test_disconnect_during_sale_reconnects_but_does_not_repeat_payment(self):
        wires = []

        def connect(*args, **kwargs):
            wire = SerialPOS(fail_sale=not wires)
            wires.append(wire)
            return POS('/dev/POS', transport=wire)

        controller = self.controller(connect)
        controller.start()
        self.wait_for(controller.ready)
        self.assertTrue(controller.submit_sale(4, 4000, 9))
        self.wait_for(lambda: len(wires) >= 2 and controller.connected and not controller.busy)
        self.assertTrue(controller.blocked)
        self.assertFalse(controller.submit_sale(4, 4000, 9))
        self.assertEqual(sum(item['Command'] == 100 for wire in wires for item in wire.writes), 1)
        self.assertEqual(self.delivered, [])
        self.assertTrue(controller.submit_command('last'))
        self.wait_for(lambda: not controller.busy)
        self.assertTrue(controller.blocked)
        self.assertEqual(self.delivered, [])

    def test_last_voucher_and_close_use_getnet_commands_without_pulses(self):
        wire = SerialPOS()
        controller = self.controller(lambda *args, **kwargs: POS('/dev/POS', transport=wire))
        controller.start()
        self.wait_for(controller.ready)
        self.assertTrue(controller.submit_command('last'))
        self.wait_for(controller.ready)
        self.assertTrue(controller.submit_command('close'))
        self.wait_for(controller.ready)
        self.assertIn(101, [request['Command'] for request in wire.writes])
        self.assertIn(103, [request['Command'] for request in wire.writes])
        self.assertEqual(self.delivered, [])

    def test_pending_record_exists_before_serial_sale(self):
        controller = self.controller()

        def sale(amount, ticket, **kwargs):
            record = controller.journal.pending()
            self.assertEqual(record['ticket'], ticket)
            self.assertEqual(record['state'], 'pending')
            self.assertEqual(record['pulses'], 4)
            return {'FunctionCode': 100, 'ResponseCode': 0, 'Amount': amount, 'Ticket': ticket}

        controller.pos = Mock(sale=sale)
        controller.sell(2, 1000, 4)
        self.assertEqual(self.delivered, [(2, 4)])

    def test_journal_cannot_reserve_another_payment_until_manual_resolution(self):
        journal = PaymentJournal(self.path)
        ticket = journal.reserve('/dev/POS', 1, 1000, 3)
        with self.assertRaises(RuntimeError):
            PaymentJournal(self.path).reserve('/dev/POS', 2, 2000, 5)
        journal.resolve(ticket, 'not-paid', 'Confirmado en el terminal: operación cancelada')
        self.assertIsNone(journal.pending())
        self.assertNotEqual(ticket, journal.reserve('/dev/POS', 1, 1000, 3))

    def test_poll_requires_connected_true_and_integer_response_code(self):
        self.assertTrue(PaymentController.poll_ok({'FunctionCode': 106, 'ResponseCode': 0, 'Connected': True}))
        self.assertFalse(PaymentController.poll_ok({'FunctionCode': 106, 'ResponseCode': False, 'Connected': True}))
        self.assertFalse(PaymentController.poll_ok({'FunctionCode': 106, 'ResponseCode': 0, 'Connected': False}))


class AppTests(unittest.TestCase):
    def test_values_are_captured_from_selected_line(self):
        app = Mock(controller=Mock())
        app.controller.ready.return_value = True
        with patch('interfaz_pulsos_optoacoplada.Path') as path:
            path.return_value.read_text.return_value = '100 1\n200 2\n300 8\n400 4\n'
            App.select_product(app, 3)
        app.controller.submit_sale.assert_called_once_with(3, 300, 8)
        app.deshabilitar_botones.assert_called_once()

    def test_gpio_callback_queues_without_touching_tk(self):
        app = Mock(running=True, ui_events=queue.Queue())
        app.controller.ready.return_value = True
        App.queue_input(app, 4)
        self.assertEqual(app.ui_events.get_nowait(), {'type': 'select', 'channel': 4})
        app.log.assert_not_called()
        app.controller.ready.return_value = False
        App.queue_input(app, 2)
        self.assertTrue(app.ui_events.empty())

    def test_invalid_prices_never_start_a_payment(self):
        app = Mock(controller=Mock())
        app.controller.ready.return_value = True
        with patch('interfaz_pulsos_optoacoplada.Path') as path:
            path.return_value.read_text.return_value = '100 1\ninvalid\n300 3\n400 4\n'
            App.select_product(app, 2)
        app.controller.submit_sale.assert_not_called()


if __name__ == '__main__':
    unittest.main()
