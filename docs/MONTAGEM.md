# Montagem do protótipo

## AM2302/DHT22 de quatro pinos

Observe o sensor de frente, com a grade voltada para você e os terminais para baixo. Confirme a identificação do fabricante antes de ligar.

| Pino do sensor | Ligação na NodeMCU |
| --- | --- |
| 1 — VCC | 3V3 |
| 2 — DATA | D2 / GPIO4 |
| 3 — não conectado | Deixar livre |
| 4 — GND | G / GND |

Coloque um resistor de **4,7 kΩ entre VCC e DATA**. Mantenha os fios curtos e o GND compartilhado.

## Chave simuladora de energia

Na chave de três terminais usada no protótipo:

- Terminal central → D5 / GPIO14.
- Um terminal lateral → 3V3.
- Outro terminal lateral → não conectado.
- Resistor de 10 kΩ entre D5 e GND.

```text
3V3 ── chave ──┬── D5 / GPIO14
               │
             10 kΩ
               │
              GND
```

A posição que fecha o contato com 3V3 indica energia presente. A outra indica energia ausente. Identifique os contatos reais da chave; não presuma a função pela posição visual da alavanca.

## Alimentação

O cabo USB fornece alimentação à NodeMCU e transmite as leituras ao notebook. Nenhum pino do ESP8266 deve receber 5 V como sinal lógico.

**Este protótipo simula energia com uma chave. Não conecte 127/220 V à placa, ao sensor ou à protoboard.** Ele não mede a rede elétrica e não substitui um equipamento certificado de monitoramento.
