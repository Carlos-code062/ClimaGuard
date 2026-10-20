# ClimaGuard

Projeto acadêmico de monitoramento de temperatura, umidade e estado de energia simulada, com NodeMCU ESP8266, sensor AM2302/DHT22 e painel local em Flask.

O usuário cria uma conta no painel, escolhe seu limite de temperatura e configura os canais de notificação. O notebook recebe as leituras físicas por USB ou pela API HTTP. O modo de demonstração é identificado e não envia mensagens externas.

## Estado do projeto

| Recurso | Situação |
| --- | --- |
| Temperatura e umidade reais por USB | Testadas no protótipo |
| Chave de energia simulada em D5 | Testada no protótipo |
| Cadastro, preferências e histórico | Implementados |
| Alertas por e-mail com Resend | Envio aceito pelo serviço em teste |
| SMS com Twilio | Teste predefinido recebido; a conta trial limita mensagens personalizadas |
| WhatsApp com Twilio | Integração preparada; vinculação e modelo obrigatório ainda pendentes no provedor |
| Transmissão por Wi-Fi | Firmware e API implementados; conexão pelo hotspot do notebook ainda pendente de estabilização |

**Um SMS de teste predefinido não comprova o envio de um alerta personalizado com a temperatura.** A apresentação física pode usar USB e e-mail enquanto os demais canais são configurados.

## Materiais e montagem

- NodeMCU ESP8266 (ESP-12E/ESP-12F), cabo USB de dados e notebook Windows.
- AM2302/DHT22 de quatro pinos, resistor de 4,7 kΩ e jumpers.
- Chave para simular presença de energia e resistor de 10 kΩ.

Veja a [pinagem e o esquema de ligação](docs/MONTAGEM.md). A chave usa somente **3,3 V**. Não conecte o circuito à rede elétrica de 127/220 V.

## Instalação no Windows

1. Instale Python 3.11 ou superior a partir do site oficial e habilite o acesso pelo terminal.
2. Baixe este repositório em **Code → Download ZIP** e extraia a pasta, ou clone com Git.
3. Execute **Instalar.cmd**. Ele cria o ambiente Python e instala as dependências.
4. Conecte a placa já programada e execute **Iniciar.cmd**.
5. Acesse **http://127.0.0.1:5000/** e crie sua conta. A primeira conta é administradora.
6. Selecione o modo real para os dados físicos ou demonstração para cenários simulados.

Mantenha a janela do ClimaGuard aberta. Ela inicia o servidor e o receptor USB juntos. Feche o Monitor Serial do Arduino para liberar a porta da placa. O receptor procura uma única interface CH340 e tenta se reconectar quando ela fica indisponível. Placas com outro conversor USB exigem ajustar a detecção em `receber_usb.py`.

Para aplicar mudanças, execute **Reiniciar.cmd**. Para rodar somente os testes, execute **Verificar.cmd**. Os testes usam serviços simulados e não enviam mensagens.

## Programar a placa

1. Instale Arduino IDE, o suporte de placas ESP8266, a biblioteca **DHT sensor library by Adafruit** e **Adafruit Unified Sensor**.
2. Abra `ClimaGuard/ClimaGuard.ino` e selecione **NodeMCU 1.0 (ESP-12E Module)** e a porta USB correspondente.
3. Inicie o painel uma vez para gerar o arquivo local `.device-token`.
4. Copie `ClimaGuard/config.h.example` para `ClimaGuard/config.h` e preencha os campos localmente. Use o mesmo token gerado pelo servidor.
5. Se usar Wi-Fi, configure uma rede de 2,4 GHz e o endereço do notebook em `SERVER_URL`. O ESP8266 não realiza login em portal cativo da faculdade.
6. Feche o ClimaGuard para liberar a porta, grave o firmware e abra **Iniciar.cmd** novamente.

`config.h` é um arquivo de configuração C/C++ do firmware. `index.html` é a página do site. Este `README.md` é a documentação exibida na página inicial do repositório.

## Notificações

Em **Serviços**, o administrador configura Resend e/ou Twilio. Em **Preferências**, cada usuário escolhe destinatários, consentimento, temperatura limite, eventos e canais.

- Temperatura: alerta ao ultrapassar o limite; rearma após cair pelo menos 1 °C abaixo dele.
- Energia: alerta quando a chave passa a indicar ausência de energia.
- Intervalo mínimo e estado da condição reduzem mensagens repetidas.
- **Demonstração:** cria eventos identificados como simulados, sem envio externo.
- **Twilio trial:** os botões de teste enviam apenas os exemplos permitidos. Alertas personalizados não são enviados nesse modo. WhatsApp exige vinculação e um `ContentSid` fornecido pelo provedor.
- “Aceito pelo serviço” significa que o provedor aceitou a requisição, não que a entrega foi confirmada.

Não existem credenciais ou destinatários reais neste repositório. Os limites e eventuais custos dependem de cada provedor.

## Estrutura

```text
ClimaGuard/                 Firmware e exemplo de configuração
docs/                       Montagem e colaboração
tests/                      Testes automatizados do portal e dos provedores
web_assets/                 JavaScript e CSS do painel
index.html                  Interface do site
portal.py                   Rotas, contas e processamento das notificações
storage.py                  Persistência SQLite e regras de alerta
providers.py                Integração Resend/Twilio
receber_usb.py              Ponte serial ESP8266 → API local
iniciar_completo.py         Inicialização conjunta do servidor e USB
```

## Segurança e escopo

As contas, leituras e preferências ficam em SQLite no notebook. Senhas de usuários são armazenadas com hash. Credenciais de provedores e segredos da aplicação ficam em arquivos locais separados, excluídos pelo `.gitignore`.

O painel é destinado ao acesso local; a API de leituras exige um token. Não publique a porta 5000 na internet. Este repositório público compartilha o código: **GitHub Pages não executa Flask, não acessa o sensor USB e não hospeda este sistema completo**.

Nunca envie `config.h`, `.providers.json`, `.device-token`, `.session-secret`, banco de dados, logs ou sua pasta `.venv` ao GitHub.

## Autoria e colaboração

Responsável pelo repositório: [Carlos-code062](https://github.com/Carlos-code062). Os créditos dos demais integrantes serão adicionados após confirmação dos nomes e perfis.

O repositório pode ter várias pessoas colaborando. Veja [como registrar autores e contribuições](docs/COLABORACAO.md).
