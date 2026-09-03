import uuid
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv
from nequi_service import NequiEngine

load_dotenv()

app = Flask(__name__)
nequi = NequiEngine()

# Almacén temporal de transacciones en memoria
pagos_recientes = []

# 1. Vista Cliente (NFC)
@app.route('/')
def inicio():
    return render_template('index.html')

# 2. Vista Tendero (Panel de Confirmación)
@app.route('/tendero')
def panel_tendero():
    return render_template('tendero.html')

# 3. Endpoint para que el cliente solicite el pago
@app.route('/api/cobrar', methods=['POST'])
def procesar_cobro():
    data = request.get_json() or {}
    celular = data.get('celular')
    monto = data.get('monto')

    if not celular or not monto:
        return jsonify({
            'exito': False, 
            'mensaje': 'Falta el número de celular o el monto.'
        }), 400

    referencia = f"TAPAGO-{uuid.uuid4().hex[:6].upper()}"
    resultado = nequi.solicitar_cobro_push(celular, monto, referencia)

    pago_registro = {
        'referencia': referencia,
        'celular': celular,
        'monto': monto,
        'estado': 'APROBADO'  # En producción cambiará tras webhook de Nequi
    }
    
    # Guardar la transacción al inicio de la lista
    pagos_recientes.insert(0, pago_registro)

    return jsonify({
        'exito': True,
        'referencia': referencia,
        'monto': monto,
        'respuesta_nequi': resultado
    })

# 4. Endpoint para que el panel del tendero consulte pagos
@app.route('/api/pagos-tendero', methods=['GET'])
def obtener_pagos():
    return jsonify({
        'exito': True,
        'pagos': pagos_recientes[:10]  # Devuelve los últimos 10 pagos
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)