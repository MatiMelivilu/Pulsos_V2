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
from gpiozero.pins.native import NativeFactory
from gpiozero.pins.rpigpio import RPiGPIOFactory
from gpiozero import Device, LED, Button

#ruta conversor pulsos
ruta_pulsos   = '/dev/ttyUSB0'

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
        self.title("Pagos pulsos")


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
            
        self.socket_thread = threading.Thread(target=self.socket_listener, daemon=True)
        self.socket_thread.start()
        
#Configurion de los puertos GPIO        
    def GPIOconf(self):
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mlog = f"[{current_time}] configurando GPIO\n"
        self.log(mlog)
        self.log_file.write(mlog)
        self.log_file.flush()
        factory = NativeFactory()
        self.inCH1 = Button(2)
        self.inCH2 = Button(3)
        self.inCH3 = Button(4)
        self.inCH4 = Button(17)
        
        self.outCH1 = LED(19, pin_factory=factory)
        self.outCH1.on()
        self.outCH2 = LED(26, pin_factory=factory)
        self.outCH2.on()
        self.outCH3 = LED(20, pin_factory=factory)
        self.outCH3.on()
        self.outCH4 = LED(21, pin_factory=factory)
        self.outCH4.on()  
        
        self.outs = [self.outCH1, self.outCH2, self.outCH3, self.outCH4]  
                
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
        self.habilitar_botones()
    
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
        self.habilitar_botones()
    
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
        self.habilitar_botones()
    
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
        self.habilitar_botones()

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
        
         tk.Label(self.pos_frame, text="Control POS", font=font_large).grid(row=0, column=1, columnspan=1, pady=10)
        
         send_button1 = tk.Button(self.pos_frame, text="Poll", font=font_large, width=pos_width, height=pos_height, command=self.Poll)
         send_button1.grid(row=1, column=0, padx=5)
        
         send_button2 = tk.Button(self.pos_frame, text="Cierre de caja", font=font_large, width=pos_width, height=pos_height, command=self.cierre_caja)
         send_button2.grid(row=1, column=1, padx=5)
        
         send_button3 = tk.Button(self.pos_frame, text="Ultima venta", font=font_large, width=pos_width, height=pos_height, command=self.ultima_venta)
         send_button3.grid(row=1, column=2, padx=5)
        
         send_button4 = tk.Button(self.pos_frame, text="Cancelar ult. venta", font=font_large, width=pos_width, height=pos_height, command=self.devolucion)
         send_button4.grid(row=2, column=0, padx=5)
        
         send_button5 = tk.Button(self.pos_frame, text="Inizializacion", font=font_large, width=pos_width, height=pos_height, command=self.init)
         send_button5.grid(row=2, column=1, padx=5)
        
         send_button6 = tk.Button(self.pos_frame, text="Respuesta ini.", font=font_large, width=pos_width, height=pos_height, command=self.respuesta_inicializacion)
         send_button6.grid(row=2, column=2, padx=5)
        
         send_button7 = tk.Button(self.pos_frame, text="Carga llaves", font=font_large, width=pos_width, height=pos_height, command=self.cargar_llaves)
         send_button7.grid(row=3, column=0, padx=5)
        
        
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
         
         send_button2 = tk.Button(self.test_frame, text="test pulso", font=font_large, width=pos_width, height=pos_height, command=self.test_pulso)
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
        global ruta_pulsos
        global POS_OPERATION
        precio = 500
        n = 1
        pos = self.POS(precio)
        POS_OPERATION = True
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
        
    def cargar_llaves(self):
        global POS_OPERATION
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.connect(('localhost', 3000))  
        client_socket.send(("02").encode())
        respuesta = client_socket.recv(1024).decode()
        POS_OPERATION = True
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mlog = f"[{current_time}] POS: Carga de llaves {respuesta}\n"
        self.log(mlog)
        self.log_file.write(mlog)
        self.log_file.flush()
        client_socket.close()
        return respuesta 
        
    def cierre_caja(self):
        global POS_OPERATION
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.connect(('localhost', 3000))  
        client_socket.send(("03").encode())
        respuesta = client_socket.recv(1024).decode()
        POS_OPERATION = True
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mlog = f"[{current_time}] POS: Cierre de caja {respuesta}\n"
        self.log(mlog)
        self.log_file.write(mlog)
        self.log_file.flush()
        client_socket.close()
        return respuesta 
        
    def init(self):
        global POS_OPERATION
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.connect(('localhost', 3000))  
        client_socket.send(("04").encode())
        respuesta = client_socket.recv(1024).decode()
        POS_OPERATION = True
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mlog = f"[{current_time}] POS: inicializacion {respuesta}\n"
        self.log(mlog)
        self.log_file.write(mlog)
        self.log_file.flush()
        client_socket.close()
        return respuesta 
        
    def ultima_venta(self):
        global POS_OPERATION
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.connect(('localhost', 3000))  
        client_socket.send(("05").encode())
        respuesta = client_socket.recv(1024).decode()
        POS_OPERATION = True
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mlog = f"[{current_time}] POS: Ultima venta {respuesta}\n"
        self.log(mlog)
        self.log_file.write(mlog)
        self.log_file.flush()
        client_socket.close()
        return respuesta 
        
    def respuesta_inicializacion(self):
        global POS_OPERATION
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.connect(('localhost', 3000))  
        client_socket.send(("07").encode())
        respuesta = client_socket.recv(1024).decode()
        POS_OPERATION = True
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mlog = f"[{current_time}] POS: Respuesta de inicializacion {respuesta}\n"
        self.log(mlog)
        self.log_file.write(mlog)
        self.log_file.flush()
        client_socket.close()
        return respuesta 
        
    def devolucion(self):
        pass
        
    def dispositivo_conectado(self, ruta):
            return os.path.exists(ruta)  
          
    def enviar_pulsos(self, n):
        global ruta_pulsos
        ser = serial.Serial(ruta_pulsos, 9600)
        message = '30313233000A'
        for i in range(n):
            ser.write(bytes.fromhex(message))
            print('mensaje enviado: ', message)
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            mlog = f"[{current_time}] Enviando: {message}\n"
            self.log(mlog)
            self.log_file.write(mlog)
            self.log_file.flush()
            time.sleep(1)
        ser.close()
        time.sleep(1)          


    def Poll(self):
        global POS_OPERATION
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.connect(('localhost', 3000))  
        client_socket.send(("06").encode())
        respuesta = client_socket.recv(1024).decode()
        POS_OPERATION = True
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mlog = f"[{current_time}] POS: Poll {respuesta}\n"
        self.log(mlog)
        self.log_file.write(mlog)
        self.log_file.flush()
        client_socket.close()

        return respuesta 
        
                
    def POS(self, precio):
        """ Envia el precio al POS y espera respuesta """
        precio = int(precio)
        try:
            with socket.create_connection(('localhost', 3000)) as client_socket:
                client_socket.send(("01" + str(precio)).encode())
                respuesta = client_socket.recv(1024).decode().strip()
            return respuesta
        except (socket.error, Exception) as e:
            print(f"Error en POS() de python: {e}")
            return "Error en python"

    def venta(self):
        """ Maneja el proceso de venta sin bloquear la escucha de sockets """
        global entry
        global ruta_pulsos
        global POS_CONNECT
        POS_CONNECT = 1
        global n_inputs

        try:
            with open('valores.txt', 'r') as file:
                for i, line in enumerate(file):
                    if i == entry - 1:
                        precio, n_pulsos = line.strip().split()
                        break
            print(precio, n_pulsos)
            self.deshabilitar_botones()
            
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            mlog = f"[{current_time}] Iniciando venta por {precio} \n"
            self.log(mlog)
            self.log_file.write(mlog)
            self.log_file.flush()

            if dispositivo_conectado(ruta_pulsos):
                venta_resultado = self.POS(precio)
                print(venta_resultado)
                print(n_pulsos)
                current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                mlog = f"[{current_time}] POS: {venta_resultado}\n"
                self.log(mlog)
                self.log_file.write(mlog)
                self.log_file.flush()
                self.habilitar_botones()

                if venta_resultado == 'Aprobado':
                    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    mlog = f"[{current_time}] Enviando {n_pulsos} creditos\n"
                    self.log(mlog)
                    self.log_file.write(mlog)
                    self.log_file.flush()
                    self.toggle_gpio(self.outs[entry - 1])
                    self.enviar_pulsos(int(n_pulsos))
                    time.sleep(1)
                else:
                    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    mlog = f"[{current_time}] No se envia credito\n"
                    self.log(mlog)
                    self.log_file.write(mlog)
                    self.log_file.flush()
                    

            else:
                current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                mlog = f"[{current_time}] Conversor de pulsos no conectado\n"
                self.log(mlog)
                self.log_file.write(mlog)
                self.log_file.flush()

        except FileNotFoundError:
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            mlog = f"[{current_time}] Error en venta: Archivo valores.txt no encontrado.\n"
            self.log(mlog)
            self.log_file.write(mlog)
            self.log_file.flush()
        except Exception as e:
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            mlog = f"[{current_time}] Error en venta: {e}\n"
            self.log(mlog)
            self.log_file.write(mlog)
            self.log_file.flush()

    def socket_listener(self):
        global POS_OPERATION
        """ Mantiene la conexion con Node.js y escucha mensajes """
        while self.running:
            try:
                self.client_socket = socket.create_connection(('localhost', 3000), timeout=5)
                self.client_socket.settimeout(5)
                POS_OPERATION = False
                print("Conectado al servidor de Node.js")
                while self.running:
                    ready, _, _ = select.select([self.client_socket], [], [], 1)
                    if ready:
                        respuesta = self.client_socket.recv(1024).decode().strip()
                        if respuesta:
                            print('Python lee:', respuesta)
                            self.venta()
                            break
                        else:
                            print('Conexion cerrada por el servidor.')
                            break  # Salir del loop interno para reconectar

                    elif POS_OPERATION == True:
                        print('operacion terminada')
                        break
                    else:
                        pass

            except (socket.error, Exception) as e:
                print(f'Error en la conexion: {e}')
                time.sleep(5)  # Espera antes de reintentar

            finally:
                if self.client_socket:
                    self.client_socket.close()
                    self.client_socket = None  # Asegurar reinicio
                    print("Socket cerrado, reintentando conexion...")

    def stop_listener(self):
        """ Detiene el listener de sockets """
        self.running = False
        if self.client_socket:
            self.client_socket.close()


                     
        
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
   
if __name__ == "__main__":
   
    # Ruta al programa Node.js
    node_program = "./control_Pago_POS_v3.js"
    
    # Crear carpeta log si no existe
    if not os.path.exists("log"):
        os.makedirs("log")

    # Generar nombre de archivo de log y dex
    log_filename = f"./log/log_{datetime.datetime.now().strftime('%Y%m%d')}.txt"

    # Verificar si el archivo Node.js existe
    if not os.path.exists(node_program):
        print("El programa Node.js no se encuentra en la ruta especificada.")
        exit()

    # Asociar la señal SIGINT (Ctrl+C) al manejador de salida
    signal.signal(signal.SIGINT, handle_exit)

    # Ejecutar el programa Node.js
    node_process = subprocess.Popen(["node", node_program])
    
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
        if node_process.poll() is None:
            node_process.terminate()
