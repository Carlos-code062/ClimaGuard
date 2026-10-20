"""Outbound providers. Real network calls happen only for explicitly enabled jobs."""
import base64
import json
import os
import re
import threading
from email.utils import parseaddr
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

FIELDS = ('RESEND_API_KEY', 'RESEND_FROM', 'TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN',
          'TWILIO_SMS_FROM', 'TWILIO_WHATSAPP_FROM', 'TWILIO_WHATSAPP_CONTENT_SID', 'TWILIO_MODE')
SECRET_FIELDS = ('RESEND_API_KEY', 'TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN')


class Providers:
    def __init__(self, path, transport=None):
        self.path, self.transport = path, transport or urlopen
        self.lock = threading.Lock()
        self.values = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

    def config(self):
        with self.lock:
            data = dict(self.values)
        for field in FIELDS:
            if os.environ.get(field):
                data[field] = os.environ[field]
        return data

    def live_enabled(self):
        return self.config().get('live_enabled') is True

    def ready(self, channel):
        c = self.config()
        if channel == 'email':
            return bool(c.get('RESEND_API_KEY') and c.get('RESEND_FROM'))
        if channel == 'whatsapp' and c.get('TWILIO_MODE') == 'trial' and not c.get('TWILIO_WHATSAPP_CONTENT_SID'):
            return False
        field = 'TWILIO_SMS_FROM' if channel == 'sms' else 'TWILIO_WHATSAPP_FROM'
        return bool(c.get('TWILIO_ACCOUNT_SID') and c.get('TWILIO_AUTH_TOKEN') and c.get(field))

    def summary(self, admin=False):
        c = self.config()
        result = {'live_enabled': c.get('live_enabled') is True,
                  'ready': {key: self.ready(key) for key in ('email', 'sms', 'whatsapp')}}
        if admin:
            result['fields'] = {k: c.get(k, '') for k in FIELDS if k not in SECRET_FIELDS}
            result['saved'] = {k: bool(c.get(k)) for k in SECRET_FIELDS}
        return result

    def save(self, data):
        with self.lock:
            c = dict(self.values)
            for field in FIELDS:
                value = data.get(field, '')
                if not isinstance(value, str) or len(value) > 512 or any(ord(ch) < 32 for ch in value):
                    raise ValueError('Revise os dados do serviço.')
                value = value.strip()
                if value:
                    c[field] = value
            if c.get('TWILIO_ACCOUNT_SID') and not re.fullmatch(r'AC[0-9a-fA-F]{32}', c['TWILIO_ACCOUNT_SID']):
                raise ValueError('O Account SID da Twilio deve começar com AC e ter 34 caracteres.')
            if c.get('TWILIO_MODE', 'standard') not in ('standard', 'trial'):
                raise ValueError('Selecione o modo de conta Twilio.')
            if c.get('TWILIO_WHATSAPP_CONTENT_SID') and not re.fullmatch(r'HX[0-9a-fA-F]{32}', c['TWILIO_WHATSAPP_CONTENT_SID']):
                raise ValueError('O identificador do modelo WhatsApp deve começar com HX.')
            for key in ('TWILIO_SMS_FROM', 'TWILIO_WHATSAPP_FROM'):
                if c.get(key) and not re.fullmatch(r'\+[1-9]\d{6,14}', c[key].removeprefix('whatsapp:')):
                    raise ValueError('Informe o remetente Twilio com +, país e número.')
            if c.get('RESEND_FROM') and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', parseaddr(c['RESEND_FROM'])[1]):
                raise ValueError('Informe um e-mail de remetente válido para o Resend.')
            if type(data.get('live_enabled', False)) is not bool:
                raise ValueError('Revise a ativação dos envios.')
            c['live_enabled'] = data.get('live_enabled', False)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(c), encoding='utf-8')
            temporary.replace(self.path)
            self.values = c

    def send(self, job):
        channel, destination, message = job['channel'], job['destination'], job['message']
        c = self.config()
        if channel == 'whatsapp' and c.get('TWILIO_MODE') == 'trial' and not c.get('TWILIO_WHATSAPP_CONTENT_SID'):
            raise ValueError('WhatsApp de teste pendente: conclua a vinculacao no Twilio e salve o ContentSid (HX...) do modelo fornecido na aba API. Verificar o telefone para SMS nao conclui a vinculacao WhatsApp.')
        if not self.live_enabled() or not self.ready(channel):
            raise ValueError('Envios reais desativados ou serviço não configurado.')
        c = self.config()
        if channel == 'email':
            body = json.dumps({'from': c['RESEND_FROM'], 'to': [destination],
                               'subject': 'ClimaGuard • ' + ('Teste de envio' if job['kind'] == 'test' else 'Alerta do ambiente'),
                               'text': message}, ensure_ascii=False).encode('utf-8')
            req = Request('https://api.resend.com/emails', data=body, headers={
                'Authorization': 'Bearer ' + c['RESEND_API_KEY'], 'Content-Type': 'application/json',
                'Idempotency-Key': f"climaguard-{job['id']}-{int(job['created'])}"})
        else:
            trial = c.get('TWILIO_MODE') == 'trial'
            if trial and job['kind'] != 'test':
                raise ValueError('A conta de teste Twilio aceita mensagens predefinidas, sem o texto real do alerta. Use e-mail para alertas reais ou configure uma conta Twilio com envio personalizado liberado.')
            sid, token = c['TWILIO_ACCOUNT_SID'], c['TWILIO_AUTH_TOKEN']
            sender = c['TWILIO_SMS_FROM' if channel == 'sms' else 'TWILIO_WHATSAPP_FROM']
            body = {'From': sender, 'To': destination}
            if channel == 'whatsapp':
                body.update(From='whatsapp:' + sender.removeprefix('whatsapp:'), To='whatsapp:' + destination)
                if c.get('TWILIO_WHATSAPP_CONTENT_SID'):
                    body['ContentSid'] = c['TWILIO_WHATSAPP_CONTENT_SID']
                    if not trial:
                        body['ContentVariables'] = json.dumps({'1': message}, ensure_ascii=False)
                else:
                    if trial:
                        raise ValueError('No teste WhatsApp, copie o ContentSid (HX...) da aba API em Experimentar WhatsApp no Twilio e salve em Modelo WhatsApp aprovado. O teste envia o texto predefinido do Twilio, sem a temperatura.')
                    body['Body'] = message
            else:
                if trial:
                    body['Body'] = 'sms_appointment_reminders'
                else:
                    body['Body'] = message
            req = Request(f'https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json', data=urlencode(body).encode(), headers={
                'Authorization': 'Basic ' + base64.b64encode(f'{sid}:{token}'.encode()).decode(),
                'Content-Type': 'application/x-www-form-urlencoded'})
        req.add_header('Accept', 'application/json')
        req.add_header('User-Agent', 'ClimaGuard/2.0')
        try:
            with self.transport(req, timeout=12) as response:
                data = json.loads(response.read(65536))
                return str(data.get('id') or data.get('sid') or '')
        except HTTPError as error:
            code = ''
            message = ''
            raw_error = error.read(65536).decode('utf-8', errors='replace')
            try:
                parsed = json.loads(raw_error)
                if isinstance(parsed, dict):
                    code = str(parsed.get('code', ''))
                    code = code if code.isdigit() else ''
                    message = str(parsed.get('message', ''))
            except (ValueError, OSError):
                pass
            if not message and raw_error.strip():
                gateway_code = re.search(r'error\s*code\s*:\s*(\d{3,5})', raw_error, re.IGNORECASE)
                if gateway_code:
                    raise ValueError(f'O serviço de proteção da API recusou a requisição (HTTP {error.code}, código {gateway_code.group(1)}). Isso não confirma erro na chave ou no destinatário.') from None
                if '<html' in raw_error.lower() or '<!doctype' in raw_error.lower():
                    raise ValueError(f'A conexão recebeu uma página de bloqueio em vez da resposta da API (HTTP {error.code}). Confira a rede e o serviço; isso não confirma erro nas credenciais.') from None
            if channel == 'email':
                lower = message.lower()
                if 'own email' in lower or 'testing emails' in lower:
                    addresses = re.findall(r'[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', message)
                    allowed = f" Destinatário autorizado informado pelo Resend: {addresses[0]}." if addresses else ''
                    raise ValueError('O Resend permite usar onboarding@resend.dev somente para o e-mail da própria conta.' + allowed + ' Para outros destinatários, verifique um domínio no Resend.') from None
                if 'api key' in lower and ('invalid' in lower or 'not found' in lower or 'missing' in lower):
                    raise ValueError('A chave de API foi recusada pelo Resend. Crie uma chave com permissão de envio e substitua a chave em Serviços de envio.') from None
                if 'domain' in lower and ('verif' in lower or 'permission' in lower or 'restrict' in lower):
                    raise ValueError('O Resend recusou o domínio ou a permissão do remetente. Confira se a chave permite o remetente configurado. Para teste, use onboarding@resend.dev com uma chave que permita esse envio.') from None
                if message:
                    # Preserve the provider's useful explanation, never credentials.
                    for secret_field in SECRET_FIELDS:
                        value = c.get(secret_field)
                        if value:
                            message = message.replace(value, '[credencial ocultada]')
                    message = ' '.join(message.split())[:350]
                    raise ValueError(f'Resend recusou o envio (HTTP {error.code}): {message}') from None
            if channel in ('sms', 'whatsapp') and message:
                # API failures need their explanation, including trial restrictions.
                for secret_field in SECRET_FIELDS:
                    value = c.get(secret_field)
                    if value:
                        message = message.replace(value, '[credencial ocultada]')
                message = re.sub(r'(?i)(Basic|Bearer)\s+[A-Za-z0-9+/=._-]+', r'\1 [credencial ocultada]', message)
                message = ' '.join(message.split())[:500]
                suffix = f', código {code}' if code else ''
                raise ValueError(f'Twilio recusou o envio (HTTP {error.code}{suffix}): {message}') from None
            suffix = f' (código {code})' if code else ''
            raise ValueError(f'O serviço recusou o envio: HTTP {error.code}{suffix}. Confira credenciais, remetente e permissões do destinatário.') from None
        except (URLError, OSError, TimeoutError):
            raise ValueError('Não foi possível confirmar o envio. Confira a internet e o serviço antes de tentar novamente.') from None


def deliver_one(store, providers):
    job = store.claim_job()
    if not job:
        return False
    prefs = store.preferences(job['user_id'])
    destination = prefs['email'] if job['channel'] == 'email' else prefs['phone']
    if not prefs['consent'] or not prefs['enabled'] or job['channel'] not in prefs['channels'] or destination != job['destination']:
        store.finish_job(job['id'], 'blocked', 'As preferências de envio foram alteradas antes do processamento.')
        return True
    if not providers.live_enabled() or not providers.ready(job['channel']):
        store.finish_job(job['id'], 'blocked', 'Envios reais desativados ou canal sem configuração.')
        return True
    try:
        provider_id = providers.send(job)
        store.finish_job(job['id'], 'accepted', 'Aceito pelo serviço. A entrega ao destinatário ainda não foi confirmada.', provider_id)
    except ValueError as error:
        store.finish_job(job['id'], 'failed', str(error))
    except Exception:
        store.finish_job(job['id'], 'failed', 'Falha ao processar o envio. Confira o serviço antes de repetir.')
    return True
