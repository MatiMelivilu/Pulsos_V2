import tkinter as tk
from tkinter import messagebox
import serial
import threading
import socket
import subprocess
import os
import signal
import sys
import datetime
import time
import select
from PIL import Image, ImageTk
import re
from gpiozero.pins.native import NativeFactory
from gpiozero.pins.rpigpio import RPiGPIOFactory
from gpiozero import Device, LED, Button

# Numero serial unico de raspberry
SERIAL_NUMBER = "100000007ccad951"

#Configuracion de POS
SERIAL_PORT = '/dev/POS1'
BAUD_RATE = 115200 

#ruta conversor pulsos
ruta_pulsos   = '/dev/ttyUSB0'
ruta_freePass = '/dev/mi_dispositivo_2'
# Número de entradas (inicial)
n_inputs = 4 
entry = 1
POS_CONNECT = 0
POS_OPERATION = False
class App(tk.Tk):
    def __init__(self, log_file):
        self.running = True
        self.client_socket = None
        self.log_file = log_file
        super().__init__()
        self.title("Pagos pulsos acoplados")


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
        tk.Button(self.config_frame, image=self.imagen_conv2, command=self.configure_inputs, width=60, height=60).grid(row=0, column=5, columnspan=6, pady=10)
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

        # Configuración del canvas y el scrollbar
        self.canvas.create_window((0, 0), window=self.frame, anchor="nw")
        self.canvas.update_idletasks()
        
        self.canvas.configure(scrollregion=self.canvas.bbox("all"), yscrollcommand=self.scroll_y.set)
        
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll_y.pack(side="right", fill="y")
        
        self.load_values()
        
        self.GPIOconf()
        self.habilitar_botones() 
        
        self.POS_thread = threading.Thread(target=self.POS_listener, daemon=True)
        self.POS_thread.start()
        
        #self.socket_thread = threading.Thread(target=self.freePassRead, daemon=True)
        #self.socket_thread.start()

#Configurion de los puertos GPIO        
    def GPIOconf(self):
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mlog = f"[{current_time}] configurando GPIO\n"
        self.log(mlog)
        self.log_file.write(mlog)
        self.log_file.flush()
        factory = NativeFactory()
        self.inCH1 = Button(2, bounce_time=0.2)
        self.inCH2 = Button(3, bounce_time=0.2)
        self.inCH3 = Button(4, bounce_time=0.2)
        self.inCH4 = Button(17, bounce_time=0.2)
        
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
        self.inCH1.when_pressed = lambda: self.select1()
        self.inCH2.when_pressed = lambda: self.select2()
        self.inCH3.when_pressed = lambda: self.select3()
        self.inCH4.when_pressed = lambda: self.select4()   
        
    def toggle_gpio(self, led):
        led.off()
        time.sleep(0.5)
        led.on() 
        time.sleep(0.5)
    
    def toggle_gpio2(self, led):
        led.on()
        time.sleep(0.5)
        led.off() 
        time.sleep(0.5)    
    def select1(self):
        global entry
        entry = 1
        self.deshabilitar_botones()
        self.toggle_gpio(self.outCH1)
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mlog = f"[{current_time}] Producto 1 selecionado\n"
        self.log(mlog)
        self.log_file.write(mlog)
        self.log_file.flush()
        time.sleep(1)
        self.habilitar_botones()
        self.venta_POS()

    def select2(self):
        global entry
        entry = 2
        self.deshabilitar_botones()
        self.toggle_gpio(self.outCH2)
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mlog = f"[{current_time}] Producto 2 selecionado\n"
        self.log(mlog)
        self.log_file.write(mlog)
        self.log_file.flush()
        time.sleep(1)
        self.habilitar_botones()
        self.venta_POS()

    def select3(self):
        global entry
        entry = 3
        self.deshabilitar_botones()
        self.toggle_gpio(self.outCH3)
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mlog = f"[{current_time}] Producto 3 selecionado\n"
        self.log(mlog)
        self.log_file.write(mlog)
        self.log_file.flush()
        time.sleep(1)
        self.habilitar_botones()
        self.venta_POS()

    def select4(self):
        global entry
        entry = 4
        self.deshabilitar_botones()
        self.toggle_gpio(self.outCH4)
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mlog = f"[{current_time}] Producto 4 selecionado\n"
        self.log(mlog)
        self.log_file.write(mlog)
        self.log_file.flush()
        time.sleep(1)
        self.habilitar_botones()
        self.venta_POS()

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
         # Fuente aumentada
         font_large = ("Helvetica", 16)
         font_large2 = ("Helvetica", 16)
         font_number = ("Helvetica", 20)
         font_medium = ("Helvetica", 14)
         # Sección de control POS
         self.pos_frame = tk.Frame(self.frame, bd=2, relief=tk.SUNKEN)
         self.pos_frame.grid(row=2, column=0, padx=10, pady=10)
        
         pos_width = 15  # Ancho del botón
         pos_height = 2  # Ancho del botón
        
         tk.Label(self.pos_frame, text="Control POS", font=font_large).grid(row=0, column=0, columnspan=1, pady=10)
        
         send_button2 = tk.Button(self.pos_frame, text="Cierre de caja", font=font_large, width=pos_width, height=pos_height, command=self.enviar_cierre)
         send_button2.grid(row=1, column=0, padx=5)
        
         send_button7 = tk.Button(self.pos_frame, text="Carga llaves", font=font_large, width=pos_width, height=pos_height, command=self.enviar_cargaLlaves)
         send_button7.grid(row=1, column=1, padx=5)
         
         send_button8 = tk.Button(self.pos_frame, text="Poll", font=font_large, width=pos_width, height=pos_height, command=self.enviar_polling)
         send_button8.grid(row=1, column=2, padx=5)
        
        
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
              
         send_button3 = tk.Button(self.test_frame, text="CH1_ON", font=font_large, width=pos_width, height=pos_height, command=lambda: self.toggle_gpio(self.outCH1))
         send_button3.grid(row=2, column=0, padx=5)
         
         send_button4 = tk.Button(self.test_frame, text="CH2_ON", font=font_large, width=pos_width, height=pos_height, command=lambda: self.toggle_gpio(self.outCH2))
         send_button4.grid(row=2, column=1, padx=5)
         
         send_button5 = tk.Button(self.test_frame, text="CH3_ON", font=font_large, width=pos_width, height=pos_height, command=lambda: self.toggle_gpio(self.outCH3))
         send_button5.grid(row=3, column=0, padx=5)
         
         send_button6 = tk.Button(self.test_frame, text="CH4_ON", font=font_large, width=pos_width, height=pos_height, command=lambda: self.toggle_gpio(self.outCH4))
         send_button6.grid(row=3, column=1, padx=5)
        
    def test_venta(self):
        global POS_OPERATION
        precio = 500
        n = 1
        mensaje_venta = self.generar_mensaje_venta(precio, "1234", 0,0)
        self.pos_serial.write(mensaje_venta)
            
    def test_pulso(self):
        global ruta_pulsos
        n = 1
        pos = 'Aprobado'
        if pos == 'Aprobado':
            ser = serial.Serial(ruta_pulsos, 9600)
            message = '30313233000A'
            for i in range(n):
                ser.write(bytes.fromhex(message))
                current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                mlog = f"[{current_time}] Enviando {message}.\n"
                self.log(mlog)
                self.log_file.write(mlog)
                self.log_file.flush()
                time.sleep(1)
            ser.close()
            time.sleep(1)

    def test_pulso_acoplado(self):
        global ruta_pulsos
        n = 1
        pos = 'Aprobado'
        if pos == 'Aprobado':
            for i in range(n):
                self.toggle_gpio2(self.pulseChannel)
                current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                mlog = f"[{current_time}] Enviando test pulso.\n"
                self.log(mlog)
                self.log_file.write(mlog)
                self.log_file.flush()
                time.sleep(0.1)
      
        
    def configure_inputs(self):
        ConfigureInputsWindow(self)
        
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
        tk.Button(self.config_frame, image=self.imagen_conv2, command=self.configure_inputs, width=60, height=60).grid(row=0, column=5, columnspan=6, pady=10)
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
            self.log_file.write(mlog)
            self.log_file.flush()
        except Exception as e:
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            mlog = f"[{current_time}] Error al cargar valores: {e}\n"
            self.log(mlog)
            self.log_file.write(mlog)
            self.log_file.flush()

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
            self.log_file.write(mlog)
            self.log_file.flush()
        except Exception as e:
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            mlog = f"[{current_time}] Error al guardar valores: {e}\n"
            self.log(mlog)
            self.log_file.write(mlog)
            self.log_file.flush()
    
    def log(self, message):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)

    def enviar_pulsos(self, n):
        global ruta_pulsos
        message = '30313233000A'
        try:
            for i in range(n):
                self.serPulsos.write(bytes.fromhex(message))
                print('mensaje enviado: ', message)
                current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                mlog = f"[{current_time}] Enviando: {message}\n"
                self.log(mlog)
                self.log_file.write(mlog)
                self.log_file.flush()
                time.sleep(1)
            self.serPulsos.close()

        except (serial.SerialException, FileNotFoundError) as e:
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            mlog = f"[{current_time}] ? No se pudieron enviar los pulsos: {e}\n"
            self.log(mlog)
            self.log_file.write(mlog)
            self.log_file.flush()
            self.serPulsos.close()
         
    def enviar_pulso_acoplado(self, n):
        for i in range(n):
            self.toggle_gpio(self.pulseChannel)
            print('mensaje enviado: pulso acoplado')
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            mlog = f"[{current_time}] Enviando: pulso acopladp\n"
            self.log(mlog)
            self.log_file.write(mlog)
            self.log_file.flush()
            time.sleep(1)
       
    def venta_POS(self):
        with open('valores.txt', 'r') as file:
            for i, line in enumerate(file):
                print(entry)
                if i == (entry -1):
                    print(i)
                    self.precio, self.n_pulsos = line.strip().split()
                    print(self.precio, self.n_pulsos)
                    break
        self.deshabilitar_botones()
                                   
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mlog = f"[{current_time}] Iniciando venta por {self.precio}\n"
        self.log(mlog)
        self.log_file.write(mlog)
        self.log_file.flush() 
        mensaje_venta = self.generar_mensaje_venta(self.precio, "1234", 0,1)
        self.pos_serial.write(mensaje_venta)

    def conectar_serial(self):
        """Intenta conectar al puerto serial cuando esta disponible."""
        while True:
            if os.path.exists(SERIAL_PORT):
                try:
                    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    mlog = f"[{current_time}] Intentando conectar a {SERIAL_PORT}...\n"
                    self.log(mlog)
                    self.log_file.write(mlog)
                    self.log_file.flush() 
                    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
                    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    mlog = f"[{current_time}] Conectado exitosamente.\n"
                    self.log(mlog)
                    self.log_file.write(mlog)
                    self.log_file.flush() 
                    return ser
                except serial.SerialException as e:
                    print(f"Error al conectar: {e}")
            else:
                print(f"{SERIAL_PORT} no encontrado. Esperando conexion...")
                current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                mlog = f"[{current_time}] {SERIAL_PORT} no encontrado. Esperando conexion...\n"
                self.log(mlog)
                self.log_file.write(mlog)
                self.log_file.flush() 

            time.sleep(1)
            
    def esperar_ack(self, timeout=3):
        """Espera un ACK (0x06) desde el POS con un tiempo maximo."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.pos_serial.in_waiting > 0:
                raw = self.pos_serial.readline()
                if raw.strip() == b'\x06':
                    print("ACK recibido tras polling")
                    return True
        print("No se recibio ACK dentro del tiempo")
        return False

    def POS_listener(self):
        """Escucha el puerto serial indefinidamente."""
        while True:
            self.pos_serial = self.conectar_serial()
            try:            
                while True:
                    if self.pos_serial.in_waiting > 0:
                        raw_line = self.pos_serial.readline()
                        print(raw_line)
                        if raw_line.strip() == b'\x06':
                            linea = "ACK recibido"
                            print("ACK recibido")
                        else:
                            linea = raw_line.decode('utf-8', errors='ignore').strip()
                        #Mensaje de POS                        
                        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        mlog = f"[{current_time}] POS: {linea}\n"
                        self.log(mlog)
                        self.log_file.write(mlog)
                        self.log_file.flush() 
                        if linea == b"\x06":
                            print("true")
                        
                        #Interpretando mensaje de POS
                        codigos = re.findall(r'\d{4}', linea)
                        if "0911" in codigos:
                            self.enviar_polling()
                            if self.esperar_ack():
                                
                                with open('valores.txt', 'r') as file:
                                    for i, line in enumerate(file):
                                        print(entry)
                                        if i == (entry -1):
                                            print(i)
                                            self.precio, self.n_pulsos = line.strip().split()
                                            print(self.precio, self.n_pulsos)
                                            break
                                self.deshabilitar_botones()
                                   
                                current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                mlog = f"[{current_time}] Iniciando venta por {self.precio}\n"
                                self.log(mlog)
                                self.log_file.write(mlog)
                                self.log_file.flush() 
                                mensaje_venta = self.generar_mensaje_venta(self.precio, "1234", 0,1)
                                self.pos_serial.write(mensaje_venta)
                            else:
                                self.log("[ERROR] No se recibio ACK tras el polling.")
                          
                        elif "0210" in codigos:
                            self.log(f"[{current_time}] Respuesta de venta recibida.")
                            self.log_file.write(f"[{current_time}] Respuesta de venta recibida.\n")
                            self.log_file.flush()
                            self.interpretar_respuesta_0210(linea, self.n_pulsos)
                            
                        elif "0510" in codigos:
                            self.log(f"[{current_time}] Respuesta de cierre recibida.")
                            self.log_file.write(f"[{current_time}] POS:{linea}.\n")
                            self.log_file.flush()
                            self.enviar_ack()
                            
                        elif "0810" in codigos:
                            self.log(f"[{current_time}] Respuesta de carga de llaves.")
                            self.log_file.write(f"[{current_time}] POS:{linea}.\n")
                            self.log_file.flush()
                            self.enviar_ack()
                            
                                               
            except (serial.SerialException, OSError) as e:
                print(f"Desconectado. Error: {e}")
                self.pos_serial.close()
                print("Reintentando conexion...")

    def interpretar_respuesta_0210(self, linea, n):
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        partes = linea.split('|')
        if len(partes) < 2:
            self.log("[ERROR] Respuesta 0210 mal formada.")
            self.log_file.write("[ERROR] Respuesta 0210 mal formada.\n")
            self.log_file.flush()
            return

        codigo_respuesta = partes[1]
        if codigo_respuesta == "00":
            self.log(f"[{current_time}] Venta Aprobada.")
            self.log_file.write(f"[{current_time}] Venta Aprobada.\n")
            self.log_file.flush()
            self.enviar_ack()
            self.toggle_gpio(self.outs[entry - 1])
            self.enviar_pulsos_acoplado(int(n))
        elif codigo_respuesta == "01":
            self.log(f"[{current_time}]Venta Rechazada.")
            self.log_file.write(f"[{current_time}]Venta Rechazada.\n")
            self.log_file.flush()
            self.enviar_ack()
            self.serPulsos.close()

        else:
            self.log(f"[{current_time}] Error en la venta. Codigo: {codigo_respuesta}")
            self.log_file.write(f"[{current_time}] Error en la venta. Codigo: {codigo_respuesta}\n")
            self.log_file.flush()
            self.enviar_ack()
            self.serPulsos.close()
        self.habilitar_botones()

    def generar_mensaje_venta(self, monto, numero_ticket, envia_voucher, envia_mensajes):
        """
        Genera el mensaje de venta segn el formato del protocolo.
        """
        def calcular_lrc(data):
            """Calcula el LRC (Longitudinal Redundancy Check)."""
            lrc = 0
            for byte in data:
                lrc ^= byte
            return lrc

        stx = 0x02
        etx = 0x03
        separador = 0x7C  # '|'

        # Construccin de los campos
        comando = "0200".encode("ascii")
        monto = str(monto).zfill(9).encode("ascii")
        numero_ticket = numero_ticket.ljust(20, '0').encode("ascii")
        campo_impresion = str(envia_voucher).encode("ascii")
        campo_mensajes = str(envia_mensajes).encode("ascii")

        # Concatenar el mensaje
        mensaje = bytearray([stx])  # Inicio del mensaje
        mensaje.extend(comando)
        mensaje.append(separador)
        mensaje.extend(monto)
        mensaje.append(separador)
        mensaje.extend(numero_ticket)
        mensaje.append(separador)
        mensaje.extend(campo_impresion)
        mensaje.append(separador)
        mensaje.extend(campo_mensajes)
        mensaje.append(separador)
        mensaje.append(etx)  # Fin del mensaje

        # Calcular y agregar el LRC
        lrc = calcular_lrc(mensaje[1:])  # LRC se calcula desde despus del STX
        mensaje.append(lrc)

        return mensaje
        
    def generar_mensaje_cierre(self, envia_voucher):
        """
        Genera el mensaje de venta segn el formato del protocolo.
        """
        def calcular_lrc(data):
            """Calcula el LRC (Longitudinal Redundancy Check)."""
            lrc = 0
            for byte in data:
                lrc ^= byte
            return lrc

        stx = 0x02
        etx = 0x03
        separador = 0x7C  # '|'

        # Construccin de los campos
        comando = "0500".encode("ascii")
        campo_impresion = str(envia_voucher).encode("ascii")

        # Concatenar el mensaje
        mensaje = bytearray([stx])  # Inicio del mensaje
        mensaje.extend(comando)
        mensaje.append(separador)
        mensaje.extend(campo_impresion)
        mensaje.append(etx)  # Fin del mensaje

        # Calcular y agregar el LRC
        lrc = calcular_lrc(mensaje[1:])  # LRC se calcula desde despus del STX
        mensaje.append(lrc)

        return mensaje

    def generar_mensaje_carga_llaves(self):
        """
        Genera el mensaje de venta segn el formato del protocolo.
        """
        def calcular_lrc(data):
            """Calcula el LRC (Longitudinal Redundancy Check)."""
            lrc = 0
            for byte in data:
                lrc ^= byte
            return lrc

        stx = 0x02
        etx = 0x03
        separador = 0x7C  # '|'

        # Construccin de los campos
        comando = "0800".encode("ascii")
        # Concatenar el mensaje
        mensaje = bytearray([stx])  # Inicio del mensaje
        mensaje.extend(comando)
        mensaje.append(etx)  # Fin del mensaje

        # Calcular y agregar el LRC
        lrc = calcular_lrc(mensaje[1:])  # LRC se calcula desde despus del STX
        mensaje.append(lrc)

        return mensaje

    def generar_mensaje_polling(self):
        """
        Genera el mensaje de venta segn el formato del protocolo.
        """
        def calcular_lrc(data):
            """Calcula el LRC (Longitudinal Redundancy Check)."""
            lrc = 0
            for byte in data:
                lrc ^= byte
            return lrc

        stx = 0x02
        etx = 0x03
        separador = 0x7C  # '|'

        # Construccin de los campos
        comando = "0100".encode("ascii")
        # Concatenar el mensaje
        mensaje = bytearray([stx])  # Inicio del mensaje
        mensaje.extend(comando)
        mensaje.append(etx)  # Fin del mensaje

        # Calcular y agregar el LRC
        lrc = calcular_lrc(mensaje[1:])  # LRC se calcula desde despus del STX
        mensaje.append(lrc)

        return mensaje

    def enviar_ack(self):
        self.pos_serial.write(b'\x06')
        self.log("[ACK] Enviado al POS.")
        self.log_file.write("[ACK] Enviado al POS.\n")
        self.log_file.flush()

    def limpiar_mensaje(self, linea):
        # Elimina STX (0x02), ETX (0x03) y posibles bytes de control
        return linea.strip('\x02\x03\x08') 

    def enviar_cierre(self):
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log(f"{current_time} Enviando cierre")
        self.log_file.write(f"{current_time} Enviando cierre\n")
        self.log_file.flush()
        mensaje_cierre = self.generar_mensaje_cierre(1)
        self.pos_serial.write(mensaje_cierre)
        
    def enviar_cargaLlaves(self):
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log(f"{current_time} Enviando carga de llaves")
        self.log_file.write(f"{current_time} Enviando carga de llaves\n")
        self.log_file.flush()
        mensaje_carga_llaves = self.generar_mensaje_carga_llaves()
        self.pos_serial.write(mensaje_carga_llaves)
        
    def enviar_polling(self):
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log(f"{current_time} Enviando poll")
        self.log_file.write(f"{current_time} Enviando poll\n")
        self.log_file.flush()
        mensaje_polling = self.generar_mensaje_polling()
        self.pos_serial.write(mensaje_polling)
       
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
    if node_process.poll() is None:
        node_process.terminate()
    sys.exit()

def get_serial():
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("Serial"):
                    return line.split(":")[1].strip()
    except:
        return None

def dispositivo_conectado(ruta):
    return os.path.exists(ruta)
   
def toggle_gpio(led):
    led.off()
    time.sleep(0.5)
    led.on()

if __name__ == "__main__":
    # Validación serial
    serial_num = get_serial()
    
    if serial_num is None:
        print("Error en obtener datos")
        sys.exit(1)
            
    if serial_num != SERIAL_NUMBER:
        print("No compatible")
        sys.exit(1)
    
    # Ruta al programa Node.js
    #node_program = "./control_Pago_POS_v3.js"
    
    # Crear carpeta log si no existe
    if not os.path.exists("log"):
        os.makedirs("log")

    # Generar nombre de archivo de log y dex
    log_filename = f"./log/log_{datetime.datetime.now().strftime('%Y%m%d')}.txt"

    # Verificar si el archivo Node.js existe
    #if not os.path.exists(node_program):
    #    print("El programa Node.js no se encuentra en la ruta especificada.")
    #    exit()

    # Asociar la señal SIGINT (Ctrl+C) al manejador de salida
    signal.signal(signal.SIGINT, handle_exit)

    # Ejecutar el programa Node.js
    #node_process = subprocess.Popen(["node", node_program])
    
    try:
        with open(log_filename, "a") as log_file:
            print("iniciando")
            app = App(log_file)
            app.mainloop()
    finally:
        # Detener el hilo de SerialHandler cuando la aplicacin se cierre
        serial_handler.stop()
        serial_thread.join()

        # Si la interfaz se cierra, se termina el proceso de Node.js
        #if node_process.poll() is None:
        #    node_process.terminate()
