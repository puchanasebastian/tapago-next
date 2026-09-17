import os
import re
import csv
import threading
import traceback
from io import StringIO
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, jsonify, Response, redirect, url_for, flash
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature
from authlib.integrations.flask_client import OAuth
import resend

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'tapago-secret-key-2026')

# Permitir transporte HTTP/HTTPS inseguro temporalmente para librerías OAuth si aplica
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# Configuración de Base de Datos
DATABASE_URL = os.environ.get('DATABASE_URL')

# Configuración de Resend API Key
resend.api_key = os.environ.get('RESEND_API_KEY')

serializer = URLSafeTimedSerializer(app.secret_key)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Configuración de Google OAuth
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.environ.get('GOOGLE_CLIENT_ID'),
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

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

def enviar_correo_async(email, link):
    try:
        print(f"📧 Intentando enviar correo vía Resend a: {email}...")
        params = {
            "from": "TAPAGO POS <onboarding@resend.dev>",
            "to": [email],
            "subject": "Confirma tu cuenta - TAPAGO POS",
            "html": f'''
                <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 10px;">
                    <h2 style="color: #00d26a;">¡Bienvenido a TAPAGO POS!</h2>
                    <p>Gracias por registrarte. Para activar tu cuenta y empezar a recibir pagos, haz clic en el siguiente botón:</p>
                    <p style="text-align: center; margin: 30px 0;">
                        <a href="{link}" style="background-color: #00d26a; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold;">Confirmar Mi Correo</a>
                    </p>
                    <p style="font-size: 12px; color: #777;">Este enlace expirará en 1 hora. Si no creaste esta cuenta, puedes ignorar este mensaje.</p>
                </div>
            '''
        }
        res = resend.Emails.send(params)
        print(f"✅ Correo enviado exitosamente con Resend. Respuesta: {res}")
    except Exception as e:
        print(f"❌ ERROR CRÍTICO ENVIANDO CON RESEND:")
        print(traceback.format_exc())

def enviar_correo_confirmacion(email):
    token = serializer.dumps(email, salt='email-confirm-salt')
    link = url_for('confirmar_email', token=token, _external=True)
    thread = threading.Thread(target=enviar_correo_async, args=(email, link))
    thread.start()

# --- FUNCIÓN MEJORADA DE EXTRACCIÓN DE NOTIFICACIONES NEQUI / BRE-B / BANCOLOMBIA ---
def procesar_notificacion_nequi(texto):
    if not texto:
        return None

    texto_limpio = " ".join(texto.split())

    # 1. Patrón Nequi a Nequi (Captura nombres como "Juan Aparicio", "Jose Restrepo", etc.)
    nequi_match = re.search(
        r'^(?!Te\s+enviaron)(.+?)\s+te\s+envi[oó]\s+\$([\d\.]+)', 
        texto_limpio, 
        re.IGNORECASE
    )
    if nequi_match:
        remitente_detectado = nequi_match.group(1).strip()
        monto_str = nequi_match.group(2).replace('.', '')
        remitente_clean = re.sub(r'^¡?Te enviaron plata!?\s*', '', remitente_detectado, flags=re.IGNORECASE).strip()

        return {
            "remitente": remitente_clean if remitente_clean else "Usuario Nequi",
            "monto": int(monto_str),
            "canal": "Nequi a Nequi"
        }

    # 2. Patrón Bre-B (Interbancario: Nu, Lulo, Davivienda, etc.)
    breb_match = re.search(r'Te\s+enviaron\s+\$([\d\.]+)', texto_limpio, re.IGNORECASE)
    if breb_match and ("Bre-B" in texto_limpio or "revisa tu saldo" in texto_limpio):
        monto_str = breb_match.group(1).replace('.', '')
        return {
            "remitente": "Bre-B (Interbancario)",
            "monto": int(monto_str),
            "canal": "Bre-B"
        }

    # 3. Patrón Bancolombia
    bancolombia_match = re.search(r'Te\s+enviaron\s+\$([\d\.]+)\s+desde\s+Bancolombia', texto_limpio, re.IGNORECASE)
    if bancolombia_match:
        monto_str = bancolombia_match.group(1).replace('.', '')
        return {
            "remitente": "Bancolombia",
            "monto": int(monto_str),
            "canal": "Bancolombia"
        }

    # 4. Patrón Transfiya
    transfiya_match = re.search(r'de\s+(.+?)\s*$', texto_limpio, re.IGNORECASE)
    monto_transfiya = re.search(r'\$([\d\.]+)', texto_limpio)
    if "Transfiya" in texto_limpio and monto_transfiya:
        monto_str = monto_transfiya.group(1).replace('.', '')
        remitente = transfiya_match.group(1).strip() if transfiya_match else "Transfiya"
        return {
            "remitente": remitente,
            "monto": int(monto_str),
            "canal": "Transfiya"
        }

    # 5. Patrón PSE / Recargas
    pse_match = re.search(r'recarga\s+por\s+\$([\d\.]+)', texto_limpio, re.IGNORECASE)
    if pse_match:
        monto_str = pse_match.group(1).replace('.', '')
        return {
            "remitente": "Recarga PSE",
            "monto": int(monto_str),
            "canal": "PSE"
        }

    # 6. Fallback General
    monto_gen = re.search(r'\$([\d\.]+)', texto_limpio)
    if monto_gen:
        monto_str = monto_gen.group(1).replace('.', '')
        return {
            "remitente": "Cliente Nequi",
            "monto": int(monto_str),
            "canal": "Nequi / General"
        }

    return None

# RUTA PARA LOGUEARSE CON GOOGLE
@app.route('/login/google')
def login_google():
    redirect_uri = url_for('google_authorize', _external=True)
    return google.authorize_redirect(redirect_uri)

# RUTA DE RETORNO TRAS AUTENTICAR CON GOOGLE
@app.route('/login/google/authorized')
def google_authorize():
    try:
        token = google.authorize_access_token()
        user_info = token.get('userinfo')
        if not user_info:
            flash('No se pudo obtener información de Google.', 'danger')
            return redirect(url_for('login'))

        correo = user_info.get('email', '').lower().strip()
        nombre = user_info.get('name', 'Usuario Google')

        conn = get_db_connection()
        if not conn:
            flash('Error de conexión a la base de datos.', 'danger')
            return redirect(url_for('login'))

        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT * FROM usuarios WHERE correo = %s;", (correo,))
            u = cur.fetchone()

            if u:
                cur.execute("UPDATE usuarios SET verificado = TRUE WHERE id = %s;", (u['id'],))
                conn.commit()
                usuario = Usuario(u['id'], u['nombre'], u['correo'], u['nequi'], True)
            else:
                password_dummy = generate_password_hash(os.urandom(16).hex())
                cur.execute(
                    "INSERT INTO usuarios (nombre, correo, nequi, password, verificado) VALUES (%s, %s, %s, %s, TRUE) RETURNING id;",
                    (nombre, correo, "0000000000", password_dummy)
                )
                conn.commit()
                nuevo_id = cur.fetchone()['id']
                usuario = Usuario(nuevo_id, nombre, correo, "0000000000", True)

            cur.close()
            login_user(usuario)
            flash(f'¡Bienvenido {nombre}!', 'success')
            return redirect(url_for('tendero'))

        finally:
            conn.close()

    except Exception as e:
        print(f"❌ Error en Google Login: {e}")
        flash('Ocurrió un error al autenticar con Google.', 'danger')
        return redirect(url_for('login'))

@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        correo = request.form.get('correo', '').lower().strip()
        nequi = request.form.get('nequi', '').strip()
        password = request.form.get('password')
        hash_password = generate_password_hash(password)

        conn = get_db_connection()
        if not conn:
            flash('Error de conexión a la base de datos.', 'danger')
            return redirect(url_for('registro'))
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT id, verificado FROM usuarios WHERE correo = %s;", (correo,))
            usuario_existente = cur.fetchone()

            if usuario_existente:
                if usuario_existente['verificado']:
                    flash('El correo electrónico ya está registrado y verificado. Inicia sesión.', 'warning')
                    cur.close()
                    return redirect(url_for('login'))
                else:
                    cur.execute("DELETE FROM usuarios WHERE id = %s;", (usuario_existente['id'],))
                    conn.commit()

            cur.execute(
                "INSERT INTO usuarios (nombre, correo, nequi, password, verificado) VALUES (%s, %s, %s, %s, FALSE) RETURNING id;",
                (nombre, correo, nequi, hash_password)
            )
            conn.commit()
            cur.close()

            enviar_correo_confirmacion(correo)
            return redirect(url_for('pantalla_espera_verificacion', email=correo))

        except Exception as e:
            conn.rollback()
            print(f"❌ Error en registro: {e}")
            flash('Ocurrió un error al procesar tu registro.', 'danger')
            return redirect(url_for('registro'))
        finally:
            conn.close()

    return render_template('registro.html')

@app.route('/espera-verificacion')
def pantalla_espera_verificacion():
    email = request.args.get('email', '')
    return render_template('espera_verificacion.html', email=email)

@app.route('/reenviar-verificacion', methods=['POST'])
def reenviar_verificacion():
    correo = request.form.get('correo', '').lower().strip()
    if not correo:
        flash('Dirección de correo no válida.', 'danger')
        return redirect(url_for('login'))

    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT verificado FROM usuarios WHERE correo = %s;", (correo,))
            u = cur.fetchone()
            cur.close()

            if u and not u['verificado']:
                enviar_correo_confirmacion(correo)
                flash('¡Correo de activación reenviado! Revisa tu bandeja de entrada o spam.', 'success')
            elif u and u['verificado']:
                flash('Tu cuenta ya está activa. Puedes iniciar sesión.', 'success')
                return redirect(url_for('login'))
            else:
                flash('No se encontró ninguna cuenta pendiente para este correo.', 'warning')
        finally:
            conn.close()

    return redirect(url_for('pantalla_espera_verificacion', email=correo))

@app.route('/confirmar-email/<token>')
def confirmar_email(token):
    try:
        email = serializer.loads(token, salt='email-confirm-salt', max_age=3600)
    except SignatureExpired:
        flash('El enlace de confirmación ha expirado. Regístrate nuevamente.', 'danger')
        return redirect(url_for('registro'))
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
        correo = request.form.get('correo', '').lower().strip()
        password = request.form.get('password')

        conn = get_db_connection()
        if not conn:
            flash('Error de conexión a la base de datos.', 'danger')
            return redirect(url_for('login'))
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT * FROM usuarios WHERE correo = %s;", (correo,))
            u = cur.fetchone()
            cur.close()

            if u and check_password_hash(u['password'], password):
                if not u.get('verificado', False):
                    flash('Debes activar tu cuenta desde el correo antes de ingresar.', 'warning')
                    return redirect(url_for('pantalla_espera_verificacion', email=correo))

                usuario = Usuario(u['id'], u['nombre'], u['correo'], u['nequi'], u['verificado'])
                login_user(usuario)
                return redirect(url_for('tendero'))
            else:
                flash('Correo o contraseña incorrectos.', 'danger')
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

# --- WEBHOOK NOTIFICACIÓN ACTUALIZADO ---
@app.route('/api/webhook-notificacion', methods=['POST'])
def webhook_notificacion():
    data = request.get_json() or {}
    texto = data.get('texto', '') or data.get('mensaje', '')
    
    if not texto:
        return jsonify({'status': 'ignorado'}), 400

    # Usar el procesador de notificaciones inteligente
    resultado = procesar_notificacion_nequi(texto)

    if not resultado:
        return jsonify({'status': 'ignorado', 'reason': 'Formato no reconocido'}), 200

    remitente = resultado['remitente']
    monto_limpio = resultado['monto']

    zona_colombia = timezone(timedelta(hours=-5))
    hora_actual = datetime.now(zona_colombia).strftime("%I:%M %p")
    referencia_completa = f"PUSH-{int(datetime.now().timestamp())} • {hora_actual}"

    conn = get_db_connection()
    if not conn: return jsonify({'status': 'error bd'}), 500
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Buscar un usuario activo o por defecto
        cur.execute("SELECT id FROM usuarios WHERE verificado = TRUE ORDER BY id DESC LIMIT 1;")
        user = cur.fetchone()
        user_id = user['id'] if user else 1

        # Si hay un cobro pendiente, lo actualiza a APROBADO con el nombre/monto
        cur.execute("SELECT * FROM transacciones WHERE estado = 'PENDIENTE' AND usuario_id = %s ORDER BY id DESC LIMIT 1;", (user_id,))
        pago_pendiente = cur.fetchone()

        if pago_pendiente:
            cur.execute(
                "UPDATE transacciones SET estado = 'APROBADO', celular = %s, monto = %s WHERE id = %s;",
                (remitente, monto_limpio if monto_limpio > 0 else pago_pendiente['monto'], pago_pendiente['id'])
            )
        else:
            # Si no había cobro pendiente, registra el pago recibido directamente
            cur.execute(
                "INSERT INTO transacciones (usuario_id, celular, monto, referencia, estado) VALUES (%s, %s, %s, %s, 'APROBADO');",
                (user_id, remitente, monto_limpio, referencia_completa)
            )

        conn.commit()
        cur.close()
        return jsonify({'status': 'exito', 'remitente': remitente, 'monto': monto_limpio}), 200
    finally:
        conn.close()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
