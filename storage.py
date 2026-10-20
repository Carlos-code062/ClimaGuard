"""SQLite persistence and alert decisions, independent of the hardware."""
import json
import math
import sqlite3
import time
from contextlib import contextmanager

DEFAULT_PREFERENCES = {
    'enabled': False, 'temperature_enabled': True, 'threshold': 30.0,
    'power_enabled': True, 'channels': ['email'], 'email': '', 'phone': '',
    'cooldown_minutes': 10, 'consent': False,
}


class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users(
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE, password TEXT NOT NULL,
                    admin INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS preferences(
                    user_id INTEGER PRIMARY KEY REFERENCES users(id), data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS readings(
                    id INTEGER PRIMARY KEY, source TEXT NOT NULL, user_id INTEGER NOT NULL,
                    temperature REAL, humidity REAL, energy INTEGER, received_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS readings_lookup ON readings(source,user_id,id);
                CREATE TABLE IF NOT EXISTS rule_state(
                    user_id INTEGER, source TEXT, kind TEXT, active INTEGER NOT NULL,
                    last_sent REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(user_id,source,kind));
                CREATE TABLE IF NOT EXISTS alerts(
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, source TEXT NOT NULL,
                    kind TEXT NOT NULL, channel TEXT NOT NULL, destination TEXT NOT NULL,
                    message TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL, provider_id TEXT);
            ''')
            # A crash after submission must not automatically resend a message.
            db.execute("UPDATE alerts SET status='failed', detail='Envio interrompido. Confira o serviço antes de repetir.', updated=? WHERE status='sending'", (time.time(),))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def user(self, user_id):
        with self.connect() as db:
            row = db.execute('SELECT id,name,email,admin FROM users WHERE id=?', (user_id,)).fetchone()
            return dict(row) if row else None

    def preferences(self, user_id, db=None):
        if db is None:
            with self.connect() as connection:
                return self.preferences(user_id, connection)
        row = db.execute('SELECT data FROM preferences WHERE user_id=?', (user_id,)).fetchone()
        return {**DEFAULT_PREFERENCES, **(json.loads(row['data']) if row else {})}

    def save_preferences(self, user_id, data):
        with self.connect() as db:
            db.execute('INSERT INTO preferences VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data', (user_id, json.dumps(data)))
            db.execute('DELETE FROM rule_state WHERE user_id=?', (user_id,))

    def add_reading(self, row, source, user_id, provider):
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO readings(source,user_id,temperature,humidity,energy,received_at) VALUES(?,?,?,?,?,?)',
                       (source, user_id, row['temperature'], row['humidity'], row['energy'], now))
            # Keep bounded history per stream; alerts have their own history.
            db.execute('DELETE FROM readings WHERE source=? AND user_id=? AND id NOT IN (SELECT id FROM readings WHERE source=? AND user_id=? ORDER BY id DESC LIMIT 720)', (source, user_id, source, user_id))
            if source == 'real':
                users = db.execute('SELECT id FROM users').fetchall()
            else:
                users = [{'id': user_id}]
            for user in users:
                self.evaluate(db, user['id'], row, source, now, provider)
        return {**row, 'received_at': now, 'source': source}

    def evaluate(self, db, user_id, row, source, now, provider):
        prefs = self.preferences(user_id, db)
        if not prefs['enabled']:
            return
        for kind, enabled in [('temperature', prefs['temperature_enabled']), ('power', prefs['power_enabled'])]:
            if not enabled:
                continue
            old = db.execute('SELECT * FROM rule_state WHERE user_id=? AND source=? AND kind=?', (user_id, source, kind)).fetchone()
            active, last_sent = (bool(old['active']), old['last_sent']) if old else (False, 0)
            value = row['temperature'] if kind == 'temperature' else row['energy']
            if value is None:
                continue  # An unavailable sensor is not a recovered condition.
            if kind == 'temperature':
                trigger = value > prefs['threshold']
                recovered = value <= prefs['threshold'] - 1
                message = f"ClimaGuard: temperatura de {value:.1f} °C acima do limite de {prefs['threshold']:g} °C."
            else:
                trigger, recovered = value is False, value is True
                message = 'ClimaGuard: falta de energia simulada detectada pela chave em D5.'
            should_notify = trigger and not active and now - last_sent >= prefs['cooldown_minutes'] * 60
            if trigger:
                active = True
            elif recovered:
                active = False
            if should_notify:
                for channel in prefs['channels']:
                    self.queue(db, user_id, source, kind, channel, prefs, message, provider, now)
                last_sent = now
            db.execute('INSERT INTO rule_state VALUES(?,?,?,?,?) ON CONFLICT(user_id,source,kind) DO UPDATE SET active=excluded.active,last_sent=excluded.last_sent', (user_id, source, kind, int(active), last_sent))

    def queue(self, db, user_id, source, kind, channel, prefs, message, provider, now):
        destination = prefs['email'] if channel == 'email' else prefs['phone']
        if source == 'demo':
            status, detail = 'simulated', 'Demonstração: registrado no histórico, sem envio externo.'
        elif not prefs['consent']:
            status, detail = 'blocked', 'É necessário autorizar o envio nas preferências.'
        elif not provider.live_enabled():
            status, detail = 'blocked', 'Envios reais desativados pelo administrador.'
        elif not provider.ready(channel):
            status, detail = 'blocked', 'O serviço deste canal ainda não foi configurado.'
        else:
            status, detail = 'queued', 'Aguardando envio ao serviço.'
        cursor = db.execute('INSERT INTO alerts(user_id,source,kind,channel,destination,message,status,detail,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                            (user_id, source, kind, channel, destination, message, status, detail, now, now))
        return cursor.lastrowid

    def snapshot(self, user_id, source):
        owner = 0 if source == 'real' else user_id
        with self.connect() as db:
            rows = db.execute('SELECT temperature,humidity,energy,received_at,source FROM readings WHERE source=? AND user_id=? ORDER BY id DESC LIMIT 120', (source, owner)).fetchall()
            real = db.execute("SELECT received_at FROM readings WHERE source='real' ORDER BY id DESC LIMIT 1").fetchone()
        history = []
        for item in reversed(rows):
            row = dict(item)
            row['energy'] = None if row['energy'] is None else bool(row['energy'])
            history.append(row)
        latest = history[-1] if history else None
        return dict(latest=latest, history=history, source=source,
                    online=bool(latest and (source == 'demo' or time.time() - latest['received_at'] < 20)),
                    device_online=bool(real and time.time() - real['received_at'] < 20), switch_enabled=True)

    def alert_history(self, user_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM alerts WHERE user_id=? ORDER BY id DESC LIMIT 100', (user_id,))]

    def claim_job(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM alerts WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE alerts SET status='sending',updated=? WHERE id=?", (time.time(), row['id']))
                return dict(row)
        return None

    def finish_job(self, job_id, status, detail, provider_id=None):
        with self.connect() as db:
            db.execute('UPDATE alerts SET status=?,detail=?,provider_id=?,updated=? WHERE id=?', (status, detail, provider_id, time.time(), job_id))


def validate_reading(data):
    if not isinstance(data, dict):
        raise ValueError('Envie uma leitura válida.')
    t, h, energy = data.get('temperature'), data.get('humidity'), data.get('energy')
    numeric = all(type(v) in (float, int) and math.isfinite(v) for v in (t, h))
    valid = numeric and -40 <= t <= 80 and 0 <= h <= 100
    if energy is not None and type(energy) is not bool:
        raise ValueError('O estado da chave deve ser verdadeiro ou falso.')
    if not valid and not (t is None and h is None and type(energy) is bool):
        raise ValueError('Temperatura ou umidade inválida.')
    return dict(temperature=t, humidity=h, energy=energy)
