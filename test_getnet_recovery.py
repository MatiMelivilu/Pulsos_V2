import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from getnet_payments import PaymentController
from getnet_serial import POS, UncertainResult
from getnet_serial.protocol import sign_message, unwrap
from test_getnet_payments import SerialPOS


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'payments.sqlite3'
        self.deliver = Mock()
        self.controller = self.make_controller()
        self.ticket = self.controller.journal.reserve('/dev/POS', 2, 30000, 4)
        self.controller.journal.update(self.ticket, 'uncertain')
        self.attach(self.controller)

    def make_controller(self):
        controller = PaymentController('/dev/POS', 115200, self.path, self.deliver,
                                       auto_recovery=True, recovery_interval=0.01)
        self.addCleanup(controller.stop)
        return controller

    def attach(self, controller):
        controller.connected = True
        controller.blocked = controller.journal.pending()
        controller.pos = Mock()
        controller.pos.cancel_sale.return_value = {'FunctionCode': 116, 'ResponseCode': 0}
        controller.pos.last_voucher.return_value = {
            'FunctionCode': 100, 'ResponseCode': 0, 'Ticket': 'PREVIOUS', 'Amount': 30000,
        }

    def sale(self, code=0, **extra):
        return {'FunctionCode': 100, 'ResponseCode': code, 'Ticket': self.ticket,
                'Amount': 30000, 'OperationId': 42, **extra}

    def test_pending_sale_cancelled_with_final_ticket_confirmation_releases_pos(self):
        controller = self.controller
        controller.pos.last_voucher.return_value = self.sale(code=1)
        controller.recover_payment()
        controller.pos.cancel_sale.assert_called_once_with(timeout=15)
        self.assertTrue(controller.ready())
        self.assertIsNone(controller.journal.pending())
        controller.pos.confirm_reconciled.assert_called_once()
        self.deliver.assert_not_called()

    def test_cancel_ack_and_another_voucher_do_not_prove_no_charge(self):
        controller = self.controller
        controller.recover_payment()
        self.assertTrue(controller.blocked)
        self.assertFalse(controller.ready())
        controller.pos.confirm_reconciled.assert_not_called()
        self.deliver.assert_not_called()

    def test_matching_approved_payment_is_voided_by_its_operation_id(self):
        controller = self.controller
        controller.pos.last_voucher.return_value = self.sale()
        controller.pos.request.return_value = {'FunctionCode': 102, 'ResponseCode': 0}
        controller.recover_payment()
        controller.pos.request.assert_called_once_with(102, OperationId=42, PrintOnPos=False, timeout=30)
        self.assertTrue(controller.ready())
        self.assertIsNone(controller.journal.pending())
        self.assertEqual(controller.journal.recovery_action(self.ticket, 'void')['state'], 'confirmed')
        self.deliver.assert_not_called()

    def test_approval_for_different_ticket_cannot_void_another_customer(self):
        self.controller.recover_payment()
        self.controller.pos.request.assert_not_called()
        self.assertTrue(self.controller.blocked)

    def test_matching_ticket_with_wrong_amount_cannot_void(self):
        self.controller.pos.last_voucher.return_value = self.sale(Amount=30001)
        self.controller.recover_payment()
        self.controller.pos.request.assert_not_called()
        self.assertTrue(self.controller.blocked)

    def test_void_declined_keeps_block_and_does_not_deliver(self):
        controller = self.controller
        controller.pos.last_voucher.return_value = self.sale()
        controller.pos.request.return_value = {'FunctionCode': 102, 'ResponseCode': 5}
        controller.recover_payment()
        self.assertTrue(controller.blocked)
        self.assertEqual(controller.journal.recovery_action(self.ticket, 'void')['state'], 'failed')
        self.deliver.assert_not_called()

    def test_void_timeout_is_not_repeated_after_restart(self):
        controller = self.controller
        controller.pos.last_voucher.return_value = self.sale()
        controller.pos.request.side_effect = UncertainResult('Timeout anulando')
        controller.recover_payment()
        self.assertEqual(controller.journal.recovery_action(self.ticket, 'void')['state'], 'uncertain')
        restarted = self.make_controller()
        self.attach(restarted)
        restarted.pos.last_voucher.return_value = self.sale()
        restarted.recover_payment()
        restarted.pos.request.assert_not_called()
        restarted.pos.cancel_sale.assert_not_called()
        self.assertTrue(restarted.blocked)
        restarted.pos.last_voucher.return_value = self.sale(FunctionCode=102)
        restarted.recover_payment()
        self.assertTrue(restarted.ready())
        self.deliver.assert_not_called()

    def test_ambiguous_cancel_is_not_repeated_after_restart(self):
        controller = self.controller
        controller.pos.cancel_sale.side_effect = UncertainResult('USB perdido')
        controller.recover_payment()
        restarted = self.make_controller()
        self.attach(restarted)
        restarted.pos.last_voucher.return_value = self.sale(code=1)
        restarted.recover_payment()
        restarted.pos.cancel_sale.assert_not_called()
        self.assertTrue(restarted.ready())

    def test_confirmed_void_is_reused_if_journal_finalization_was_interrupted(self):
        journal = self.controller.journal
        journal.begin_recovery_action(self.ticket, 'void', {'OperationId': 42})
        journal.finish_recovery_action(self.ticket, 'void', 'confirmed', {'FunctionCode': 102, 'ResponseCode': 0})
        self.controller.recover_payment()
        self.controller.pos.request.assert_not_called()
        self.controller.pos.cancel_sale.assert_not_called()
        self.assertTrue(self.controller.ready())

    def test_partial_product_delivery_is_not_automatically_cancelled(self):
        self.controller.journal.update(self.ticket, 'pulses_failed')
        self.controller.recover_payment()
        self.controller.pos.cancel_sale.assert_not_called()
        self.controller.pos.request.assert_not_called()
        self.assertTrue(self.controller.blocked)

    def test_cancel_receives_late_final_response_using_real_serial_client(self):
        ticket = self.ticket

        class CancellationWire(SerialPOS):
            def write(self, data):
                request = unwrap(json.loads(data))
                if request.get('Command') == 116:
                    self.writes.append(request)
                    self.buffer.extend(b'{"Received":true}')
                    self.buffer.extend(sign_message({'FunctionCode': 100, 'ResponseCode': 1,
                                                     'Amount': 0, 'Ticket': ticket}))
                    self.buffer.extend(sign_message({'FunctionCode': 116, 'ResponseCode': 0}))
                    return len(data)
                return super().write(data)

        wire = CancellationWire()
        self.controller.pos = POS('/dev/POS', transport=wire,
                                  on_message=self.controller.receive_message,
                                  on_raw=self.controller.receive_raw)
        self.controller.pos.needs_reconciliation = True
        self.controller.recover_payment()
        self.assertTrue(self.controller.ready())
        self.assertFalse(self.controller.pos.needs_reconciliation)
        self.assertEqual([request['Command'] for request in wire.writes], [116])
        self.deliver.assert_not_called()

    def test_worker_automatically_recovers_pending_attempt_after_start(self):
        ticket = self.ticket

        class PendingWire(SerialPOS):
            def write(self, data):
                request = unwrap(json.loads(data))
                if request.get('Command') == 116:
                    self.writes.append(request)
                    self.buffer.extend(sign_message({'FunctionCode': 100, 'ResponseCode': 1,
                                                     'Amount': 0, 'Ticket': ticket}))
                    self.buffer.extend(sign_message({'FunctionCode': 116, 'ResponseCode': 0}))
                    return len(data)
                return super().write(data)

        wire = PendingWire()
        self.controller.pos = None
        self.controller.connected = False
        self.controller.poll_interval = 0.01
        self.controller.pos_factory = lambda *args, **kwargs: POS('/dev/POS', transport=wire, **kwargs)
        self.controller.start()
        for _ in range(50):
            event = self.controller.events.get(timeout=2)
            if event['type'] == 'state' and event['connected'] and not event['busy'] and not event['blocked']:
                break
        else:
            self.fail('La recuperación automática no liberó el intento confirmado.')
        self.assertTrue(self.controller.ready())
        self.assertEqual(sum(request['Command'] == 116 for request in wire.writes), 1)
        self.deliver.assert_not_called()

    def test_busy_controller_does_not_start_recovery(self):
        self.controller.busy = True
        self.controller.recover_payment()
        self.controller.pos.cancel_sale.assert_not_called()
        self.controller.pos.last_voucher.assert_not_called()

    def previous_completed_voucher(self):
        voucher = {'FunctionCode': 100, 'ResponseCode': 0, 'Ticket': 'PREVIOUS', 'Amount': 30000,
                   'TerminalId': 'TERMINAL1', 'OperationId': 12, 'AuthorizationCode': 'AUTH12',
                   'AccountingDate': '2026-11-03 16:36:29'}
        with self.controller.journal.connect() as db:
            db.execute('''INSERT INTO payments
                       (ticket,port,channel,amount,pulses,created,state,response)
                       VALUES (?,?,?,?,?,?,?,?)''',
                       ('PREVIOUS', '/dev/POS', 1, 30000, 1, '2000-01-01', 'completed', json.dumps(voucher)))
        return voucher

    def no_active_sale(self):
        return {'FunctionCode': 116, 'ResponseCode': 999,
                'ResponseMessage': 'Rechazado. No hay venta en curso'}

    def test_no_active_sale_and_verified_previous_voucher_release_only_operational_lock(self):
        controller = self.controller
        pos = controller.pos
        pos.last_voucher.return_value = self.previous_completed_voucher()
        pos.cancel_sale.return_value = self.no_active_sale()
        controller.recover_payment()
        self.assertIsNone(controller.journal.pending())
        with controller.journal.connect() as db:
            state, note = db.execute('SELECT state,note FROM payments WHERE ticket=?', (self.ticket,)).fetchone()
        self.assertEqual(state, 'unresolved_released')
        self.assertIn('financiero todavía incierto', note)
        pos.confirm_reconciled.assert_not_called()
        pos.request.assert_not_called()
        pos.close.assert_called_once()
        self.assertFalse(controller.connected)  # Debe reconectar antes de otra selección.
        self.deliver.assert_not_called()
        restarted = self.make_controller()
        self.assertIsNone(restarted.blocked)

    def test_saved_no_active_sale_response_is_used_after_restart(self):
        controller = self.controller
        voucher = self.previous_completed_voucher()
        controller.journal.begin_recovery_action(self.ticket, 'cancel', {'Command': 116})
        controller.journal.finish_recovery_action(self.ticket, 'cancel', 'failed', self.no_active_sale())
        restarted = self.make_controller()
        self.attach(restarted)
        restarted.pos.last_voucher.return_value = voucher
        pos = restarted.pos
        restarted.recover_payment()
        pos.cancel_sale.assert_not_called()
        self.assertIsNone(restarted.journal.pending())

    def test_no_active_sale_with_unknown_voucher_keeps_block(self):
        self.controller.pos.cancel_sale.return_value = self.no_active_sale()
        self.controller.recover_payment()
        self.assertTrue(self.controller.blocked)

    def test_different_terminal_cannot_release_lock(self):
        voucher = self.previous_completed_voucher()
        voucher['TerminalId'] = 'ANOTHER_TERMINAL'
        self.controller.pos.cancel_sale.return_value = self.no_active_sale()
        self.controller.pos.last_voucher.return_value = voucher
        self.controller.recover_payment()
        self.assertTrue(self.controller.blocked)

    def test_generic_code_999_does_not_mean_no_active_sale(self):
        self.controller.pos.last_voucher.return_value = self.previous_completed_voucher()
        self.controller.pos.cancel_sale.return_value = {
            'FunctionCode': 116, 'ResponseCode': 999, 'ResponseMessage': 'Error de comunicación'}
        self.controller.recover_payment()
        self.assertTrue(self.controller.blocked)


if __name__ == '__main__':
    unittest.main()
