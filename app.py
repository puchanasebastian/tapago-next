import os
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
    """Recibe y valida las notificaciones de Nequi capturadas por la App Android"""
    global TRANSACCIONES
    data = request.get_json() or {}
    texto = data.get('texto', '')
    
    print(f"📥 Notificación recibida desde App TAPAGO: {texto}")
    
    # Normalizamos el texto en minúsculas para evaluar la transferencia de Nequi
    texto_lower = texto.lower()
    
    # Palabras clave habituales en las notificaciones push de Nequi / Bre-B
    if any(palabra in texto_lower for palabra in ["enviaron", "recibiste", "transfirió", "pago", "bre-b"]):
        
        # 1. Intentamos buscar un cobro PENDIENTE para marcarlo como APROBADO
        for pago in TRANSACCIONES:
            if pago.get('estado') == 'PENDIENTE':
                pago['estado'] = 'APROBADO'
                print(f"✅ Cobro APROBADO exitosamente para referencia: {pago.get('referencia')}")
                return jsonify({'status': 'exito', 'mensaje': 'Pago verificado y aprobado'}), 200
        
        # 2. Si el cliente transfirió directo sin cobro previo en la pantalla, registramos el pago
        transaccion_directa = {
            'celular': 'Nequi Directo',
            'monto': 'Verificado',
            'referencia': 'PUSH-AUTO',
            'estado': 'APROBADO'
        }
        TRANSACCIONES.insert(0, transaccion_directa)
        print("✅ Pago directo de Nequi registrado como APROBADO.")
        return jsonify({'status': 'exito', 'mensaje': 'Pago directo registrado'}), 200

    return jsonify({'status': 'ignorado', 'mensaje': 'La notificación no corresponde a un pago'}), 200

# ==========================================
# INICIALIZACIÓN DEL SERVIDOR
# ==========================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
