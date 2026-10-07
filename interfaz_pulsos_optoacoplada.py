import tkinter as tk
import json
from pathlib import Path
import queue
from getnet_payments import PaymentController
from tkinter import messagebox
from wifi_touch import WifiWindow
import os
import signal
import sys
import datetime
import time
from PIL import Image, ImageTk
from gpiozero import Device, LED, Button

# Configuración del POS Getnet en modo integrado USB
SERIAL_PORT = '/dev/ttyACM0'
BAUD_RATE = 115200
SALE_TIMEOUT = 120
PRINT_ON_POS = False
PAYMENT_JOURNAL = Path(__file__).with_name('pagos_getnet.sqlite3')

# Número de entradas (inicial)
n_inputs = 4 
class App(tk.Tk):
    def __init__(self, log_file):
        self.running = True
        self.ui_events = queue.Queue()
        self.controller = None
        self.log_file = log_file
        super().__init__()
        self.title("Pagos pulsos Getnet")
        self.protocol("WM_DELETE_WINDOW", self.close_app)
        self.pos_status = tk.StringVar(value="Conectando POS Getnet…")


        # Configuración para pantalla completa
        self.attributes('-fullscreen', True)
        self.bind('<F11>', self.toggle_fullscreen)
        self.bind('<Escape>', self.quit_fullscreen)

        # Crear un canvas y una scrollbar
        self.canvas = tk.Canvas(self)
        self.scroll_y = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview, width=40)
        
        self.frame = tk.Frame(self.canvas)

        # Sección de configuración de pagos de pulsos
        self.config_frame = tk.Frame(self.frame, bd=2, relief=tk.SUNKEN)
        self.config_frame.grid(row=0, column=0, padx=10, pady=10)
        
         # Fuente aumentada
        font_large = ("Helvetica", 20)
        font_large2 = ("Helvetica", 18)
        font_number = ("Helvetica", 20)
        font_medium = ("Helvetica", 14)
        button_width = 4  # Ancho del botón
        button_height = 2  # Ancho del botón
        
        button_width2 = 6  # Ancho del botón
        button_height2 = 3  # Ancho del bot
    
        self.imagen2 = Image.open("gear.png")
        self.resized_image2 = self.imagen2.resize((60,60))
        self.imagen_conv2 = ImageTk.PhotoImage(self.resized_image2) 
        
        tk.Label(self.config_frame, text="Configuración de pago de pulsos", font=font_large).grid(row=0, column=0, columnspan=6, pady=10)
        tk.Button(self.config_frame, image=self.imagen_conv2, command=self.open_wifi_settings, width=60, height=60).grid(row=0, column=5, columnspan=6, pady=10)
        tk.Label(self.config_frame, text="$Precio", font=font_large2).grid(row=1, column=0, columnspan=2, pady=10)
        tk.Label(self.config_frame, text="#Pulsos", font=font_large2).grid(row=1, column=3, columnspan=6, pady=10)

        self.price_vars = [tk.StringVar(value="0") for _ in range(n_inputs)]
        self.pulse_vars = [tk.IntVar(value=1) for _ in range(n_inputs)]
        
        self.create_input_widgets()
        self.pos_config()
        self.pulse_test()
        
        # Sección de log
        self.log_frame = tk.Frame(self.frame, bd=2, relief=tk.SUNKEN)
        self.log_frame.grid(row=1, column=0, padx=10, pady=10)
        
        tk.Label(self.log_frame, text="Log:", font=font_large2).pack(anchor="w", pady=5)
        
        self.log_text = tk.Text(self.log_frame, height=10, width=70, font=font_medium)
        self.log_text.pack(expand=True, fill=tk.BOTH)
        
        self.virtual_keyboard = None
        self.wifi_window = None

        # Configuración del canvas y el scrollbar
        self.canvas.create_window((0, 0), window=self.frame, anchor="nw")
        self.canvas.update_idletasks()
        
        self.canvas.configure(scrollregion=self.canvas.bbox("all"), yscrollcommand=self.scroll_y.set)
        
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll_y.pack(side="right", fill="y")
        
        self.load_values()
        
        self.GPIOconf()
        self.controller = PaymentController(
            SERIAL_PORT, BAUD_RATE, PAYMENT_JOURNAL, self.deliver_product,
            on_selected=lambda channel: self.toggle_gpio(self.outs[channel - 1]),
            events=self.ui_events, sale_timeout=SALE_TIMEOUT, print_on_pos=PRINT_ON_POS,
        )
        self.deshabilitar_botones()
        self.controller.start()
        self.after(50, self.process_events)

#Configurion de los puertos GPIO        
    def GPIOconf(self):
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mlog = f"[{current_time}] configurando GPIO\n"
        self.log(mlog)
        # Entradas y salidas comparten el controlador configurado al arrancar.
        factory = Device.pin_factory
        self.inCH1 = Button(2, bounce_time=0.05)
        self.inCH2 = Button(3, bounce_time=0.05)
        self.inCH3 = Button(4, bounce_time=0.05)
        self.inCH4 = Button(17, bounce_time=0.05)
        
        self.outCH1 = LED(19, pin_factory=factory)
        self.outCH1.on()
        self.outCH2 = LED(26, pin_factory=factory)
        self.outCH2.on()
        self.outCH3 = LED(20, pin_factory=factory)
        self.outCH3.on()
        self.outCH4 = LED(21, pin_factory=factory)
        self.outCH4.on()  
        
        self.outs = [self.outCH1, self.outCH2, self.outCH3, self.outCH4]  
        
        self.pulseChannel = LED(16, pin_factory=factory)
        self.pulseChannel.off()
                
    def deshabilitar_botones(self):
        self.inCH1.when_pressed = None
        self.inCH2.when_pressed = None
        self.inCH3.when_pressed = None
        self.inCH4.when_pressed = None


    def habilitar_botones(self):
        for channel in range(1, 5):
            getattr(self, f"inCH{channel}").when_pressed = lambda ch=channel: self.queue_input(ch)

    def queue_input(self, channel):
        # gpiozero llama desde otro hilo; Tk solo se modifica en process_events.
        if self.running and self.controller is not None and self.controller.ready():
            self.ui_events.put({'type': 'select', 'channel': channel})

    def toggle_gpio(self, led):
        led.on()
        time.sleep(0.1)
        led.off() 
        time.sleep(0.1)
    
    def toggle_gpio2(self, led):
        led.on()
        time.sleep(0.1)
        led.off() 
        time.sleep(0.1)    
    def select_product(self, channel):
        if not self.controller.ready():
            return
        try:
            lines = Path('valores.txt').read_text().splitlines()
            if len(lines) != n_inputs:
                raise ValueError(f"valores.txt debe tener {n_inputs} líneas.")
            amount, pulses = map(int, lines[channel - 1].split())
            if self.controller.submit_sale(channel, amount, pulses):
                self.deshabilitar_botones()
        except (OSError, ValueError, IndexError) as exc:
            self.log(f"No se inició la venta: {exc}")

    def select1(self):
        self.select_product(1)

    def select2(self):
        self.select_product(2)

    def select3(self):
        self.select_product(3)

    def select4(self):
        self.select_product(4)

    def toggle_fullscreen(self, event=None):
        self.attributes('-fullscreen', True)

    def quit_fullscreen(self, event=None):
        self.attributes('-fullscreen', False)

    def send_message1(self):
        pass
        
    def format_price(self, value):
        try:
            int_value = int(value)
            formatted_value = "{:,.0f}".format(value).replace(",", ".")
            return formatted_value
            
        except ValueError:
            return value 
        
    def create_input_widgets(self): 
         # Fuente aumentada
        font_large = ("Helvetica", 16)
        font_large2 = ("Helvetica", 16)
        font_number = ("Helvetica", 20)
        font_medium = ("Helvetica", 14)
        button_width = 4  # Ancho del botón
        button_height = 2  # Ancho del botón
        
        button_width2 = 6  # Ancho del botón
        button_height2 = 3  # Ancho del botón   
        
        self.imagen = Image.open("pencil2.png")
        self.resized_image = self.imagen.resize((60,60))
        self.imagen_conv = ImageTk.PhotoImage(self.resized_image)
        
        for i in range(n_inputs):
            formatted_price = self.format_price(self.price_vars[i].get())
            self.price_vars[i].set(formatted_price)
            
            tk.Label(self.config_frame, text=f"CH{i+1}:", font=("Helvetica", 18)).grid(row=i+2, column=0, padx=5, pady=5)
            tk.Entry(self.config_frame, textvariable=self.price_vars[i], state="readonly", font=("Helvetica", 20)).grid(row=i+2, column=1, padx=10, pady=10)
            tk.Button(self.config_frame, image=self.imagen_conv, command=lambda i=i: self.edit_price(i), font=("Helvetica", 14), width=60, height=60).grid(row=i+2, column=2, padx=5, pady=5)
            tk.Button(self.config_frame, text="-", command=lambda i=i: self.update_pulse(i, -1), font=("Helvetica", 16), width=5, height=3).grid(row=i+2, column=3, padx=5, pady=5)
            tk.Label(self.config_frame, textvariable=self.pulse_vars[i], font=("Helvetica", 18)).grid(row=i+2, column=4, padx=5, pady=5)
            tk.Button(self.config_frame, text="+", command=lambda i=i: self.update_pulse(i, 1), font=("Helvetica", 16), width=5, height=3).grid(row=i+2, column=5, padx=5, pady=5)
        tk.Button(self.config_frame, text="Guardar", command=self.save_values, font=font_large, width=button_width2, height=button_height2).grid(row=i+4, column=1, columnspan=3, padx=5, pady=10) 
        

    def pos_config(self):
        self.pos_frame = tk.Frame(self.frame, bd=2, relief=tk.SUNKEN)
        self.pos_frame.grid(row=2, column=0, padx=10, pady=10)
        tk.Label(self.pos_frame, text="Control POS Getnet", font=("Helvetica", 16)).grid(
            row=0, column=0, columnspan=3, pady=10)
        tk.Label(self.pos_frame, textvariable=self.pos_status, font=("Helvetica", 14),
                 wraplength=650).grid(row=1, column=0, columnspan=3, pady=10)
        self.pos_buttons = {}
        for column, (key, text, command) in enumerate([
            ('close', 'Cierre de caja', self.enviar_cierre),
            ('last', 'Último comprobante', self.ultimo_comprobante),
            ('poll', 'Poll', self.enviar_polling),
        ]):
            button = tk.Button(self.pos_frame, text=text, font=("Helvetica", 16),
                               width=18, height=2, command=command, state='disabled')
            button.grid(row=2, column=column, padx=5)
            self.pos_buttons[key] = button

    def pulse_test(self):
         # Fuente aumentada
         font_large = ("Helvetica", 16)
         font_large2 = ("Helvetica", 16)
         font_number = ("Helvetica", 20)
         font_medium = ("Helvetica", 14)
         # Sección de control POS
         self.test_frame = tk.Frame(self.frame, bd=2, relief=tk.SUNKEN)
         self.test_frame.grid(row=3, column=0, padx=10, pady=10)
        
         pos_width = 15  # Ancho del botón
         pos_height = 2  # Ancho del botón
        
         tk.Label(self.test_frame, text="Test", font=font_large).grid(row=0, column=0, columnspan=1, pady=10)
        
         send_button1 = tk.Button(self.test_frame, text="test venta", font=font_large, width=pos_width, height=pos_height, command=self.test_venta)
         send_button1.grid(row=1, column=0, padx=5)
         
         send_button2 = tk.Button(self.test_frame, text="test pulso", font=font_large, width=pos_width, height=pos_height, command=self.test_pulso_acoplado)
         send_button2.grid(row=1, column=1, padx=5)
              
         send_button3 = tk.Button(self.test_frame, text="CH1_ON", font=font_large, width=pos_width, height=pos_height, command=lambda: self.test_channel(1))
         send_button3.grid(row=2, column=0, padx=5)
         
         send_button4 = tk.Button(self.test_frame, text="CH2_ON", font=font_large, width=pos_width, height=pos_height, command=lambda: self.test_channel(2))
         send_button4.grid(row=2, column=1, padx=5)
         
         send_button5 = tk.Button(self.test_frame, text="CH3_ON", font=font_large, width=pos_width, height=pos_height, command=lambda: self.test_channel(3))
         send_button5.grid(row=3, column=0, padx=5)
         
         send_button6 = tk.Button(self.test_frame, text="CH4_ON", font=font_large, width=pos_width, height=pos_height, command=lambda: self.test_channel(4))
         send_button6.grid(row=3, column=1, padx=5)
        
    def test_venta(self):
        # Venta real de $500 y un pulso al aprobar; comparte bloqueo y registro.
        if not self.controller.submit_sale(1, 500, 1):
            self.log("POS no disponible: hay una operación en curso o pendiente de revisión.")

    def test_channel(self, channel):
        if not self.controller.submit_test(lambda: self.toggle_gpio(self.outs[channel - 1])):
            self.log("Prueba GPIO no disponible mientras el POS está ocupado o desconectado.")

    def test_pulso_acoplado(self):
        if not self.controller.submit_test(lambda: self.enviar_pulso_acoplado(1)):
            self.log("Prueba de pulsos no disponible mientras hay un pago pendiente.")

    def open_wifi_settings(self):
        """Muestra las redes cercanas y permite conectarse desde la pantalla táctil."""
        if self.wifi_window is not None and self.wifi_window.winfo_exists():
            self.wifi_window.lift()
            return
        self.wifi_window = WifiWindow(self)

    def reload_app(self):
         # Fuente aumentada
        font_large = ("Helvetica", 18)
        font_large2 = ("Helvetica", 16)
        font_number = ("Helvetica", 20)
        font_medium = ("Helvetica", 14)
        button_width = 4  # Ancho del botón
        button_height = 2  # Ancho del botón
        
        button_width2 = 6  # Ancho del botón
        button_height2 = 3  # Ancho del botón
        
        self.imagen2 = Image.open("gear.png")
        self.resized_image2 = self.imagen2.resize((60,60))
        self.imagen_conv2 = ImageTk.PhotoImage(self.resized_image2) 
        
        
        for widget in self.config_frame.winfo_children():
            widget.destroy()
        self.price_vars = [tk.StringVar(value="0") for _ in range(n_inputs)]
        self.pulse_vars = [tk.IntVar(value=1) for _ in range(n_inputs)]
        title_frame = tk.Frame(self.config_frame)
        title_frame.grid(row=0, column=0, columnspan=6, pady=10)

        tk.Label(title_frame, text="Configuración de pago de pulsos", font=("Helvetica", 20)).pack(side="left")
        tk.Button(self.config_frame, image=self.imagen_conv2, command=self.open_wifi_settings, width=60, height=60).grid(row=0, column=5, columnspan=6, pady=10)
        tk.Label(self.config_frame, text="$Precio", font=font_large).grid(row=1, column=0, columnspan=2, pady=10)
        tk.Label(self.config_frame, text="#Pulsos", font=font_large).grid(row=1, column=3, columnspan=6, pady=10)
        
        self.create_input_widgets()
        self.pos_config()
        self.pulse_test()

        self.load_values()
        
    def edit_price(self, index):
        if self.virtual_keyboard is not None:
            self.virtual_keyboard.destroy()
        
        self.virtual_keyboard = VirtualKeyboard(self, index, self.price_vars[index])

    def update_pulse(self, index, delta):
        new_value = self.pulse_vars[index].get() + delta
        if new_value >= 0:
            self.pulse_vars[index].set(new_value)
        else:
            messagebox.showwarning("Valor inválido", "El valor no puede ser negativo.")
    
    def load_values(self):
        try:
            with open("valores.txt", "r") as f:
                lines = f.readlines()
                for i, line in enumerate(lines):
                    price, pulses = line.strip().split()
                    formatted_price = self.format_price(price)
                    self.price_vars[i].set(formatted_price)
                    self.pulse_vars[i].set(int(pulses))
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            mlog = f"[{current_time}] Valores cargados correctamente.\n"
            self.log(mlog)
        except Exception as e:
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            mlog = f"[{current_time}] Error al cargar valores: {e}\n"
            self.log(mlog)

    def save_values(self):
        global n_inputs
        try:
            with open("valores.txt", "w") as f:
                for i in range(n_inputs):
                    price = self.price_vars[i].get().replace(".","")
                    pulse = self.pulse_vars[i].get()
                    f.write(f"{price} {pulse}\n")
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            mlog = f"[{current_time}] Valores guardados correctamente.\n"
            self.log(mlog)
        except Exception as e:
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            mlog = f"[{current_time}] Error al guardar valores: {e}\n"
            self.log(mlog)
    
    def log(self, message):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_file.write(message.rstrip() + "\n")
        self.log_file.flush()

    def process_events(self):
        if not self.running:
            return
        for _ in range(100):
            try:
                event = self.ui_events.get_nowait()
            except queue.Empty:
                break
            if event['type'] == 'select':
                self.select_product(event['channel'])
            elif event['type'] == 'log':
                now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.log(f"[{now}] {event['message']}")
            elif event['type'] == 'state':
                self.update_pos_state(event)
            elif event['type'] == 'command_result':
                response = json.dumps(event['response'], ensure_ascii=False, indent=2)
                self.log(f"Getnet {event['command']}: {response}")
                if event['command'] == 'last':
                    messagebox.showinfo("Último comprobante Getnet", response, parent=self)
        self.after(50, self.process_events)

    def update_pos_state(self, event):
        connected, busy, blocked = event['connected'], event['busy'], event['blocked']
        if busy:
            self.pos_status.set("POS ocupado: esperando resultado o entregando pulsos…")
        elif blocked:
            self.pos_status.set(f"Revisar pago {blocked['ticket']} (${blocked.get('amount', '?')}). "
                                "Consultar último comprobante; nuevas ventas bloqueadas.")
        elif connected:
            self.pos_status.set("POS Getnet conectado. Selecciona una entrada.")
        else:
            self.pos_status.set("POS desconectado. Reconectando automáticamente…")
        if connected and not busy and not blocked:
            self.habilitar_botones()
        else:
            self.deshabilitar_botones()
        for key, button in self.pos_buttons.items():
            allowed = connected and not busy and (not blocked or key in ('poll', 'last'))
            button.configure(state='normal' if allowed else 'disabled')

    def enviar_pulso_acoplado(self, n):
        for _ in range(n):
            if not self.running:
                raise RuntimeError("Cierre durante la entrega de pulsos.")
            self.toggle_gpio2(self.pulseChannel)

    def deliver_product(self, channel, pulses):
        self.toggle_gpio(self.outs[channel - 1])
        self.enviar_pulso_acoplado(pulses)

    def submit_pos_command(self, command):
        if not self.controller.submit_command(command):
            self.log("Comando no disponible: POS desconectado, ocupado o pago pendiente.")

    def enviar_cierre(self):
        self.submit_pos_command('close')

    def enviar_polling(self):
        self.submit_pos_command('poll')

    def ultimo_comprobante(self):
        self.submit_pos_command('last')

    def close_app(self):
        self.running = False
        self.deshabilitar_botones()
        if self.controller is not None:
            self.controller.stop()
        self.destroy()

class ConfigureInputsWindow(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
                
        self.title("Configurar Número de Entradas")
        self.geometry("400x300")
        self.master = master
        
        self.current_value = tk.IntVar(value=n_inputs)
        
        self.value_label = tk.Label(self, textvariable=self.current_value, font=("Helvetica", 24))
        self.value_label.pack(pady=10)
        
        self.button_frame = tk.Frame(self)
        self.button_frame.pack(pady=10)
        
        self.decrement_button = tk.Button(self.button_frame, text="-", font=("Helvetica", 16), command=self.decrement, width=6, height=2)
        self.decrement_button.pack(side=tk.LEFT, padx=10)
        
        self.increment_button = tk.Button(self.button_frame, text="+", font=("Helvetica", 16), command=self.increment, width=6, height=2)
        self.increment_button.pack(side=tk.RIGHT, padx=10)
        
        tk.Button(self, text="Guardar", command=self.save_and_reload, font=("Helvetica", 14), width=6, height=2).pack(pady=20)
        
    def fix_touch(self):
        self.iconify()
        self.update_idletasks()
        self.deiconify()
        
    def toggle_fullscreen(self, event=None):
        self.attributes("-fullscreen", not self.attributes("-fullscreen"))

    def quit_fullscreen(self, event=None):
        self.attributes("-fullscreen", False)
        
    def increment(self):
        global n_inputs
        if self.current_value.get() < n_inputs:
            self.current_value.set(self.current_value.get() + 1)

    def decrement(self):
        if self.current_value.get() > 1:
            self.current_value.set(self.current_value.get() - 1)

    def save_and_reload(self):
        global n_inputs
        new_value = self.current_value.get()
        if 1 <= new_value <= n_inputs:
            n_inputs = new_value
            self.master.reload_app()
            self.destroy()
        else:
            messagebox.showwarning("Valor inválido", "El número de entradas debe estar entre 4 y 8.")

class VirtualKeyboard(tk.Toplevel):
    def __init__(self, master, index, price_var):
        super().__init__(master)
        self.title(f"Editar precio {index+1}")
        self.geometry("480x420")
        self.price_var = price_var
        
        self.entry = tk.Entry(self, textvariable=price_var, font=("Arial", 24))
        self.entry.grid(row=0, column=0, columnspan=3, pady=10)
        
        buttons = [
            ('1', 1, 0), ('2', 1, 1), ('3', 1, 2),
            ('4', 2, 0), ('5', 2, 1), ('6', 2, 2),
            ('7', 3, 0), ('8', 3, 1), ('9', 3, 2),
            ('0', 4, 1), ('cerrar', 5, 0), ('borrar', 5, 1), ('guardar', 5, 2),
        ]
        
        for (text, row, col) in buttons:
            action = lambda x=text: self.click(x)
            tk.Button(self, text=text, width=10, height=2, command=action, font=("Helvetica", 16)).grid(row=row, column=col, padx=5, pady=5)
    
    def click(self, key):
        if key == 'cerrar':
            self.destroy()
        elif key == 'borrar':
            self.price_var.set(self.price_var.get()[:-1])
        elif key == 'guardar':
            self.destroy()
        else:
            current_text = self.price_var.get()
            self.price_var.set(current_text + key)
            
def handle_exit(signum, frame):
    sys.exit()

def configure_gpio_factory():
    """Usa gpiochip mediante lgpio, sin recurrir al backend sysfs antiguo."""
    try:
        from gpiozero.pins.lgpio import LGPIOFactory
    except ImportError as exc:
        raise RuntimeError(
            "Falta lgpio en el Python que ejecuta la aplicación. "
            "Instálalo con: python -m pip install --upgrade gpiozero lgpio"
        ) from exc
    Device.pin_factory = LGPIOFactory()

def dispositivo_conectado(ruta):
    return os.path.exists(ruta)
   
def toggle_gpio(led):
    led.off()
    time.sleep(0.5)
    led.on()

if __name__ == "__main__":
    
    # Crear carpeta log si no existe
    if not os.path.exists("log"):
        os.makedirs("log")

    # Generar nombre de archivo de log y dex
    log_filename = f"./log/log_{datetime.datetime.now().strftime('%Y%m%d')}.txt"

    # Asociar la señal SIGINT (Ctrl+C) al manejador de salida
    signal.signal(signal.SIGINT, handle_exit)

    try:
        configure_gpio_factory()
        with open(log_filename, "a") as log_file:
            print("iniciando")
            app = App(log_file)
            try:
                app.mainloop()
            finally:
                app.running = False
                app.deshabilitar_botones()
                app.controller.stop()
    finally:
        if Device.pin_factory is not None:
            Device.pin_factory.close()
