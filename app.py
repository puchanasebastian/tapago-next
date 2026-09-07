import os
import re
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

app = Flask(__name__)

# Estructura en memoria para almacenar las transacciones temporalmente
TRANSACCIONES = []

# ==========================================
# RUTAS DE INTERFAZ DE USUARIO (FRONTEND)
# ==========================================

@app.route('/')
def checkout():
    """Pantalla pública del cliente (Checkout NFC)"""
    return render_template('index.html')

@app.route('/tendero')
def tendero():
    """Panel privado en tiempo real para el comerciante/POS"""
    return render_template('tendero.html')

# ==========================================
# ENDPOINTS DE API & WEBHOOKS
# ==========================================

@app.route('/api/transacciones', methods=['GET', 'POST'])
def gestionar_transacciones():
    """Obtiene el historial o genera un nuevo cobro desde la web"""
    global TRANSACCIONES
    if request.method == 'POST':
        data = request.get_json() or {}
        nueva_transaccion = {
            'celular': data.get('celular', 'Anonimo'),
            'monto': data.get('monto', 0),
            'referencia': data.get('referencia', 'REF-PENDIENTE'),
            'estado': 'PENDIENTE'
        }
        TRANSACCIONES.insert(0, nueva_transaccion)
        return jsonify({'exito': True, 'transaccion': nueva_transaccion}), 201
    
    # Si es GET, devuelve las últimas transacciones
    return jsonify(TRANSACCIONES), 200

@app.route('/api/webhook-notificacion', methods=['POST'])
def webhook_notificacion():
    """Recibe, analiza y clasifica las notificaciones capturadas por la App Android"""
    global TRANSACCIONES
    data = request.get_json() or {}
    texto = data.get('texto', '') or data.get('mensaje', '')
    
    print(f"📥 Notificación recibida desde App TAPAGO: {texto}")
    
    if not texto:
        return jsonify({'status': 'ignorado', 'mensaje': 'Sin contenido'}), 400

    texto_lower = texto.lower()
    
    # Palabras clave para validar si es un movimiento financiero
    palabras_clave = ["enviaron", "recibiste", "transfirió", "pago", "bre-b", "transfiya", "aceptaste"]
    
    if any(palabra in texto_lower for palabra in palabras_clave):
        
        # 1. EXTRACCIÓN DINÁMICA DEL MONTO ($X.XXX)
        monto_match = re.search(r'\$\s?([\d\.,]+)', texto)
        monto_str = monto_match.group(1) if monto_match else "0"
        
        try:
            monto_limpio = int(re.sub(r'[^\d]', '', monto_str))
        except ValueError:
            monto_limpio = 0

        # 2. IDENTIFICACIÓN DE ORIGEN / BANCO / REMITENTE
        remitente = "Nequi Directo"
        
        if "bancolombia" in texto_lower:
            remitente = "Bancolombia"
        elif "daviplata" in texto_lower:
            remitente = "Daviplata"
        elif "transfiya" in texto_lower:
            remitente = "Transfiya"
        elif "bre-b" in texto_lower:
            remitente = "Bre-B (Interbancario)"
        elif "qr" in texto_lower:
            remitente = "Pago QR Nequi"
        elif " de " in texto_lower:
            # Intenta capturar nombres completos tipo "de Juan Perez"
            nombre_match = re.search(r'de\s+([A-Za-z\s]+?)(?=\s+(te|desde|por|a|\$|$))', texto, re.IGNORECASE)
            if nombre_match:
                remitente = nombre_match.group(1).strip()

        # 3. HORA LOCAL COLOMBIA (UTC-5) Y REFERENCIA
        zona_colombia = timezone(timedelta(hours=-5))
        hora_actual = datetime.now(zona_colombia).strftime("%I:%M %p")
        ref_id = f"PUSH-{int(datetime.now().timestamp())}"

        # 4. SI EXISTE UN COBRO PENDIENTE, LO ACTUALIZAMOS
        for pago in TRANSACCIONES:
            if pago.get('estado') == 'PENDIENTE':
                pago['estado'] = 'APROBADO'
                pago['celular'] = remitente
                if monto_limpio > 0:
                    pago['monto'] = monto_limpio
                print(f"✅ Cobro PENDIENTE APROBADO: {pago.get('referencia')}")
                return jsonify({'status': 'exito', 'mensaje': 'Pago pendiente aprobado'}), 200

        # 5. SI NO HAY COBRO PREVIO, REGISTRAMOS TRANSACCIÓN DIRECTA DETALLADA
        transaccion_directa = {
            'celular': remitente,
            'monto': monto_limpio if monto_limpio > 0 else 'Verificado',
            'referencia': f"{ref_id} • {hora_actual}",
            'estado': 'APROBADO'
        }
        TRANSACCIONES.insert(0, transaccion_directa)
        print(f"✅ Pago directo registrado a las {hora_actual}: ${monto_limpio} COP desde {remitente}")
        return jsonify({'status': 'exito', 'mensaje': 'Pago directo registrado'}), 200

    return jsonify({'status': 'ignorado', 'mensaje': 'La notificación no corresponde a un pago'}), 200

# ==========================================
# INICIALIZACIÓN DEL SERVIDOR
# ==========================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
