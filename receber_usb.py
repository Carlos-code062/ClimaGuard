"""Forward fresh physical ESP readings to the local portal over USB."""
import json
import math
from pathlib import Path
import time
import urllib.request
import serial
from serial.tools import list_ports

ROOT = Path(__file__).resolve().parent


def physical_reading(line):
    try:
        value = json.loads(line)
        if not isinstance(value, dict) or type(value.get('energy')) is not bool:
            return None
        t, h = value.get('temperature'), value.get('humidity')
        if t is None and h is None:
            return {'temperature': None, 'humidity': None, 'energy': value['energy']}
        if type(t) not in (int, float) or type(h) not in (int, float):
            return None
        if not math.isfinite(t) or not math.isfinite(h) or not -40 <= t <= 80 or not 0 <= h <= 100:
            return None
        return {'temperature': t, 'humidity': h, 'energy': value['energy']}
    except (ValueError, TypeError):
        return None


def main():
    ports = [p.device for p in list_ports.comports() if 'CH340' in p.description.upper() or (p.vid == 0x1A86 and p.pid == 0x7523)]
    if len(ports) != 1:
        raise RuntimeError('Conecte somente a placa ClimaGuard por USB. Nenhuma porta unica CH340 foi encontrada.')
    token = (ROOT / '.device-token').read_text(encoding='utf-8').strip()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    log = ROOT / 'recebimento-usb.log'
    print('ClimaGuard: recebimento REAL por USB em', ports[0], flush=True)
    print('Mantenha esta janela e o servidor abertos. Feche antes de gravar codigo pelo Arduino.', flush=True)
    with serial.Serial(ports[0], 115200, timeout=2) as connection:
        connection.reset_input_buffer()
        while True:
            line = connection.readline().decode('utf-8', errors='replace').strip()
            reading = physical_reading(line)
            if reading is None:
                continue
            request = urllib.request.Request('http://127.0.0.1:5000/api/readings',
                data=json.dumps(reading).encode(), headers={'Content-Type': 'application/json', 'X-Device-Token': token}, method='POST')
            try:
                with opener.open(request, timeout=4) as response:
                    response.read()
                message = f"{time.strftime('%Y-%m-%d %H:%M:%S')} USB recebido: temperatura={reading['temperature']} umidade={reading['humidity']} energia={reading['energy']}"
            except Exception as exc:
                message = f"{time.strftime('%Y-%m-%d %H:%M:%S')} Falha ao entregar ao servidor: {exc}"
            print(message, flush=True)
            with log.open('a', encoding='utf-8') as file:
                file.write(message + '\n')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print('Nao foi possivel receber por USB:', exc, flush=True)
