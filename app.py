import os
import re
import csv
from io import StringIO
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, jsonify, Response
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

# Cargar variables de entorno
load_dotenv()

app = Flask(__name__)

# Configuración de Conexión a PostgreSQL en Render
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    """Establece conexión con la base de datos PostgreSQL"""
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    """Crea la tabla de transacciones si no existe"""
    if DATABASE_URL:
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute('''
                CREATE TABLE IF NOT EXISTS transacciones (
                    id SERIAL PRIMARY KEY,
                    celular VARCHAR(100),
                    monto INT,
                    referencia VARCHAR(150),
                    estado VARCHAR(50),
                    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
            conn.commit()
            cur.close()
            conn.close()
            print("✅ Base de datos PostgreSQL inicializada correctamente.")
        except Exception as e:
            print(f"❌ Error al inicializar la base de datos: {e}")

# Inicializar DB al arrancar el servidor
init_db()

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
    """Obtiene el historial desde SQL o genera un nuevo cobro desde la web"""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if request.method == 'POST':
        data = request.get_json() or {}
        celular = data.get('celular', 'Anonimo')
        monto = data.get('monto', 0)
        referencia = data.get('referencia', 'REF-PENDIENTE')
        estado = 'PENDIENTE'

        cur.execute(
            "INSERT INTO transacciones (celular, monto, referencia, estado) VALUES (%s, %s, %s, %s) RETURNING id;",
            (celular, monto, referencia, estado)
        )
        conn.commit()
        nueva_id = cur.fetchone()['id']
        cur.close()
        conn.close()

        nueva_tx = {
            'id': nueva_id,
            'celular': celular,
            'monto': monto,
            'referencia': referencia,
            'estado': estado
        }
        return jsonify({'exito': True, 'transaccion': nueva_tx}), 201
    
    # GET: Devuelve las últimas 50 transacciones desde la BD
    cur.execute("SELECT id, celular, monto, referencia, estado, fecha FROM transacciones ORDER BY id DESC LIMIT 50;")
    filas = cur.fetchall()
    cur.close()
    conn.close()

    transacciones_list = []
    for f in filas:
        transacciones_list.append({
            'id': f['id'],
            'celular': f['celular'],
            'monto': f['monto'],
            'referencia': f['referencia'],
            'estado': f['estado']
        })

    return jsonify(transacciones_list), 200

@app.route('/api/resumen-hoy', methods=['GET'])
def resumen_hoy():
    """Retorna el total sumado de ventas aprobadas hoy y el conteo"""
    zona_colombia = timezone(timedelta(hours=-5))
    hoy_inicio = datetime.now(zona_colombia).replace(hour=0, minute=0, second=0, microsecond=0)
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute(
        "SELECT SUM(monto) as total_ventas, COUNT(id) as total_tx FROM transacciones WHERE estado = 'APROBADO' AND fecha >= %s;",
        (hoy_inicio,)
    )
    resultado = cur.fetchone()
    cur.close()
    conn.close()

    total_ventas = resultado['total_ventas'] or 0
    total_tx = resultado['total_tx'] or 0

    return jsonify({
        'total_ventas': total_ventas,
        'total_tx': total_tx
    }), 200

@app.route('/api/exportar-excel', methods=['GET'])
def exportar_excel():
    """Genera y descarga un archivo CSV compatible con Excel con el historial completo"""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, celular, monto, referencia, estado, fecha FROM transacciones ORDER BY id DESC;")
    filas = cur.fetchall()
    cur.close()
    conn.close()

    si = StringIO()
    writer = csv.writer(si)
    # Encabezados
    writer.writerow(['ID', 'Banco / Remitente', 'Monto (COP)', 'Referencia', 'Estado', 'Fecha'])

    for f in filas:
        writer.writerow([
            f['id'],
            f['celular'],
            f['monto'],
            f['referencia'],
            f['estado'],
            f['fecha'].strftime("%Y-%m-%d %H:%M:%S") if f['fecha'] else ''
        ])

    output = si.getvalue()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=reporte_ventas_tapago.csv"}
    )

@app.route('/api/webhook-notificacion', methods=['POST'])
def webhook_notificacion():
    """Recibe, analiza y clasifica las notificaciones capturadas por la App Android"""
    data = request.get_json() or {}
    texto = data.get('texto', '') or data.get('mensaje', '')
    
    print(f"📥 Notificación recibida desde App TAPAGO: {texto}")
    
    if not texto:
        return jsonify({'status': 'ignorado', 'mensaje': 'Sin contenido'}), 400

    texto_lower = texto.lower()
    palabras_clave = ["enviaron", "recibiste", "transfirió", "pago", "bre-b", "transfiya", "aceptaste"]
    
    if any(palabra in texto_lower for palabra in palabras_clave):
        
        # 1. EXTRACCIÓN DINÁMICA DEL MONTO
        monto_match = re.search(r'\$\s?([\d\.,]+)', texto)
        monto_str = monto_match.group(1) if monto_match else "0"
        
        try:
            monto_limpio = int(re.sub(r'[^\d]', '', monto_str))
        except ValueError:
            monto_limpio = 0

        # 2. IDENTIFICACIÓN DE ORIGEN / BANCO
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
            nombre_match = re.search(r'de\s+([A-Za-z\s]+?)(?=\s+(te|desde|por|a|\$|$))', texto, re.IGNORECASE)
            if nombre_match:
                remitente = nombre_match.group(1).strip()

        # 3. HORA LOCAL COLOMBIA (UTC-5)
        zona_colombia = timezone(timedelta(hours=-5))
        hora_actual = datetime.now(zona_colombia).strftime("%I:%M %p")
        ref_id = f"PUSH-{int(datetime.now().timestamp())}"
        referencia_completa = f"{ref_id} • {hora_actual}"

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 4. SI EXISTE UN COBRO PENDIENTE, LO MARCAMOS COMO APROBADO
        cur.execute("SELECT * FROM transacciones WHERE estado = 'PENDIENTE' ORDER BY id DESC LIMIT 1;")
        pago_pendiente = cur.fetchone()

        if pago_pendiente:
            monto_final = monto_limpio if monto_limpio > 0 else pago_pendiente['monto']
            cur.execute(
                "UPDATE transacciones SET estado = 'APROBADO', celular = %s, monto = %s WHERE id = %s;",
                (remitente, monto_final, pago_pendiente['id'])
            )
            conn.commit()
            cur.close()
            conn.close()
            print(f"✅ Cobro PENDIENTE ID {pago_pendiente['id']} APROBADO.")
            return jsonify({'status': 'exito', 'mensaje': 'Pago pendiente aprobado'}), 200

        # 5. REGISTRO DE TRANSACCIÓN DIRECTA
        cur.execute(
            "INSERT INTO transacciones (celular, monto, referencia, estado) VALUES (%s, %s, %s, %s);",
            (remitente, monto_limpio, referencia_completa, 'APROBADO')
        )
        conn.commit()
        cur.close()
        conn.close()

        print(f"✅ Pago directo guardado en BD: ${monto_limpio} COP de {remitente}")
        return jsonify({'status': 'exito', 'mensaje': 'Pago directo registrado'}), 200

    return jsonify({'status': 'ignorado', 'mensaje': 'La notificación no corresponde a un pago'}), 200

# ==========================================
# INICIALIZACIÓN DEL SERVIDOR
# ==========================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
