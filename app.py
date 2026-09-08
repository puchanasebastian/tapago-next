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
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'tapago-secret-key-2026')

# Configuración de Base de Datos
DATABASE_URL = os.environ.get('DATABASE_URL')

# Configuración de Flask-Mail (Gmail)
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME')

mail = Mail(app)
serializer = URLSafeTimedSerializer(app.secret_key)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class Usuario(UserMixin):
    def __init__(self, id, nombre, correo, nequi, verificado=False):
        self.id = id
        self.nombre = nombre
        self.correo = correo
        self.nequi = nequi
        self.verificado = verificado

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    if not conn: return None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id, nombre, correo, nequi, verificado FROM usuarios WHERE id = %s;", (user_id,))
        u = cur.fetchone()
        cur.close()
        if u:
            return Usuario(u['id'], u['nombre'], u['correo'], u['nequi'], u['verificado'])
        return None
    finally:
        conn.close()

def get_db_connection():
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print(f"❌ Error conectando a BD: {e}")
        return None

def init_db():
    if DATABASE_URL:
        conn = get_db_connection()
        if not conn: return
        try:
            cur = conn.cursor()
            cur.execute('''
                CREATE TABLE IF NOT EXISTS usuarios (
                    id SERIAL PRIMARY KEY,
                    nombre VARCHAR(120) NOT NULL,
                    correo VARCHAR(120) UNIQUE NOT NULL,
                    nequi VARCHAR(20) NOT NULL,
                    password VARCHAR(255) NOT NULL,
                    verificado BOOLEAN DEFAULT FALSE,
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
            # Asegurar que la columna 'verificado' exista si la tabla ya estaba creada
            cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS verificado BOOLEAN DEFAULT FALSE;")
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
        finally:
            conn.close()

init_db()

def enviar_correo_confirmacion(email):
    token = serializer.dumps(email, salt='email-confirm-salt')
    link = url_for('confirmar_email', token=token, _external=True)
    msg = Message('Confirma tu cuenta - TAPAGO POS', recipients=[email])
    msg.html = f'''
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 10px;">
            <h2 style="color: #00d26a;">¡Bienvenido a TAPAGO POS!</h2>
            <p>Gracias por registrarte. Para activar tu cuenta y empezar a recibir pagos, haz clic en el siguiente botón:</p>
            <p style="text-align: center; margin: 30px 0;">
                <a href="{link}" style="background-color: #00d26a; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold;">Confirmar Mi Correo</a>
            </p>
            <p style="font-size: 12px; color: #777;">Este enlace expirará en 1 hora. Si no creaste esta cuenta, puedes ignorar este mensaje.</p>
        </div>
    '''
    mail.send(msg)

@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        correo = request.form.get('correo').lower().strip()
        nequi = request.form.get('nequi').strip()
        password = request.form.get('password')
        hash_password = generate_password_hash(password)

        conn = get_db_connection()
        if not conn:
            flash('Error de conexión a la base de datos.')
            return redirect(url_for('registro'))
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO usuarios (nombre, correo, nequi, password, verificado) VALUES (%s, %s, %s, %s, FALSE) RETURNING id;",
                (nombre, correo, nequi, hash_password)
            )
            conn.commit()
            cur.close()

            # Enviar correo de confirmación
            try:
                enviar_correo_confirmacion(correo)
                flash('Registro exitoso. Te hemos enviado un correo de activación. Por favor revisa tu bandeja de entrada o spam.', 'info')
            except Exception as e:
                print(f"❌ Error enviando correo: {e}")
                flash('Usuario creado, pero hubo un problema al enviar el correo de activación.', 'warning')

            return redirect(url_for('login'))
        except psycopg2.IntegrityError:
            conn.rollback()
            flash('El correo electrónico ya está registrado.')
            return redirect(url_for('registro'))
        finally:
            conn.close()

    return render_template('registro.html')

@app.route('/confirmar-email/<token>')
def confirmar_email(token):
    try:
        email = serializer.loads(token, salt='email-confirm-salt', max_age=3600) # Expira en 1 hora
    except SignatureExpired:
        flash('El enlace de confirmación ha expirado. Solicita un nuevo registro o verificación.', 'danger')
        return redirect(url_for('login'))
    except BadTimeSignature:
        flash('El enlace de confirmación no es válido.', 'danger')
        return redirect(url_for('login'))

    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("UPDATE usuarios SET verificado = TRUE WHERE correo = %s;", (email,))
            conn.commit()
            cur.close()
            flash('¡Tu cuenta ha sido activada correctamente! Ya puedes iniciar sesión.', 'success')
        finally:
            conn.close()
            
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        correo = request.form.get('correo').lower().strip()
        password = request.form.get('password')

        conn = get_db_connection()
        if not conn:
            flash('Error de conexión a la base de datos.')
            return redirect(url_for('login'))
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT * FROM usuarios WHERE correo = %s;", (correo,))
            u = cur.fetchone()
            cur.close()

            if u and check_password_hash(u['password'], password):
                if not u.get('verificado', False):
                    flash('Debes activar tu cuenta desde el correo de confirmación enviado a tu email antes de ingresar.', 'warning')
                    return redirect(url_for('login'))

                usuario = Usuario(u['id'], u['nombre'], u['correo'], u['nequi'], u['verificado'])
                login_user(usuario)
                return redirect(url_for('tendero'))
            else:
                flash('Correo o contraseña incorrectos.')
                return redirect(url_for('login'))
        finally:
            conn.close()

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
def checkout():
    return render_template('index.html')

@app.route('/tendero')
@login_required
def tendero():
    return render_template('tendero.html', usuario=current_user)

@app.route('/api/transacciones', methods=['GET', 'POST'])
@login_required
def gestionar_transacciones():
    conn = get_db_connection()
    if not conn: return jsonify([]), 500
    try:
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
            return jsonify({'exito': True, 'id': nueva_id}), 201

        cur.execute("SELECT id, celular, monto, referencia, estado, fecha FROM transacciones WHERE usuario_id = %s ORDER BY id DESC LIMIT 50;", (current_user.id,))
        filas = cur.fetchall()
        cur.close()
        return jsonify(filas), 200
    finally:
        conn.close()

@app.route('/api/resumen-hoy', methods=['GET'])
@login_required
def resumen_hoy():
    zona_colombia = timezone(timedelta(hours=-5))
    hoy_inicio = datetime.now(zona_colombia).replace(hour=0, minute=0, second=0, microsecond=0)
    
    conn = get_db_connection()
    if not conn: return jsonify({'total_ventas': 0, 'total_tx': 0}), 500
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT SUM(monto) as total_ventas, COUNT(id) as total_tx FROM transacciones WHERE usuario_id = %s AND estado = 'APROBADO' AND fecha >= %s;",
            (current_user.id, hoy_inicio)
        )
        res = cur.fetchone()
        cur.close()

        return jsonify({
            'total_ventas': res['total_ventas'] or 0,
            'total_tx': res['total_tx'] or 0
        }), 200
    finally:
        conn.close()

@app.route('/api/exportar-excel', methods=['GET'])
@login_required
def exportar_excel():
    conn = get_db_connection()
    if not conn: return "Error de BD", 500
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id, celular, monto, referencia, estado, fecha FROM transacciones WHERE usuario_id = %s ORDER BY id DESC;", (current_user.id,))
        filas = cur.fetchall()
        cur.close()

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
    finally:
        conn.close()

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
        if not conn: return jsonify({'status': 'error bd'}), 500
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)

            cur.execute("SELECT id FROM usuarios WHERE verificado = TRUE ORDER BY id DESC LIMIT 1;")
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
            return jsonify({'status': 'exito'}), 200
        finally:
            conn.close()

    return jsonify({'status': 'ignorado'}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
