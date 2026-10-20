"""Isolated tests: temporary databases, synthetic users, zero external sends."""
import json
import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs
from urllib.error import HTTPError

from portal import create_app
from providers import deliver_one
from storage import validate_reading


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, _):
        return b'{"id":"test-only-provider-id"}'


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict('os.environ', {}, clear=True)
        self.env.start()
        self.directory = tempfile.TemporaryDirectory()
        self.calls = []

        def fake_transport(req, timeout):
            self.calls.append(req)
            return FakeResponse()

        self.app = create_app(self.directory.name, worker=False, transport=fake_transport)
        self.app.testing = True
        self.client = self.app.test_client()
        self.store = self.app.extensions['climaguard_store']
        self.providers = self.app.extensions['climaguard_providers']
        self.csrf = self.client.get('/api/session').json['csrf_token']
        result = self.post('/api/register', {'name': 'Usuário de teste', 'email': 'user@example.test', 'password': 'local-test-password'})
        self.assertEqual(result.status_code, 200)
        self.csrf = result.json['csrf_token']
        self.user_id = result.json['user']['id']

    def tearDown(self):
        self.directory.cleanup()
        self.env.stop()

    def post(self, path, data):
        return self.client.post(path, json=data, headers={'X-CSRF-Token': self.csrf})

    def preferences(self, **updates):
        data = dict(enabled=True, temperature_enabled=True, power_enabled=True,
                    threshold=30, cooldown_minutes=1, channels=['email'],
                    email='user@example.test', phone='', consent=False)
        data.update(updates)
        response = self.post('/api/preferences', data)
        self.assertEqual(response.status_code, 200, response.json)
        return data

    def reading(self, temperature=28, energy=True):
        return {'temperature': temperature, 'humidity': 50 if temperature is not None else None, 'energy': energy}

    def history(self):
        return self.client.get('/api/alerts').json['alerts']

    def test_login_csrf_and_local_access(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get('/api/status').status_code, 401)
        self.assertEqual(self.client.post('/api/preferences', json={}).status_code, 403)
        self.assertEqual(self.client.get('/api/session', environ_overrides={'REMOTE_ADDR':'192.168.137.10'}).status_code, 403)
        self.assertEqual(self.post('/api/login', {'email':'user@example.test','password':'wrong-password'}).status_code, 401)
        with self.store.connect() as db:
            hashed = db.execute('SELECT password FROM users').fetchone()['password']
        self.assertNotEqual(hashed, 'local-test-password')
        result = self.post('/api/login', {'email':'user@example.test','password':'local-test-password'})
        self.assertEqual(result.status_code, 200)
        self.assertNotEqual(result.json['csrf_token'], self.csrf)

    def test_preferences_persist_and_validate(self):
        self.preferences(threshold=32.5, channels=['sms'], phone='+15555550123')
        self.assertEqual(self.client.get('/api/preferences').json['preferences']['threshold'], 32.5)
        invalid = self.store.preferences(self.user_id)
        invalid['phone'] = 'not-a-phone'
        self.assertEqual(self.post('/api/preferences', invalid).status_code, 400)
        invalid['threshold'] = True
        self.assertEqual(self.post('/api/preferences', invalid).status_code, 400)

    def test_temperature_crossing_and_no_duplicate(self):
        self.preferences()
        self.post('/api/demo', self.reading(30))
        self.assertEqual(len(self.history()), 0)
        self.post('/api/demo', self.reading(30.1))
        self.post('/api/demo', self.reading(35))
        self.assertEqual(len(self.history()), 1)
        self.assertEqual(self.history()[0]['status'], 'simulated')
        self.assertEqual(self.calls, [])
        # Recover with hysteresis; then permit a new crossing after cooldown.
        self.post('/api/demo', self.reading(29))
        with self.store.connect() as db:
            db.execute("UPDATE rule_state SET last_sent=? WHERE kind='temperature'", (time.time()-61,))
        self.post('/api/demo', self.reading(31))
        self.assertEqual(len(self.history()), 2)

    def test_power_alert_works_without_temperature(self):
        self.preferences(channels=['email','sms','whatsapp'], phone='+15555550123')
        self.post('/api/demo', self.reading(None, False))
        self.assertEqual(len(self.history()), 3)
        self.assertTrue(all(row['kind']=='power' and row['status']=='simulated' for row in self.history()))
        latest = self.client.get('/api/status?source=demo').json['latest']
        self.assertIs(latest['temperature'], None)
        self.assertIs(latest['energy'], False)
        self.assertIsNone(self.client.get('/api/status?source=real').json['latest'])

    def test_user_data_isolation_and_admin(self):
        self.preferences()
        self.post('/api/demo', self.reading(32))
        other = self.app.test_client()
        token = other.get('/api/session').json['csrf_token']
        created = other.post('/api/register', json={'name':'Outro teste','email':'other@example.test','password':'other-test-password'}, headers={'X-CSRF-Token':token}).json
        self.assertFalse(created['user']['admin'])
        self.assertEqual(other.get('/api/alerts').json['alerts'], [])
        self.assertIsNone(other.get('/api/status?source=demo').json['latest'])
        self.assertEqual(other.get('/api/providers').status_code, 403)

    def test_device_token_and_real_readings(self):
        row = self.reading(27, False)
        self.assertEqual(self.client.post('/api/readings', json=row).status_code, 401)
        token = (Path(self.directory.name)/'.device-token').read_text()
        self.preferences()
        result = self.client.post('/api/readings', json=row, headers={'X-Device-Token':token}, environ_overrides={'REMOTE_ADDR':'192.168.137.24'})
        self.assertEqual(result.status_code, 200)
        snapshot = self.client.get('/api/status?source=real').json
        self.assertTrue(snapshot['online'])
        self.assertEqual(self.history()[0]['status'], 'blocked')
        self.assertFalse(deliver_one(self.store, self.providers))
        self.assertEqual(self.calls, [])

    def test_disabled_rules_do_not_create_notifications(self):
        self.preferences(enabled=False)
        self.post('/api/demo', self.reading(35, False))
        self.assertEqual(self.history(), [])

    def test_simulation_never_sends_even_when_live_enabled(self):
        self.preferences(consent=True)
        self.providers.save({'RESEND_API_KEY':'test-only-not-a-real-key','RESEND_FROM':'alerts@example.test','live_enabled':True})
        self.post('/api/demo', self.reading(35, False))
        self.assertEqual(len(self.history()), 2)
        self.assertFalse(deliver_one(self.store, self.providers))
        self.assertEqual(self.calls, [])

    def test_real_test_requires_confirmation_and_consent(self):
        self.preferences()
        self.assertEqual(self.post('/api/test-message', {'channel':'email','confirm':True}).status_code, 400)
        self.preferences(consent=True)
        self.providers.save({'RESEND_API_KEY':'test-only-not-a-real-key','RESEND_FROM':'alerts@example.test','live_enabled':True})
        self.assertEqual(self.post('/api/test-message', {'channel':'email'}).status_code, 400)
        self.assertEqual(self.post('/api/test-message', {'channel':'email','confirm':True}).status_code, 202)
        self.assertTrue(deliver_one(self.store, self.providers))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0].full_url, 'https://api.resend.com/emails')
        self.assertEqual(self.calls[0].get_header('User-agent'), 'ClimaGuard/2.0')
        self.assertEqual(self.calls[0].get_header('Accept'), 'application/json')
        self.assertEqual(json.loads(self.calls[0].data)['to'], ['user@example.test'])
        self.assertEqual(self.history()[0]['status'], 'accepted')

    def test_disabling_preferences_cancels_queued_job(self):
        self.preferences(consent=True)
        self.providers.save({'RESEND_API_KEY':'test-only-not-a-real-key','RESEND_FROM':'alerts@example.test','live_enabled':True})
        self.post('/api/test-message', {'channel':'email','confirm':True})
        self.preferences(enabled=False)
        self.assertTrue(deliver_one(self.store, self.providers))
        self.assertEqual(self.history()[0]['status'], 'blocked')
        self.assertEqual(self.calls, [])

    def test_twilio_whatsapp_template_request(self):
        self.preferences(consent=True, channels=['whatsapp'], phone='+15555550123')
        self.providers.save({'TWILIO_ACCOUNT_SID':'AC'+'0'*32,'TWILIO_AUTH_TOKEN':'test-only-token',
                             'TWILIO_WHATSAPP_FROM':'+15555550124','TWILIO_WHATSAPP_CONTENT_SID':'HX'+'0'*32,'live_enabled':True})
        self.post('/api/test-message', {'channel':'whatsapp','confirm':True})
        self.assertTrue(deliver_one(self.store, self.providers))
        request = parse_qs(self.calls[0].data.decode())
        self.assertEqual(request['To'], ['whatsapp:+15555550123'])
        self.assertIn('ContentVariables', request)
        self.assertNotIn('Body', request)

    def test_invalid_sensor_data(self):
        for row in ({'temperature':True,'humidity':50,'energy':True},
                    {'temperature':90,'humidity':50,'energy':True},
                    {'temperature':25,'humidity':float('nan'),'energy':False},
                    {'temperature':None,'humidity':50,'energy':False}):
            with self.assertRaises(ValueError):
                validate_reading(row)
        self.assertEqual(validate_reading(self.reading(None, False))['energy'], False)

    def test_secret_files_are_not_served(self):
        for path in ('/.device-token','/.providers.json','/.session-secret','/climaguard.sqlite3','/assets/../.device-token'):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_resend_error_explains_allowed_test_recipient(self):
        self.providers.save({'RESEND_API_KEY':'test-only-not-a-real-key','RESEND_FROM':'onboarding@resend.dev','live_enabled':True})
        def rejected(req, timeout):
            body = json.dumps({'message':'You can only send testing emails to your own email address (owner@example.test).'}).encode()
            raise HTTPError(req.full_url, 403, 'Forbidden', {}, io.BytesIO(body))
        self.providers.transport = rejected
        job = {'id':1,'created':time.time(),'channel':'email','destination':'user@example.test','message':'Teste isolado','kind':'test'}
        with self.assertRaisesRegex(ValueError, 'owner@example.test'):
            self.providers.send(job)

    def test_resend_error_does_not_expose_api_key(self):
        test_key = 'test-only-not-a-real-key'
        self.providers.save({'RESEND_API_KEY':test_key,'RESEND_FROM':'onboarding@resend.dev','live_enabled':True})
        def rejected(req, timeout):
            raise HTTPError(req.full_url, 403, 'Forbidden', {}, io.BytesIO(json.dumps({'message':'Request rejected: '+test_key}).encode()))
        self.providers.transport = rejected
        job = {'id':1,'created':time.time(),'channel':'email','destination':'user@example.test','message':'Teste isolado','kind':'test'}
        with self.assertRaises(ValueError) as raised:
            self.providers.send(job)
        self.assertNotIn(test_key, str(raised.exception))

    def test_non_json_gateway_error_is_identified(self):
        self.providers.save({'RESEND_API_KEY':'test-only-not-a-real-key','RESEND_FROM':'onboarding@resend.dev','live_enabled':True})
        def rejected(req, timeout):
            raise HTTPError(req.full_url, 403, 'Forbidden', {}, io.BytesIO(b'error code: 1010'))
        self.providers.transport = rejected
        job = {'id':1,'created':time.time(),'channel':'email','destination':'user@example.test','message':'Teste isolado','kind':'test'}
        with self.assertRaisesRegex(ValueError, '1010'):
            self.providers.send(job)

    def test_twilio_error_preserves_reason_without_credentials(self):
        sid = 'AC' + '0' * 32
        token = 'isolated-test-secret'
        self.providers.save({'TWILIO_ACCOUNT_SID': sid, 'TWILIO_AUTH_TOKEN': token,
                             'TWILIO_SMS_FROM': '+15555550124', 'live_enabled': True})
        def rejected(req, timeout):
            body = json.dumps({'code': 572006, 'message': 'Trial accepts predefined templates only. ' + token + ' ' + sid}).encode()
            raise HTTPError(req.full_url, 400, 'Bad Request', {}, io.BytesIO(body))
        self.providers.transport = rejected
        job = {'id': 1, 'created': time.time(), 'channel': 'sms', 'destination': '+15555550123', 'message': 'Teste isolado', 'kind': 'test'}
        with self.assertRaises(ValueError) as raised:
            self.providers.send(job)
        detail = str(raised.exception)
        self.assertIn('572006', detail)
        self.assertIn('predefined templates', detail)
        self.assertNotIn(token, detail)
        self.assertNotIn(sid, detail)

    def test_twilio_trial_uses_fixed_test_sms_and_blocks_real_alert(self):
        self.providers.save({'TWILIO_ACCOUNT_SID': 'AC'+'0'*32, 'TWILIO_AUTH_TOKEN': 'test-only',
                             'TWILIO_SMS_FROM': '+15555550124', 'TWILIO_MODE': 'trial', 'live_enabled': True})
        job = {'id': 1, 'created': time.time(), 'channel': 'sms', 'destination': '+15555550123', 'message': 'Temperatura 35 graus', 'kind': 'test'}
        self.providers.send(job)
        body = parse_qs(self.calls[-1].data.decode())
        self.assertEqual(body, {'From': ['+15555550124'], 'To': ['+15555550123'], 'Body': ['sms_appointment_reminders']})
        count = len(self.calls)
        job['kind'] = 'temperature'
        with self.assertRaisesRegex(ValueError, 'predefinidas'):
            self.providers.send(job)
        self.assertEqual(len(self.calls), count)

    def test_twilio_trial_whatsapp_requires_sid_without_arbitrary_variables(self):
        self.providers.save({'TWILIO_ACCOUNT_SID': 'AC'+'0'*32, 'TWILIO_AUTH_TOKEN': 'test-only',
                             'TWILIO_WHATSAPP_FROM': '+15555550124', 'TWILIO_MODE': 'trial', 'live_enabled': True})
        job = {'id': 1, 'created': time.time(), 'channel': 'whatsapp', 'destination': '+15555550123', 'message': 'Teste', 'kind': 'test'}
        self.assertFalse(self.providers.summary()['ready']['whatsapp'])
        with self.assertRaisesRegex(ValueError, 'ContentSid'):
            self.providers.send(job)
        self.assertEqual(self.calls, [])
        self.providers.save({'TWILIO_WHATSAPP_CONTENT_SID': 'HX'+'0'*32, 'live_enabled': True})
        self.assertTrue(self.providers.summary()['ready']['whatsapp'])
        self.providers.send(job)
        body = parse_qs(self.calls[-1].data.decode())
        self.assertEqual(body['ContentSid'], ['HX'+'0'*32])
        self.assertNotIn('ContentVariables', body)
        self.assertNotIn('Body', body)


if __name__ == '__main__':
    unittest.main()
