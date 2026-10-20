#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <DHT.h>
#include "config.h"
DHT dht(D2, DHT22);
unsigned long lastSend = 0, switchChangedAt = 0;
bool rawEnergy, stableEnergy, switchPending = true;
void setup() {
  Serial.begin(115200);
  pinMode(D5, INPUT); // Resistor externo de 10 kohms entre D5 e GND.
  rawEnergy = stableEnergy = digitalRead(D5) == HIGH;
  dht.begin();
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setSleepMode(WIFI_NONE_SLEEP);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.println("ClimaGuard: DHT22 D2/GPIO4; chave D5/GPIO14");
}
void loop() {
  bool sample = digitalRead(D5) == HIGH;
  if (sample != rawEnergy) { rawEnergy = sample; switchChangedAt = millis(); }
  if (millis() - switchChangedAt >= 80 && stableEnergy != rawEnergy) {
    stableEnergy = rawEnergy;
    switchPending = true;
  }
  if (!switchPending && millis() - lastSend < 5000) { delay(20); return; }
  lastSend = millis();
  switchPending = false;
  float h = dht.readHumidity(), t = dht.readTemperature();
  bool sensorOK = !isnan(h) && !isnan(t);
  String payload = "{\"temperature\":";
  payload += sensorOK ? String(t, 1) : "null";
  payload += ",\"humidity\":";
  payload += sensorOK ? String(h, 1) : "null";
  payload += ",\"energy\":";
  payload += stableEnergy ? "true}" : "false}";
  Serial.println(payload);
  if (!sensorOK) Serial.println("ERRO_DHT: chave continua funcionando");
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("IP_ESP: "); Serial.println(WiFi.localIP());
    Serial.print("GATEWAY: "); Serial.println(WiFi.gatewayIP());
    Serial.print("SERVIDOR: "); Serial.println(SERVER_URL);
    WiFiClient client;
    HTTPClient http;
    http.setTimeout(3000);
    if (http.begin(client, SERVER_URL)) {
      http.addHeader("Content-Type", "application/json");
      http.addHeader("X-Device-Token", DEVICE_TOKEN);
      Serial.printf("HTTP: %d\n", http.POST(payload));
      http.end();
    }
  } else Serial.printf("WIFI_STATUS=%d\n", WiFi.status());
}
