"""Selección de redes Wi-Fi con NetworkManager y teclado táctil."""

import os
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox


def parse_networks(output):
    """Interpreta campos terse de nmcli, incluyendo ':' y '\\' escapados."""
    networks = {}
    for line in output.splitlines():
        fields, field, escaped = [], [], False
        for char in line:
            if escaped:
                field.append(char)
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == ":":
                fields.append("".join(field))
                field = []
            else:
                field.append(char)
        fields.append("".join(field))
        if len(fields) != 4 or not fields[1]:
            continue
        active, ssid, signal, security = fields
        try:
            network = (ssid, int(signal), security, active == "*")
        except ValueError:
            continue
        previous = networks.get(ssid)
        if previous is None or (network[3], network[1]) > (previous[3], previous[1]):
            networks[ssid] = network
    return sorted(networks.values(), key=lambda item: (not item[3], -item[1], item[0]))


def run_nmcli(command, password=None):
    # La contraseña viaja por stdin, nunca por argumentos ni por logs.
    result = subprocess.run(
        ["nmcli", "--wait", "45", *command],
        input="" if password is None else password + "\n",
        text=True, capture_output=True, timeout=60,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode:
        if password is not None:
            raise RuntimeError(
                "No se pudo conectar. Revisa la contraseña, la señal y los "
                "permisos para configurar la red."
            )
        raise RuntimeError(result.stderr.strip() or "No se pudo consultar el Wi-Fi.")
    return result.stdout


class WifiWindow(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Conexión Wi-Fi")
        self.transient(master)
        self.attributes("-fullscreen", True)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda event: self.close())
        self.busy = False
        self.networks = []
        self.results = queue.Queue()
        self.status = tk.StringVar(value="Buscando redes…")
        self.password = tk.StringVar()
        self.show_password = tk.BooleanVar()
        self.shift = False
        self.symbols = False
        self.font = ("Helvetica", 16)

        header = tk.Frame(self)
        header.pack(fill="x", padx=10, pady=5)
        tk.Label(header, text="Conectar a Wi-Fi", font=("Helvetica", 22)).pack(side="left")
        self.close_button = tk.Button(header, text="Volver", font=self.font, command=self.close)
        self.close_button.pack(side="right", ipadx=15, ipady=8)
        self.scan_button = tk.Button(header, text="Buscar redes", font=self.font, command=self.scan)
        self.scan_button.pack(side="right", padx=10, ipady=8)

        listing = tk.Frame(self)
        listing.pack(fill="both", expand=True, padx=10)
        self.listbox = tk.Listbox(listing, font=("Helvetica", 20), height=4, exportselection=False)
        scrollbar = tk.Scrollbar(listing, width=35, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self.select_network)

        tk.Label(self, textvariable=self.status, font=("Helvetica", 13), wraplength=700).pack(pady=5)
        credentials = tk.Frame(self)
        credentials.pack(fill="x", padx=10)
        tk.Label(credentials, text="Contraseña:", font=self.font).pack(side="left")
        self.entry = tk.Entry(credentials, textvariable=self.password, show="*", font=self.font)
        self.entry.pack(side="left", fill="x", expand=True, padx=5)
        tk.Checkbutton(credentials, text="Mostrar", variable=self.show_password,
                       font=self.font, command=self.toggle_password).pack(side="left")
        self.connect_button = tk.Button(credentials, text="Conectar", font=self.font,
                                        command=self.connect)
        self.connect_button.pack(side="right", ipady=8)

        self.keyboard = tk.Frame(self)
        self.keyboard.pack(fill="x", padx=6, pady=5)
        self.draw_keyboard()
        self.grab_set()
        self.lift()
        self.after_id = self.after(100, self.poll_results)
        self.scan()

    def toggle_password(self):
        self.entry.configure(show="" if self.show_password.get() else "*")

    def draw_keyboard(self):
        for widget in self.keyboard.winfo_children():
            widget.destroy()
        rows = ["1234567890", "qwertyuiop", "asdfghjklñ", "zxcvbnm,.-"]
        if self.symbols:
            rows = ["!@#$%^&*()", "_+=[]{}<>?", "/\\|:;\"'`~", "¡¿€£°"]
        elif self.shift:
            rows = [row.upper() for row in rows]
        for row in rows:
            frame = tk.Frame(self.keyboard)
            frame.pack(fill="x")
            for char in row:
                tk.Button(frame, text=char, font=self.font, takefocus=False,
                          command=lambda c=char: self.type_character(c)).pack(
                              side="left", expand=True, fill="x", ipady=4)
        frame = tk.Frame(self.keyboard)
        frame.pack(fill="x")
        for text, action in [("Mayús", self.toggle_shift), ("Símbolos", self.toggle_symbols),
                             ("Espacio", lambda: self.type_character(" ")),
                             ("Borrar", self.backspace)]:
            tk.Button(frame, text=text, font=self.font, takefocus=False, command=action).pack(
                side="left", expand=True, fill="x", ipady=4)

    def type_character(self, char):
        self.entry.insert(tk.INSERT, char)
        self.entry.focus_set()

    def backspace(self):
        position = self.entry.index(tk.INSERT)
        if position:
            self.entry.delete(position - 1, position)

    def toggle_shift(self):
        self.shift = not self.shift
        self.draw_keyboard()

    def toggle_symbols(self):
        self.symbols = not self.symbols
        self.draw_keyboard()

    def set_busy(self, busy):
        self.busy = busy
        for widget in (self.scan_button, self.connect_button, self.close_button, self.listbox):
            widget.configure(state="disabled" if busy else "normal")

    def start_job(self, kind, operation):
        self.set_busy(True)

        def worker():
            try:
                self.results.put((kind, operation(), None))
            except subprocess.TimeoutExpired:
                self.results.put((kind, None, "La operación tardó demasiado. Vuelve a intentarlo."))
            except (OSError, RuntimeError) as exc:
                self.results.put((kind, None, str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def scan(self):
        if self.busy:
            return
        if not shutil.which("nmcli"):
            self.status.set("Esta función requiere NetworkManager (nmcli) instalado y activo.")
            return
        self.password.set("")
        self.status.set("Activando Wi-Fi y buscando redes cercanas…")

        def scan_networks():
            run_nmcli(["radio", "wifi", "on"])
            return parse_networks(run_nmcli([
                "--terse", "--escape", "yes", "--fields", "IN-USE,SSID,SIGNAL,SECURITY",
                "device", "wifi", "list", "--rescan", "yes",
            ]))

        self.start_job("scan", scan_networks)

    def select_network(self, event=None):
        if self.busy:
            return
        self.password.set("")
        self.status.set("Ingresa la contraseña; déjala vacía si la red es abierta o ya está guardada.")
        self.entry.focus_set()

    def connect(self):
        selection = self.listbox.curselection()
        if self.busy:
            return
        if not selection:
            self.status.set("Selecciona una red de la lista.")
            return
        ssid, signal, security, active = self.networks[selection[0]]
        if "802.1X" in security or "EAP" in security:
            self.status.set("Esta red empresarial requiere configuración avanzada en el editor de redes.")
            return
        password = self.password.get()
        self.password.set("")
        self.status.set(f"Conectando a {ssid}…")

        def connect_network():
            run_nmcli(["--ask", "device", "wifi", "connect", ssid], password=password)
            return ssid

        self.start_job("connect", connect_network)

    def poll_results(self):
        try:
            kind, result, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.set_busy(False)
            if error:
                self.status.set("No se pudo completar la operación.")
                messagebox.showerror("Wi-Fi", error, parent=self)
            elif kind == "scan":
                self.networks = result
                self.listbox.delete(0, "end")
                for ssid, signal, security, active in result:
                    prefix = "✓ " if active else ""
                    self.listbox.insert("end", f"{prefix}{ssid}   {signal}%   {security or 'Abierta'}")
                self.status.set("Selecciona una red." if result else
                                "No se encontraron redes visibles. Acerca el router y vuelve a buscar.")
            else:
                self.status.set(f"Conectado a {result}.")
                # Quitar la marca anterior hasta la siguiente búsqueda.
                self.networks = [(s, strength, sec, s == result)
                                 for s, strength, sec, active in self.networks]
                self.listbox.delete(0, "end")
                for ssid, signal, security, active in self.networks:
                    self.listbox.insert("end", f"{'✓ ' if active else ''}{ssid}   {signal}%   {security or 'Abierta'}")
        self.after_id = self.after(100, self.poll_results)

    def close(self):
        if self.busy:
            return
        self.after_cancel(self.after_id)
        self.password.set("")
        self.grab_release()
        self.destroy()
        self.master.lift()
