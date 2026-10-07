"""Synchronous USB SDK. No HTTP, Windows agent, DLL, or automatic retries."""

from collections import deque
from datetime import datetime, timezone
import threading
import time

from .protocol import JSONStream, ProtocolError, sign_message, unwrap
from .tickets import new_ticket


class BusyError(RuntimeError):
    pass


class UncertainResult(RuntimeError):
    """A request may have reached the POS; reconcile before another payment."""

    def __init__(self, message, *, response=None):
        super().__init__(message)
        self.response = response


class POS:
    def __init__(self, port, baudrate=115200, *, transport=None, on_message=None, on_wire=None, on_request=None):
        if transport is None:
            import serial

            transport = serial.Serial(
                port, baudrate=baudrate, bytesize=8, parity="N", stopbits=1,
                timeout=0.1, write_timeout=3, exclusive=True,
            )
        self.transport = transport
        self.on_message = on_message or (lambda message: None)
        self.on_wire = on_wire or (lambda message: None)
        self.on_request = on_request or (lambda message: None)
        self._stream = JSONStream()
        self._messages = deque()
        self._lock = threading.Lock()
        self.needs_reconciliation = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.transport.close()

    def confirm_reconciled(self):
        """Call ONLY after the application has resolved and persisted the outcome."""
        if not self._lock.acquire(blocking=False):
            raise BusyError("POS has an active request")
        try:
            self.needs_reconciliation = False
        finally:
            self._lock.release()

    def _write(self, data):
        if self.transport.write(data) != len(data):
            raise OSError("Incomplete serial write")
        self.transport.flush()

    def request(self, command, *, timeout=60, received_timeout=3, **fields):
        if type(command) is not int or command not in range(100, 117):
            raise ValueError("Unsupported command")
        if timeout <= 0 or received_timeout <= 0:
            raise ValueError("Timeouts must be positive")
        if "Command" in fields:
            raise ValueError("Command must be passed separately")
        if not self._lock.acquire(blocking=False):
            raise BusyError("Only one request per POS is allowed")
        sent = False
        try:
            if self.needs_reconciliation and command not in (101, 104, 105, 106):
                raise UncertainResult("Reconcile the previous request before sending another operation")
            payload = {"Command": command, **fields}
            payload.setdefault("DateTime", datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"))
            encoded = sign_message(payload)
            self.on_request(payload)
            start = time.monotonic()
            sent = True  # Even a partial write can reach the terminal.
            self._write(encoded)
            received = False
            # Manual 1.11, pp.47–49: LAST VOUCHER responds with the type of
            # the recorded transaction, not necessarily the request code 101.
            final_codes = {100, 101, 102, 108} if command == 101 else {command}
            while True:
                if self._messages:
                    outer = self._messages.popleft()
                    self.on_wire(outer)
                    try:
                        message = unwrap(outer)
                    except ProtocolError:
                        self._write(b'{"Received":false}')
                        raise
                    if "Received" in message:
                        if message["Received"] is not True:
                            raise ProtocolError("POS did not accept the request")
                        received = True
                        continue
                    self._write(b'{"Received":true}')
                    # Valid traffic from the POS proves reception even if firmware
                    # omits a separate Received object. Keep the final-result timer.
                    received = True
                    # Callbacks must be quick and must not send another command.
                    self.on_message(message)
                    code = message.get("FunctionCode")
                    if code == 0 and "ResponseCode" in message:
                        # Observed firmware error: code=0, response=0, text=
                        # "Error durante el procesamiento". This is not approval
                        # and will not be followed as if it were a progress event.
                        raise ProtocolError(
                            "POS returned an unassigned operation response: "
                            + str(message.get("ResponseMessage", message)),
                            response=message,
                        )
                    if code in final_codes and "ResponseCode" in message:
                        if command == 100:
                            approved = message["ResponseCode"] == 0
                            amount = message.get("Amount")
                            ticket = message.get("Ticket")
                            # Failed operations may return empty/default transaction
                            # fields. These are not evidence of another payment.
                            amount_mismatch = (
                                amount != fields["Amount"] if approved
                                else amount not in (None, 0) and amount != fields["Amount"]
                            )
                            ticket_mismatch = (
                                ticket not in (None, "") and ticket != fields["TicketNumber"]
                            )
                            if amount_mismatch or ticket_mismatch:
                                raise ProtocolError(
                                    "Sale response does not match the active payment: "
                                    f"expected Amount={fields['Amount']!r}, Ticket={fields['TicketNumber']!r}; "
                                    f"received Amount={message.get('Amount')!r}, Ticket={message.get('Ticket')!r}, "
                                    f"ResponseCode={message.get('ResponseCode')!r}",
                                    response=message,
                                )
                        return message
                    # Progress and late responses are events, not final results.
                    continue
                elapsed = time.monotonic() - start
                if elapsed >= timeout:
                    raise TimeoutError("Timed out waiting for final POS response")
                if not received and elapsed >= received_timeout:
                    raise TimeoutError("Timed out waiting for POS receipt confirmation")
                chunk = self.transport.read(max(1, min(getattr(self.transport, "in_waiting", 0), 65536)))
                if chunk:
                    self._messages.extend(self._stream.feed(chunk))
        except Exception as exc:
            if sent:
                self.needs_reconciliation = True
                raise UncertainResult(
                    "POS outcome is uncertain; do not resend automatically. " + str(exc),
                    response=getattr(exc, "response", None),
                ) from exc
            raise
        finally:
            self._lock.release()

    def poll(self, timeout=10):
        return self.request(106, timeout=timeout)

    def sale(self, amount, ticket=None, *, print_on_pos=False, sale_type=0,
             send_message=True, employee_id=1, timeout=120):
        if type(amount) is not int or not 0 < amount <= 9007199254740991:
            raise ValueError("Amount must be a positive integer in pesos, within the JS safe integer range")
        if ticket is None:
            ticket = new_ticket()
        if not isinstance(ticket, str) or not ticket or len(ticket.encode("utf-16-le")) // 2 > 24:
            raise ValueError("Ticket must contain 1 to 24 UTF-16 units")
        if type(sale_type) is not int or sale_type not in range(7):
            raise ValueError("Invalid sale type")
        if type(employee_id) is not int or employee_id < 0:
            raise ValueError("Employee ID must be a nonnegative integer")
        if type(print_on_pos) is not bool or type(send_message) is not bool:
            raise ValueError("Print and message options must be booleans")
        return self.request(100, timeout=timeout, Amount=amount, TicketNumber=ticket,
                            PrintOnPos=print_on_pos, SaleType=sale_type,
                            SendMessage=send_message, EmployeeId=employee_id)

    def last_voucher(self, print_on_pos=False, timeout=60):
        return self.request(101, timeout=timeout, PrintOnPos=print_on_pos)

    def cancel_sale(self, timeout=10):
        """Standalone command; cannot run concurrently with this synchronous client's sale."""
        return self.request(116, timeout=timeout)
