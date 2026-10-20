"""Run the local portal and physical USB receiver together."""
import threading
import time
import webbrowser
from waitress import create_server
from server import app
from receber_usb import main as receive_usb


def usb_worker():
    while True:
        try:
            receive_usb()
        except Exception as exc:
            print('USB: aguardando placa/porta disponivel:', exc, flush=True)
            print('Feche o Monitor Serial do Arduino se estiver aberto.', flush=True)
            time.sleep(5)


if __name__ == '__main__':
    try:
        server = create_server(app, host='0.0.0.0', port=5000)
        threading.Thread(target=usb_worker, daemon=True).start()
        webbrowser.open('http://127.0.0.1:5000')
        print('ClimaGuard iniciado: site + sensor USB. Mantenha esta janela aberta.', flush=True)
        server.run()
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        print('Nao foi possivel iniciar o servidor:', exc, flush=True)
        print('Use Reiniciar.cmd para fechar a instancia anterior do ClimaGuard.', flush=True)
