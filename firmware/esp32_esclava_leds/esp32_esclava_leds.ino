/*
 * =====================================================================
 *  ESP32 ESCLAVA DE LEDs  -  señalización física del plano de administración
 *  Zonas Virtualizadas de Simulación Físico-Robótica · UMNG Mecatrónica
 * =====================================================================
 *  Patrón maestro-esclavo: esta placa NO decide nada. Se suscribe al tópico
 *  MQTT "lab/leds" que publica el monitor (contenedor Alpine de la VLAN 3) y
 *  refleja en 8 LEDs el estado de cada contenedor:
 *
 *    '0' CAÍDO      LED apagado
 *    '1' OK         LED encendido fijo
 *    '2' ACTIVO     parpadeo rápido (su ESP32 maestra lo está controlando)
 *    '3' DEGRADADO  parpadeo lento  (latencia / jitter / pérdida altos)
 *
 *  Además publica su latido en "lab/leds/esclava" (con testamento MQTT),
 *  así el tablero sabe si la esclava está en línea.
 *
 *  Requiere la librería "PubSubClient" de Nick O'Leary (Gestor de librerías).
 *  Placa: "ESP32 Dev Module".
 * =====================================================================
 */
#include <WiFi.h>
#include <PubSubClient.h>
#include "config.h"

WiFiClient red;
PubSubClient mqtt(red);

char estados[9] = "00000000";
uint32_t tUltimoMensaje = 0;
uint32_t mensajes = 0;
char idCliente[32];

void alRecibir(char *topico, byte *datos, unsigned int largo) {
  if (strcmp(topico, "lab/leds") != 0) return;
  for (int i = 0; i < 8; i++) {
    char c = i < (int)largo ? (char)datos[i] : '0';
    estados[i] = (c >= '0' && c <= '3') ? c : '0';
  }
  tUltimoMensaje = millis();
  mensajes++;
}

void conectarWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("[WiFi] conectando a '%s'", WIFI_SSID);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) {
    delay(250);
    Serial.print(".");
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? " OK" : " sin conexión");
  if (WiFi.status() == WL_CONNECTED)
    Serial.printf("[WiFi] IP %s  RSSI %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
}

bool conectarMqtt() {
  Serial.printf("[MQTT] conectando a %s:%d ... ", MQTT_HOST, MQTT_PUERTO);
  // testamento: si la esclava se desconecta, el broker avisa al tablero
  bool ok = mqtt.connect(idCliente, nullptr, nullptr, "lab/leds/esclava", 0, true, "{\"vivo\":false}");
  if (ok) {
    mqtt.subscribe("lab/leds");
    Serial.println("OK, suscrita a lab/leds");
  } else {
    Serial.printf("falló (estado %d)\n", mqtt.state());
  }
  return ok;
}

void publicarLatido() {
  char json[160];
  snprintf(json, sizeof(json),
           "{\"vivo\":true,\"ip\":\"%s\",\"rssi\":%d,\"uptime_s\":%lu,\"mensajes\":%lu,\"leds\":\"%s\"}",
           WiFi.localIP().toString().c_str(), WiFi.RSSI(), (unsigned long)(millis() / 1000),
           (unsigned long)mensajes, estados);
  mqtt.publish("lab/leds/esclava", json, true);
}

void autoprueba() {          // al encender: barrido de los 8 LEDs
  for (int i = 0; i < 8; i++) { digitalWrite(PINES_LED[i], HIGH); delay(90); }
  for (int i = 0; i < 8; i++) { digitalWrite(PINES_LED[i], LOW); delay(60); }
}

void dibujarLeds() {
  uint32_t t = millis();
  bool datosFrescos = mqtt.connected() && tUltimoMensaje && (t - tUltimoMensaje < SIN_DATOS_MS);
  if (!datosFrescos) {
    // sin datos del monitor: una luz que recorre la tira (esperando)
    int k = (t / 150) % 8;
    for (int i = 0; i < 8; i++) digitalWrite(PINES_LED[i], i == k);
    return;
  }
  bool rapido = (t / 125) % 2;       // 4 Hz
  bool lento = (t / 500) % 2;        // 1 Hz
  for (int i = 0; i < 8; i++) {
    bool on = false;
    switch (estados[i]) {
      case '1': on = true; break;
      case '2': on = rapido; break;
      case '3': on = lento; break;
      default:  on = false;
    }
    digitalWrite(PINES_LED[i], on);
  }
}

void setup() {
  Serial.begin(115200);
  delay(200);
  for (int i = 0; i < 8; i++) pinMode(PINES_LED[i], OUTPUT);
  pinMode(PIN_LED_PLACA, OUTPUT);
  snprintf(idCliente, sizeof(idCliente), "esp32-leds-%06lX", (unsigned long)(ESP.getEfuseMac() & 0xFFFFFF));
  Serial.println("\n=== ESP32 esclava de LEDs · plano de administración ===");
  autoprueba();
  conectarWiFi();
  mqtt.setServer(MQTT_HOST, MQTT_PUERTO);
  mqtt.setCallback(alRecibir);
  mqtt.setKeepAlive(10);
}

void loop() {
  static uint32_t tReintento = 0, tLatido = 0;
  uint32_t ahora = millis();

  if (WiFi.status() != WL_CONNECTED) {
    if (ahora - tReintento > 5000) { tReintento = ahora; conectarWiFi(); }
  } else if (!mqtt.connected()) {
    if (ahora - tReintento > 3000) { tReintento = ahora; conectarMqtt(); }
  } else {
    mqtt.loop();
    if (ahora - tLatido > 2000) { tLatido = ahora; publicarLatido(); }
  }

  digitalWrite(PIN_LED_PLACA, mqtt.connected() ? HIGH : (ahora / 300) % 2);
  dibujarLeds();
}
