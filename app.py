import os
import re
import csv
from io import StringIO
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, jsonify, Response, redirect, url_for, flash
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'tapago-secret-key-2026')

DATABASE_URL = os.environ.get('DATABASE_URL')

# Configuración Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class Usuario(UserMixin):
    def __init__(self, id, nombre, correo, nequi):
        self.id = id
        self.nombre = nombre
        self.correo = correo
        self.nequi = nequi

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, nombre, correo, nequi FROM usuarios WHERE id = %s;", (user_id,))
    u = cur.fetchone()
    cur.close()
    conn.close()
    if u:
        return Usuario(u['id'], u['nombre'], u['correo'], u['nequi'])
    return None

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    if DATABASE_URL:
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            # Tabla Usuarios
            cur.execute('''
                CREATE TABLE IF NOT EXISTS usuarios (
                    id SERIAL PRIMARY KEY,
                    nombre VARCHAR(120) NOT NULL,
                    correo VARCHAR(120) UNIQUE NOT NULL,
                    nequi VARCHAR(20) NOT NULL,
                    password VARCHAR(255) NOT NULL,
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
            # Tabla Transacciones Vinculada
            cur.execute('''
                CREATE TABLE IF NOT EXISTS transacciones (
                    id SERIAL PRIMARY KEY,
                    usuario_id INT REFERENCES usuarios(id) ON DELETE CASCADE,
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
            print("✅ Tablas inicializadas correctamente.")
        except Exception as e:
            print(f"❌ Error al inicializar BD: {e}")

init_db()

# ==========================================
# AUTENTICACIÓN Y REGISTRO
# ==========================================

@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        correo = request.form.get('correo').lower().strip()
        nequi = request.form.get('nequi').strip()
        password = request.form.get('password')

        hash_password = generate_password_hash(password)

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO usuarios (nombre, correo, nequi, password) VALUES (%s, %s, %s, %s) RETURNING id;",
                (nombre, correo, nequi, hash_password)
            )
            user_id = cur.fetchone()[0]
            conn.commit()
            cur.close()
            conn.close()

            usuario = Usuario(user_id, nombre, correo, nequi)
            login_user(usuario)
            return redirect(url_for('tendero'))
        except psycopg2.IntegrityError:
            conn.rollback()
            cur.close()
            conn.close()
            flash('El correo electrónico ya está registrado.')
            return redirect(url_for('registro'))

    return render_template('registro.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        correo = request.form.get('correo').lower().strip()
        password = request.form.get('password')

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM usuarios WHERE correo = %s;", (correo,))
        u = cur.fetchone()
        cur.close()
        conn.close()

        if u and check_password_hash(u['password'], password):
            usuario = Usuario(u['id'], u['nombre'], u['correo'], u['nequi'])
            login_user(usuario)
            return redirect(url_for('tendero'))
        else:
            flash('Correo o contraseña incorrectos.')
            return redirect(url_for('login'))

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# ==========================================
# RUTAS DE INTERFAZ
# ==========================================

@app.route('/')
def checkout():
    return render_template('index.html')

@app.route('/tendero')
@login_required
def tendero():
    return render_template('tendero.html', usuario=current_user)

# ==========================================
# API Y ENDPOINTS
# ==========================================

@app.route('/api/transacciones', methods=['GET', 'POST'])
@login_required
def gestionar_transacciones():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if request.method == 'POST':
        data = request.get_json() or {}
        celular = data.get('celular', 'Anonimo')
        monto = data.get('monto', 0)
        referencia = data.get('referencia', 'REF-PENDIENTE')
        
        cur.execute(
            "INSERT INTO transacciones (usuario_id, celular, monto, referencia, estado) VALUES (%s, %s, %s, %s, 'PENDIENTE') RETURNING id;",
            (current_user.id, celular, monto, referencia)
        )
        conn.commit()
        nueva_id = cur.fetchone()['id']
        cur.close()
        conn.close()
        return jsonify({'exito': True, 'id': nueva_id}), 201

    cur.execute("SELECT id, celular, monto, referencia, estado, fecha FROM transacciones WHERE usuario_id = %s ORDER BY id DESC LIMIT 50;", (current_user.id,))
    filas = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(filas), 200

@app.route('/api/resumen-hoy', methods=['GET'])
@login_required
def resumen_hoy():
    zona_colombia = timezone(timedelta(hours=-5))
    hoy_inicio = datetime.now(zona_colombia).replace(hour=0, minute=0, second=0, microsecond=0)
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT SUM(monto) as total_ventas, COUNT(id) as total_tx FROM transacciones WHERE usuario_id = %s AND estado = 'APROBADO' AND fecha >= %s;",
        (current_user.id, hoy_inicio)
    )
    res = cur.fetchone()
    cur.close()
    conn.close()

    return jsonify({
        'total_ventas': res['total_ventas'] or 0,
        'total_tx': res['total_tx'] or 0
    }), 200

@app.route('/api/exportar-excel', methods=['GET'])
@login_required
def exportar_excel():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, celular, monto, referencia, estado, fecha FROM transacciones WHERE usuario_id = %s ORDER BY id DESC;", (current_user.id,))
    filas = cur.fetchall()
    cur.close()
    conn.close()

    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(['ID', 'Banco / Remitente', 'Monto (COP)', 'Referencia', 'Estado', 'Fecha'])

    for f in filas:
        writer.writerow([
            f['id'], f['celular'], f['monto'], f['referencia'], f['estado'],
            f['fecha'].strftime("%Y-%m-%d %H:%M:%S") if f['fecha'] else ''
        ])

    return Response(
        si.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename=ventas_{current_user.nequi}.csv"}
    )

@app.route('/api/webhook-notificacion', methods=['POST'])
def webhook_notificacion():
    data = request.get_json() or {}
    texto = data.get('texto', '') or data.get('mensaje', '')
    
    if not texto:
        return jsonify({'status': 'ignorado'}), 400

    texto_lower = texto.lower()
    palabras_clave = ["enviaron", "recibiste", "transfirió", "pago", "bre-b", "transfiya", "aceptaste"]
    
    if any(p in texto_lower for p in palabras_clave):
        monto_match = re.search(r'\$\s?([\d\.,]+)', texto)
        monto_str = monto_match.group(1) if monto_match else "0"
        try:
            monto_limpio = int(re.sub(r'[^\d]', '', monto_str))
        except ValueError:
            monto_limpio = 0

        remitente = "Nequi Directo"
        if "bancolombia" in texto_lower: remitente = "Bancolombia"
        elif "daviplata" in texto_lower: remitente = "Daviplata"
        elif "transfiya" in texto_lower: remitente = "Transfiya"
        elif "bre-b" in texto_lower: remitente = "Bre-B (Interbancario)"
        elif "qr" in texto_lower: remitente = "Pago QR Nequi"

        zona_colombia = timezone(timedelta(hours=-5))
        hora_actual = datetime.now(zona_colombia).strftime("%I:%M %p")
        referencia_completa = f"PUSH-{int(datetime.now().timestamp())} • {hora_actual}"

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Asignar la transacción al último usuario registrado activo (o mapeado por Nequi)
        cur.execute("SELECT id FROM usuarios ORDER BY id DESC LIMIT 1;")
        user = cur.fetchone()
        user_id = user['id'] if user else 1

        cur.execute("SELECT * FROM transacciones WHERE estado = 'PENDIENTE' AND usuario_id = %s ORDER BY id DESC LIMIT 1;", (user_id,))
        pago_pendiente = cur.fetchone()

        if pago_pendiente:
            cur.execute(
                "UPDATE transacciones SET estado = 'APROBADO', celular = %s, monto = %s WHERE id = %s;",
                (remitente, monto_limpio if monto_limpio > 0 else pago_pendiente['monto'], pago_pendiente['id'])
            )
        else:
            cur.execute(
                "INSERT INTO transacciones (usuario_id, celular, monto, referencia, estado) VALUES (%s, %s, %s, %s, 'APROBADO');",
                (user_id, remitente, monto_limpio, referencia_completa)
            )

        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'status': 'exito'}), 200

    return jsonify({'status': 'ignorado'}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
