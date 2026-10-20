import ipaddress
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import deque
from datetime import timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

from providers import Providers, deliver_one
from storage import DEFAULT_PREFERENCES, Store, validate_reading

ROOT = Path(__file__).resolve().parent
EMAIL = re.compile(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+')
PHONE = re.compile(r'\+[1-9]\d{6,14}')


def create_app(data_dir=None, worker=True, transport=None):
    data_dir = Path(data_dir or os.environ.get('CLIMAGUARD_DATA_DIR', str(ROOT)))
    data_dir.mkdir(parents=True, exist_ok=True)

    def secret(name):
        path = data_dir / name
        if not path.exists():
            try:
                with path.open('x', encoding='utf-8') as file:
                    file.write(secrets.token_urlsafe(48))
            except FileExistsError:
                pass
        return path.read_text(encoding='utf-8').strip()

    app = Flask(__name__, static_folder=None)
    app.config.update(SECRET_KEY=secret('.session-secret'), MAX_CONTENT_LENGTH=32768,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
    device_token = secret('.device-token')
    store = Store(data_dir / 'climaguard.sqlite3')
    providers = Providers(data_dir / '.providers.json', transport)
    app.extensions.update(climaguard_store=store, climaguard_providers=providers)
    attempts, attempts_lock = {}, threading.Lock()

    # Migrate old real readings without evaluating notifications for old data.
    legacy = data_dir / 'readings.jsonl'
    with store.connect() as db:
        if legacy.exists() and not db.execute("SELECT 1 FROM readings WHERE source='real' LIMIT 1").fetchone():
            with legacy.open(encoding='utf-8') as file:
                lines = deque(file, maxlen=720)
            for line in lines:
                try:
                    original = json.loads(line)
                    row = validate_reading(original)
                    timestamp = float(original['received_at'])
                    if not math.isfinite(timestamp):
                        continue
                    db.execute("INSERT INTO readings(source,user_id,temperature,humidity,energy,received_at) VALUES('real',0,?,?,?,?)",
                               (row['temperature'], row['humidity'], row['energy'], timestamp))
                except (ValueError, KeyError, TypeError):
                    continue

    def local_request():
        try:
            return ipaddress.ip_address(request.remote_addr or '').is_loopback
        except ValueError:
            return False

    def csrf():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_urlsafe(32)
        return session['csrf']

    @app.before_request
    def protect():
        g.user = store.user(session['user_id']) if session.get('user_id') else None
        # Only the ESP ingestion endpoint is reachable from the hotspot.
        if request.path not in ('/api/readings', '/health') and not local_request():
            return jsonify(error='Abra o painel no próprio notebook: http://127.0.0.1:5000.'), 403
        if request.method in ('POST', 'PUT', 'DELETE', 'PATCH') and request.path != '/api/readings':
            expected = session.get('csrf', '')
            given = request.headers.get('X-CSRF-Token', '')
            if not expected or not secrets.compare_digest(given, expected):
                return jsonify(error='Atualize a página e tente novamente.'), 403
            origin = request.headers.get('Origin')
            if origin and origin != request.host_url.rstrip('/'):
                return jsonify(error='Origem da solicitação inválida.'), 403

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error='Os dados enviados são muito grandes.'), 413

    def logged_in(function):
        @wraps(function)
        def decorated(*args, **kwargs):
            if not g.user:
                return jsonify(error='Entre na sua conta para continuar.'), 401
            return function(*args, **kwargs)
        return decorated

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('Preencha os campos do formulário.')
        return data

    def limited(key, limit=8, window=300):
        now = time.time()
        with attempts_lock:
            if len(attempts) > 4096:
                attempts.clear()
            items = [t for t in attempts.get(key, []) if now - t < window]
            if len(items) >= limit:
                return True
            attempts[key] = items + [now]
        return False

    def signed_in(user_id):
        session.clear()
        session['user_id'] = user_id
        session.permanent = True
        return jsonify(user=store.user(user_id), csrf_token=csrf())

    @app.get('/')
    def home():
        return send_from_directory(ROOT, 'index.html')

    @app.get('/assets/<path:filename>')
    def assets(filename):
        return send_from_directory(ROOT / 'web_assets', filename)

    @app.get('/health')
    def health():
        return jsonify(ok=True, app='ClimaGuard', version=2)

    @app.get('/api/session')
    def current_session():
        return jsonify(user=g.user, csrf_token=csrf())

    @app.post('/api/register')
    def register():
        if limited(('signup', request.remote_addr), 10, 3600):
            return jsonify(error='Muitas tentativas de cadastro. Tente novamente mais tarde.'), 429
        try:
            data = body()
            name = str(data.get('name', '')).strip()
            email = str(data.get('email', '')).strip().lower()
            password = data.get('password', '')
            if not 2 <= len(name) <= 80 or not EMAIL.fullmatch(email) or len(email) > 254:
                raise ValueError('Informe nome e e-mail válidos.')
            if not isinstance(password, str) or not 8 <= len(password) <= 128:
                raise ValueError('Use uma senha de 8 a 128 caracteres.')
            hashed = generate_password_hash(password)
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                admin = not db.execute('SELECT 1 FROM users LIMIT 1').fetchone()
                cursor = db.execute('INSERT INTO users(name,email,password,admin,created) VALUES(?,?,?,?,?)', (name, email, hashed, int(admin), time.time()))
                user_id = cursor.lastrowid
                prefs = {**DEFAULT_PREFERENCES, 'email': email}
                db.execute('INSERT INTO preferences VALUES(?,?)', (user_id, json.dumps(prefs)))
            return signed_in(user_id)
        except sqlite3.IntegrityError:
            return jsonify(error='Este e-mail já está cadastrado. Use a opção Entrar.'), 409
        except ValueError as error:
            return jsonify(error=str(error)), 400

    @app.post('/api/login')
    def login():
        try:
            data = body()
            email, password = str(data.get('email', '')).strip().lower(), data.get('password', '')
            if limited(('login', request.remote_addr, email)):
                return jsonify(error='Muitas tentativas. Aguarde cinco minutos.'), 429
            if not isinstance(password, str) or len(password) > 128:
                return jsonify(error='E-mail ou senha incorretos.'), 401
            with store.connect() as db:
                user = db.execute('SELECT id,password FROM users WHERE email=?', (email,)).fetchone()
            if not user or not check_password_hash(user['password'], password):
                return jsonify(error='E-mail ou senha incorretos.'), 401
            return signed_in(user['id'])
        except ValueError as error:
            return jsonify(error=str(error)), 400

    @app.post('/api/logout')
    def logout():
        session.clear()
        return jsonify(ok=True, csrf_token=csrf())

    @app.get('/api/preferences')
    @logged_in
    def get_preferences():
        return jsonify(preferences=store.preferences(g.user['id']), providers=providers.summary())

    @app.post('/api/preferences')
    @logged_in
    def save_preferences():
        try:
            data = body()
            prefs = {}
            for key in ('enabled', 'temperature_enabled', 'power_enabled', 'consent'):
                if type(data.get(key)) is not bool:
                    raise ValueError('Revise as opções de alerta.')
                prefs[key] = data[key]
            threshold, cooldown = data.get('threshold'), data.get('cooldown_minutes')
            if type(threshold) not in (float, int) or not math.isfinite(threshold) or not -40 <= threshold <= 80:
                raise ValueError('Escolha uma temperatura entre -40 e 80 °C.')
            if type(cooldown) is not int or not 1 <= cooldown <= 1440:
                raise ValueError('Escolha um intervalo entre 1 e 1440 minutos.')
            channels = data.get('channels')
            if not isinstance(channels, list) or any(c not in ('email', 'sms', 'whatsapp') for c in channels) or len(channels) > 3:
                raise ValueError('Escolha canais válidos.')
            channels = list(dict.fromkeys(channels))
            if prefs['enabled'] and (not channels or not (prefs['temperature_enabled'] or prefs['power_enabled'])):
                raise ValueError('Ative pelo menos uma condição e um canal.')
            email, phone = str(data.get('email', '')).strip().lower(), str(data.get('phone', '')).strip()
            if email and (not EMAIL.fullmatch(email) or len(email) > 254):
                raise ValueError('Informe um e-mail válido.')
            if phone and not PHONE.fullmatch(phone):
                raise ValueError('Use telefone com +, código do país, DDD e número. Exemplo de formato: +5511999999999.')
            if prefs['enabled'] and (('email' in channels and not email) or (any(c in channels for c in ('sms', 'whatsapp')) and not phone)):
                raise ValueError('Preencha o destinatário de cada canal selecionado.')
            prefs.update(threshold=float(threshold), cooldown_minutes=cooldown, channels=channels, email=email, phone=phone)
            store.save_preferences(g.user['id'], prefs)
            return jsonify(ok=True, preferences=prefs)
        except ValueError as error:
            return jsonify(error=str(error)), 400

    @app.get('/api/status')
    @logged_in
    def status():
        source = request.args.get('source', 'demo')
        if source not in ('real', 'demo'):
            return jsonify(error='Modo inválido.'), 400
        result = store.snapshot(g.user['id'], source)
        result.update(preferences=store.preferences(g.user['id']), providers=providers.summary())
        return jsonify(result)

    @app.post('/api/readings')
    def ingest():
        if not secrets.compare_digest(request.headers.get('X-Device-Token', ''), device_token):
            return jsonify(error='unauthorized'), 401
        try:
            row = validate_reading(request.get_json(silent=True))
            store.add_reading(row, 'real', 0, providers)
            return jsonify(ok=True)
        except ValueError as error:
            return jsonify(error=str(error)), 400

    @app.post('/api/demo')
    @logged_in
    def demo():
        try:
            row = validate_reading(body())
            reading = store.add_reading(row, 'demo', g.user['id'], providers)
            return jsonify(ok=True, reading=reading)
        except ValueError as error:
            return jsonify(error=str(error)), 400

    @app.get('/api/alerts')
    @logged_in
    def alert_history():
        return jsonify(alerts=store.alert_history(g.user['id']))

    @app.post('/api/test-message')
    @logged_in
    def test_message():
        try:
            data = body()
            channel = data.get('channel')
            if channel not in ('email', 'sms', 'whatsapp') or data.get('confirm') is not True:
                raise ValueError('Confirme o canal e a autorização para este envio real.')
            prefs = store.preferences(g.user['id'])
            if not prefs['enabled'] or not prefs['consent'] or channel not in prefs['channels']:
                raise ValueError('Salve as preferências com alertas, canal e autorização de envio ativados.')
            if not providers.live_enabled() or not providers.ready(channel):
                raise ValueError('O administrador precisa configurar e ativar os envios reais deste canal.')
            if limited(('test', g.user['id']), 3, 300):
                return jsonify(error='Aguarde cinco minutos antes de enviar mais testes.'), 429
            now = time.time()
            with store.connect() as db:
                job_id = store.queue(db, g.user['id'], 'test', 'test', channel, prefs,
                                     'ClimaGuard: este é um teste de envio solicitado pelo usuário. Nenhuma ocorrência real foi detectada.', providers, now)
            return jsonify(ok=True, id=job_id), 202
        except ValueError as error:
            return jsonify(error=str(error)), 400

    @app.route('/api/providers', methods=['GET', 'POST'])
    @logged_in
    def provider_settings():
        if not g.user['admin']:
            return jsonify(error='Somente o administrador pode configurar os serviços.'), 403
        if request.method == 'POST':
            try:
                providers.save(body())
                with store.connect() as db:
                    db.execute("DELETE FROM rule_state WHERE source='real'")
            except ValueError as error:
                return jsonify(error=str(error)), 400
        return jsonify(providers=providers.summary(admin=True))

    if worker:
        def process_jobs():
            while True:
                try:
                    if not deliver_one(store, providers):
                        time.sleep(2)
                except Exception:
                    app.logger.error('Falha temporária no processamento da fila de alertas.')
                    time.sleep(2)
        threading.Thread(target=process_jobs, daemon=True, name='climaguard-alerts').start()
    return app
