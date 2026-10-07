"""Manual POS diagnostics. Only the explicit 'sale' command initiates a payment."""

import argparse
from datetime import datetime, timezone
import json
import sqlite3
import sys

from . import POS, UncertainResult, new_ticket


def show(data):
    print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)


def events(message):
    if "ProtocolEvent" in message:
        print("Mensaje POS adicional (seguimos esperando el resultado):", flush=True)
        show(message)
    if "MessageCode" in message or message.get("FunctionCode") == 112:
        print("POS:", message.get("MessageText") or message.get("ResponseMessage") or message, flush=True)


def request_trace(payload):
    print("JSON de solicitud (antes de firmar):", flush=True)
    show(payload)


def journal(path):
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE IF NOT EXISTS attempts (
        ticket TEXT PRIMARY KEY, port TEXT NOT NULL, amount INTEGER NOT NULL,
        created TEXT NOT NULL, state TEXT NOT NULL, response TEXT, note TEXT
    )""")
    db.commit()
    return db


def reserve(db, ticket, port, amount):
    # Persist before sending. Across processes, only one unresolved attempt.
    db.execute("BEGIN IMMEDIATE")
    try:
        pending = db.execute("SELECT ticket FROM attempts WHERE state IN ('pending', 'uncertain')").fetchone()
        if pending:
            raise ValueError(f"Intento {pending[0]} sin conciliar. Usa history y last; no repitas el cobro.")
        if ticket is None:
            for _ in range(10):
                candidate = new_ticket()
                if not db.execute("SELECT 1 FROM attempts WHERE ticket=?", (candidate,)).fetchone():
                    ticket = candidate
                    break
            else:
                raise ValueError("No se pudo generar un ticket único; no se envió la venta")
        db.execute("INSERT INTO attempts(ticket,port,amount,created,state) VALUES (?,?,?,?,?)",
                   (ticket, port, amount, datetime.now(timezone.utc).isoformat(), "pending"))
        db.commit()
        return ticket
    except BaseException:
        db.rollback()
        raise


def update(db, ticket, state, response=None, note=None):
    db.execute("UPDATE attempts SET state=?,response=?,note=? WHERE ticket=?",
               (state, json.dumps(response, ensure_ascii=False) if response is not None else None, note, ticket))
    db.commit()


def parser():
    root = argparse.ArgumentParser(description="Pruebas manuales Getnet USB. sale inicia un cobro real.")
    root.add_argument("--journal", default=".getnet-tests.sqlite3", help="Registro persistente de intentos de prueba")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("ports", help="Listar puertos, sin enviar comandos al POS")
    sub.add_parser("history", help="Mostrar intentos guardados")
    for name in ("poll", "last", "sale"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--port", required=True, help="Ejemplo: /dev/ttyACM0 o /dev/serial/by-id/...")
        cmd.add_argument("--baudrate", type=int, default=115200)
        cmd.add_argument("--timeout", type=float, default=120 if name == "sale" else 15)
        cmd.add_argument("--verbose", action="store_true", help="Mostrar todos los mensajes POS procesados")
        if name == "sale":
            cmd.add_argument("--amount", type=int, required=True, help="Monto entero en pesos CLP")
            cmd.add_argument("--ticket", help="Opcional; se genera un ticket hexadecimal basado en UUID4 (10 caracteres)")
            cmd.add_argument("--print-on-pos", action="store_true")
    resolve = sub.add_parser("resolve", help="Registrar una conciliación manual ya realizada")
    resolve.add_argument("--ticket", required=True)
    resolve.add_argument("--outcome", required=True, choices=("approved", "not-paid"))
    resolve.add_argument("--note", required=True, help="Evidencia de la conciliación; no basta un timeout")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "ports":
        from serial.tools.list_ports import comports
        ports = list(comports())
        for port in ports:
            print(f"{port.device} | {port.description} | {port.hwid}")
        if not ports:
            print("No hay puertos seriales visibles. Comprueba cable de datos, modo integrado y acceso USB.")
            return 1
        return 0
    db = journal(args.journal)
    try:
        if args.command == "history":
            db.row_factory = sqlite3.Row
            show([dict(row) for row in db.execute("SELECT * FROM attempts ORDER BY created DESC")])
            return 0
        if args.command == "resolve":
            row = db.execute("SELECT state FROM attempts WHERE ticket=?", (args.ticket,)).fetchone()
            if not row or row[0] not in ("pending", "uncertain"):
                raise ValueError("El ticket no existe o ya tiene resultado final")
            if not args.note.strip():
                raise ValueError("Especifica evidencia de conciliación")
            update(db, args.ticket, "reconciled-" + args.outcome, note=args.note)
            print("Conciliación registrada. Este comando no envía nada al POS.")
            return 0
        if args.timeout <= 0:
            raise ValueError("El timeout debe ser positivo")
        if args.command == "sale":
            if args.amount <= 0 or args.amount > 9007199254740991:
                raise ValueError("El monto debe ser un entero positivo en pesos")
            if args.ticket is not None and (not args.ticket or len(args.ticket.encode("utf-16-le")) // 2 > 24):
                raise ValueError("El ticket debe tener de 1 a 24 unidades UTF-16")
        with POS(args.port, args.baudrate, on_message=events,
                 on_wire=show if args.verbose else None,
                 on_request=request_trace if args.verbose else None) as pos:
            if args.command == "poll":
                result = pos.poll(timeout=args.timeout)
                show(result)
                return 0 if result.get("ResponseCode") == 0 and result.get("Connected") is True else 1
            if args.command == "last":
                show(pos.last_voucher(timeout=args.timeout))
                print("Este comprobante debe cotejarse con el intento. No desbloquea ventas automáticamente.")
                return 0
            # Verify connection before reserving and initiating a payment.
            connection = pos.poll()
            if connection.get("ResponseCode") != 0 or connection.get("Connected") is not True:
                show(connection)
                raise ValueError("El POS no confirmó conexión; no se envió la venta")
            args.ticket = reserve(db, args.ticket, args.port, args.amount)
            print(f"Iniciando cobro REAL de ${args.amount} CLP, ticket {args.ticket}. Opera la tarjeta en el POS.", flush=True)
            try:
                result = pos.sale(args.amount, args.ticket, print_on_pos=args.print_on_pos, timeout=args.timeout)
            except BaseException as exc:
                response = getattr(exc, "response", None)
                update(db, args.ticket, "uncertain", response=response,
                       note=str(exc) or type(exc).__name__)
                if response is not None:
                    print("Respuesta recibida para conciliación (no se marca como aprobada):", flush=True)
                    show(response)
                raise
            approved = result.get("ResponseCode") == 0
            update(db, args.ticket, "approved" if approved else "rejected", response=result)
            print("PAGO APROBADO" if approved else "PAGO RECHAZADO / CANCELADO")
            show(result)
            return 0 if approved else 1
    except KeyboardInterrupt:
        print("Prueba interrumpida. Si había una venta enviada, concilia el intento antes de repetir.", file=sys.stderr)
        return 130
    except (UncertainResult, ValueError, OSError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
