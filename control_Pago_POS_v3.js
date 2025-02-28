const { POSAutoservicio } = require('transbank-pos-sdk');
const net = require('net');
const fs  = require('fs');
const path= require('path');
const { SerialPort } = require('serialport');
const pos = new POSAutoservicio();

let pythonSocket = null;  // Referencia al socket de Python
let posConectado = false; // Estado de la conexion al POS
let reconectando = false; // Evita multiples intentos de reconexion simultaneos
let puertoSerialPOS = null; // Puerto serie donde se conecta el POS
global.POS_PORT = "/dev/ttyACM0"

// Conectar al POS a traves de USB (Puerto serie)
async function conectarPOS() {
    try {
        // Si ya esta conectado, evitamos reconectar
        if (posConectado) return;
        
        // Abrir el puerto serie una sola vez
        puertoSerialPOS = new SerialPort({
            path: global.POS_PORT, // Puerto donde esta conectado el POS
            baudRate: 9600, // Asegurate de que coincida con la configuracion del POS
            autoOpen: true // Abrir automaticamente el puerto
        });

        puertoSerialPOS.on('open', () => {
            console.log('Puerto serie abierto correctamente.');
            posConectado = true;
        });

        puertoSerialPOS.on('data', (data) => {
            console.log('Mensaje recibido del POS por USB:', data.toString());
            if (pythonSocket) {
                pythonSocket.write(`POS_EVENT:${data.toString()}`);
                console.log('Mensaje enviado a Python.');
            } else {
                console.log('No hay conexion con Python.');
            }
        });

        puertoSerialPOS.on('error', (error) => {
            console.error('Error en el puerto serie:', error);
            //posConectado = false;
            intentarReconectarPOS();
        });

        puertoSerialPOS.on('close', () => {
            console.log('Puerto serie cerrado.');
            //posConectado = false;
            intentarReconectarPOS();
        });

    } catch (error) {
        console.error('Error al conectar con el POS:', error);
        intentarReconectarPOS();
    }
}

// Funcion para intentar reconectar el POS en caso de error
function intentarReconectarPOS() {
    if (reconectando) return;
    reconectando = true;

    console.log('Intentando reconectar el POS en 5 segundos...');
    setTimeout(async () => {
        reconectando = false;
        posConectado = false;
        if (!posConectado) {
            console.log('Intentando reconectar el POS...');
            await conectarPOS();
        }
    }, 1000);
}
function cerrarConexionPOS() {
    if (puertoSerialPOS && puertoSerialPOS.isOpen) {
        puertoSerialPOS.close((err) => {
            if (err) {
                console.error('Error al cerrar el puerto serie:', err);
            } else {
                console.log('Puerto serie cerrado correctamente.');
                posConectado = false;
            }
        });
    } else {
        console.log('El puerto serie ya esta cerrado o no esta abierto.');
    }
}
// Funcion para manejar transacciones sin desconectar el POS
async function runTransaction(precio, socket) {
    try {
        // Cerrar el puerto serie antes de usar el POS      
        reconectando = true;
        posConectado = true;
        cerrarConexionPOS();

        // Conectar al POS
        await pos.connect(global.POS_PORT);
        console.log('POS conectado correctamente');

        // Realizar la venta
        const amount = precio;
        const orderId = Date.now().toString(); // Orden unica basada en timestamp
        const saleResponse = await pos.sale(amount, orderId);
        
        // Directorio donde están los archivos .txt (ajusta la ruta según tu carpeta)
        const folderPath = path.join('./log');
        
        // Verificar si la carpeta existe
        if (!fs.existsSync(folderPath)) {
            fs.mkdirSync(folderPath, { recursive: true });
            console.log(`Carpeta creada: ${folderPath}`);
        }

        // Obtener la lista de archivos en la carpeta
        let files = fs.readdirSync(folderPath);

        // Filtrar solo los archivos .txt que siguen el patrón 'log_YYYYMMDD.txt'
        let logFiles = files.filter(file => file.startsWith('log_') && file.endsWith('.txt'));
        
        if (logFiles.length > 0) {
            // Ordenar los archivos por nombre de forma descendente (el más reciente primero)
            logFiles.sort((a, b) => {
                const dateA = a.match(/log_(\d+)\.txt/);
                const dateB = b.match(/log_(\d+)\.txt/);
                return dateB[1] - dateA[1]; // Comparar por fecha y luego por hora
            });

            // El archivo más reciente
            const latestLogFile = logFiles[0];
            const filePath = path.join(folderPath, latestLogFile);

            // Formato de la respuesta que será guardada
            const formattedResponse = `\n************************\n${JSON.stringify(saleResponse, null, 2)}\n************************\n`;

            // Guardar la respuesta en el archivo, agregándola sin sobrescribir lo anterior
            fs.appendFileSync(filePath, formattedResponse);
            console.log(`SaleResponse guardado en ${filePath}`);
        } else {
            console.log("No se encontraron archivos .txt en la carpeta");
        }

        if (saleResponse.successful) {
            console.log('Transaccion exitosa:', saleResponse);
			// Enviar la respuesta de vuelta a Python
			const responseMessage = saleResponse?.responseMessage ?? "Fallo de respuesta";
			socket.write(responseMessage);

        } else {
            console.log('Transaccion rechazada:', saleResponse);
			// Enviar la respuesta de vuelta a Python
			const responseMessage = saleResponse?.responseMessage ?? "Fallo de respuesta";
			socket.write(responseMessage);
        }

        // Desconectar el POS
        await pos.disconnect();
        console.log('POS desconectado correctamente');

        // Reabrir la conexion al puerto serie
        reconectando = false;
        posConectado = false;
        conectarPOS();

    } catch (error) {
        console.error('Error en transaccion:', error);
        socket.write('Error en transaccion');
    }
}

async function poll_POS(socket) {
    try {
        // Cerrar el puerto serie antes de usar el POS      
        reconectando = true;
        posConectado = true;
        cerrarConexionPOS();

        // Conectar al POS
        await pos.connect(global.POS_PORT);
        console.log('POS conectado correctamente');

        const pollResponse = await pos.poll();
		if (pollResponse){
			socket.write('True');
		}
        // Desconectar el POS
        await pos.disconnect();
        console.log('POS desconectado correctamente');

        // Reabrir la conexion al puerto serie
        reconectando = false;
        posConectado = false;
        conectarPOS();

    } catch (error) {
        console.error('Error en transaccion:', error);
        socket.write('Error en transaccion');
    }
}

async function cargarLlaves(socket) {
    try {
        // Cerrar el puerto serie antes de usar el POS      
        reconectando = true;
        posConectado = true;
        cerrarConexionPOS();

        // Conectar al POS
        await pos.connect(global.POS_PORT);
        console.log('POS conectado correctamente');

		// Carga de llaves
		const keyResult = await pos.loadKeys();
		console.log('Carga de llaves ejecutada. Respuesta:', keyResult);
		socket.write(keyResult.responseMessage);

        // Desconectar el POS
        await pos.disconnect();
        console.log('POS desconectado correctamente');

        // Reabrir la conexion al puerto serie
        reconectando = false;
        posConectado = false;
        conectarPOS();

    } catch (error) {
        console.error('Error en transaccion:', error);
        socket.write('Error en transaccion');
    }
}

async function cierre(socket) {
    try {
        // Cerrar el puerto serie antes de usar el POS      
        reconectando = true;
        posConectado = true;
        cerrarConexionPOS();

        // Conectar al POS
        await pos.connect(global.POS_PORT);
        console.log('POS conectado correctamente');

		// Cierre de caja
		const closeResult = await pos.closeDay(true);
		console.log('Cierre de caja ejecutada. Respuesta:', closeResult);
		socket.write(closeResult.responseMessage);

        // Desconectar el POS
        await pos.disconnect();
        console.log('POS desconectado correctamente');

        // Reabrir la conexion al puerto serie
        reconectando = false;
        posConectado = false;
        conectarPOS();

    } catch (error) {
        console.error('Error en transaccion:', error);
        socket.write('Error en transaccion');
    }
}

async function inicializacion(socket) {
    try {
        // Cerrar el puerto serie antes de usar el POS      
        reconectando = true;
        posConectado = true;
        cerrarConexionPOS();

        // Conectar al POS
        await pos.connect(global.POS_PORT);
        console.log('POS conectado correctamente');

		// Inicialización
		const init = await pos.initialization();
		console.log("Resultado de ejecucion:", init);
		socket.write("inicializacion realizada");

        // Desconectar el POS
        await pos.disconnect();
        console.log('POS desconectado correctamente');

        // Reabrir la conexion al puerto serie
        reconectando = false;
        posConectado = false;
        conectarPOS();

    } catch (error) {
        console.error('Error en transaccion:', error);
        socket.write('Error en transaccion');
    }
}

async function ultima_venta(socket) {
    try {
        // Cerrar el puerto serie antes de usar el POS      
        reconectando = true;
        posConectado = true;
        cerrarConexionPOS();

        // Conectar al POS
        await pos.connect(global.POS_PORT);
        console.log('POS conectado correctamente');

		const u_venta = await pos.getLastSale(true);
		console.log('Ultima venta ejecutada. Respuesta:', u_venta);
		socket.write(u_venta.responseMessage);

        // Desconectar el POS
        await pos.disconnect();
        console.log('POS desconectado correctamente');

        // Reabrir la conexion al puerto serie
        reconectando = false;
        posConectado = false;
        conectarPOS();

    } catch (error) {
        console.error('Error en transaccion:', error);
        socket.write('Error en transaccion');
    }
}

async function respuesta_inicializacion(socket) {
    try {
        // Cerrar el puerto serie antes de usar el POS      
        reconectando = true;
        posConectado = true;
        cerrarConexionPOS();
        // Conectar al POS
        await pos.connect(global.POS_PORT);
        console.log('POS conectado correctamente');
		const initResult = await pos.initializationResponse();
		console.log('Inicialización ejecutada. Respuesta:', initResult);
		console.log('Inicialización ejecutada. Respuesta:', initResult.responseMessage);
		socket.write(initResult.responseCode.toString());
        // Desconectar el POS
        await pos.disconnect();
        console.log('POS desconectado correctamente');

        // Reabrir la conexion al puerto serie
        reconectando = false;
        posConectado = false;
        conectarPOS();

    } catch (error) {
        console.error('Error en transaccion:', error);
        socket.write('Error en transaccion');
    }
}

async function cancelar_ultima_venta(socket) {
    try {
        // Cerrar el puerto serie antes de usar el POS      
        reconectando = true;
        posConectado = true;
        cerrarConexionPOS();

        // Conectar al POS
        await pos.connect(global.POS_PORT);
        console.log('POS conectado correctamente');

		const anulacion = [0x31, 0x32, 0x30, 0x30]
		const sendRes = await pos.send(anulacion, waitResponde = true, callback = null);
		console.log('Respuesta de POS:', sendRes);
		socket.write(sendRes.toString());

        // Desconectar el POS
        await pos.disconnect();
        console.log('POS desconectado correctamente');

        // Reabrir la conexion al puerto serie
        reconectando = false;
        posConectado = false;
        conectarPOS();

    } catch (error) {
        console.error('Error en transaccion:', error);
        socket.write('Error en transaccion');
    }
}
// Servidor de sockets para Python
const server = net.createServer((socket) => {
    console.log('Cliente Python conectado');
    pythonSocket = socket;

    socket.on('data', async (data) => {
        const dato = data.toString();

        if (dato.startsWith("01")) {
            const precio = parseFloat(dato.substring(2));
            console.log("Valor recibido:", precio);
            await runTransaction(precio, socket);
    } else if (dato.startsWith("02")) {
        await cargarLlaves(socket);
        
    } else if (dato.startsWith("03")) {
        await cierre(socket);
        
    } else if (dato.startsWith("04")) {
        await inicializacion(socket);
        
    } else if (dato.startsWith("05")) {
        await ultima_venta(socket);

    } else if (dato.startsWith("06")) {
        await poll_POS(socket);

    } else if (dato.startsWith("07")) {
        await respuesta_inicializacion(socket);

    } else if (dato.startsWith("08")) {
        await cancelar_ultima_venta(socket);            

        } else {
            console.log("Formato de mensaje no valido.");
            socket.write("Formato de mensaje no valido.");
            socket.end();
        }
    });

});

const PORT = 3000;
server.listen(PORT, () => {
    console.log(`Servidor Node.js escuchando en el puerto ${PORT}`);
});

// Mantener la conexion con el POS a traves de USB
conectarPOS();
