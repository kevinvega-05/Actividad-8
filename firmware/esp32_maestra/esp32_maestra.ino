/*
 * =====================================================================
 *  ESP32 MAESTRA  -  control en tiempo real de un contenedor de simulación
 *  Zonas Virtualizadas de Simulación Físico-Robótica · UMNG Mecatrónica
 * =====================================================================
 *  Lee 2 potenciómetros y 3 pulsadores y los envía por UDP a 50 Hz al
 *  contenedor que le corresponde según su ROL (ver config.h). El contenedor
 *  responde a cada paquete con un ACK; con eso la ESP32 mide el RTT real del
 *  enlace (WiFi + Docker), el jitter y la pérdida, y los reporta en el
 *  siguiente paquete para que el plano de administración los vea.
 *
 *  Paquete:  C,<seq>,<millis>,<pot1>,<pot2>,<b1>,<b2>,<b3>,<rtt_us>,0
 *            pot1/pot2 en -1000..1000, botones 1 = presionado
 *  ACK:      A,<seq>,<estado del contenedor>
 *
 *  LED azul:  fijo = enlace OK · parpadeo lento = WiFi OK pero el contenedor
 *             no responde · parpadeo rápido = sin WiFi
 *  Placa: "ESP32 Dev Module" (core arduino-esp32 2.x o 3.x). Sin librerías extra.
 * =====================================================================
 */
#include <WiFi.h>
#include <WiFiUdp.h>
#include "config.h"

#if ROL < 1 || ROL > 6
#error "ROL debe estar entre 1 y 6 (ver config.h)"
#endif

const uint16_t PUERTOS[7] = {0, 5001, 5002, 5003, 5011, 5012, 5013};
const char *NOMBRES[7] = {"", "carro 1", "carro 2", "carro 3", "Spot", "Pepper", "NAO"};
const uint16_t PUERTO = PUERTOS[ROL];
const IPAddress PC(PC_IP);

WiFiUDP udp;
char buf[128];

// ---- registro de envíos para medir RTT (anillo de 64) ----
#define N_ANILLO 64
uint32_t anilloSeq[N_ANILLO];
uint32_t anilloT[N_ANILLO];

uint32_t seq = 0;
int32_t ultimoRttUs = -1;
uint32_t tUltimoAck = 0;
char estadoRemoto[24] = "-";

// estadísticas por ventana de reporte
uint32_t vEnviados = 0, vAcks = 0;
uint32_t vRttMin = UINT32_MAX, vRttMax = 0;
uint64_t vRttSuma = 0;
float jitterUs = 0;          // RFC 3550 sobre RTT consecutivos
int32_t rttPrev = -1;

// ---- entradas ----
struct Boton { uint8_t pin; bool estable; bool lectura; uint32_t t; };
Boton botones[3] = {{PIN_BTN1, false, false, 0}, {PIN_BTN2, false, false, 0}, {PIN_BTN3, false, false, 0}};

int leerPot(uint8_t pin, bool invertir) {
  uint32_t s = 0;
  for (int i = 0; i < 8; i++) s += analogRead(pin);      // promedio de 8 lecturas
  int v = map(s / 8, 0, 4095, -1000, 1000);
  v = constrain(v, -1000, 1000);
  return invertir ? -v : v;
}

void leerBotones() {
  uint32_t ahora = millis();
  for (auto &b : botones) {
    bool l = digitalRead(b.pin) == LOW;                   // activo en bajo (pull-up)
    if (l != b.lectura) { b.lectura = l; b.t = ahora; }
    if (ahora - b.t > 25) b.estable = b.lectura;          // antirrebote 25 ms
  }
}

// ---- red ----
void conectarWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);                // sin ahorro de energía: menos latencia y jitter
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("[WiFi] conectando a '%s'", WIFI_SSID);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) {
    delay(250);
    Serial.print(".");
    digitalWrite(PIN_LED, !digitalRead(PIN_LED));
  }
  if (WiFi.status() == WL_CONNECTED)
    Serial.printf(" OK  IP %s  RSSI %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  else
    Serial.println(" sin conexión (se reintenta)");
}

void enviar(int p1, int p2) {
  seq++;
  uint32_t ahoraUs = micros();
  anilloSeq[seq % N_ANILLO] = seq;
  anilloT[seq % N_ANILLO] = ahoraUs;
  int n = snprintf(buf, sizeof(buf), "C,%lu,%lu,%d,%d,%d,%d,%d,%ld,0",
                   (unsigned long)seq, (unsigned long)millis(), p1, p2,
                   botones[0].estable, botones[1].estable, botones[2].estable, (long)ultimoRttUs);
  udp.beginPacket(PC, PUERTO);
  udp.write((const uint8_t *)buf, n);
  udp.endPacket();
  vEnviados++;
}

void recibirAcks() {
  int tam;
  while ((tam = udp.parsePacket()) > 0) {
    uint32_t llegada = micros();
    int n = udp.read(buf, sizeof(buf) - 1);
    if (n <= 0) continue;
    buf[n] = 0;
    if (buf[0] != 'A' || buf[1] != ',') continue;
    char *coma;
    uint32_t s = strtoul(buf + 2, &coma, 10);
    if (anilloSeq[s % N_ANILLO] != s) continue;            // demasiado viejo
    uint32_t rtt = llegada - anilloT[s % N_ANILLO];
    anilloSeq[s % N_ANILLO] = 0;                           // no contarlo dos veces
    ultimoRttUs = rtt;
    if (rttPrev >= 0) jitterUs += (fabs((float)rtt - rttPrev) - jitterUs) / 16.0f;
    rttPrev = rtt;
    vAcks++;
    vRttSuma += rtt;
    if (rtt < vRttMin) vRttMin = rtt;
    if (rtt > vRttMax) vRttMax = rtt;
    tUltimoAck = millis();
    if (coma && *coma == ',') {
      strncpy(estadoRemoto, coma + 1, sizeof(estadoRemoto) - 1);
      estadoRemoto[sizeof(estadoRemoto) - 1] = 0;
    }
  }
}

void actualizarLed() {
  uint32_t t = millis();
  bool wifi = WiFi.status() == WL_CONNECTED;
  bool enlace = wifi && tUltimoAck && (t - tUltimoAck < TIMEOUT_ACK_MS);
  if (enlace) digitalWrite(PIN_LED, HIGH);
  else if (wifi) digitalWrite(PIN_LED, (t / 500) % 2);     // lento: el contenedor no responde
  else digitalWrite(PIN_LED, (t / 100) % 2);               // rápido: sin WiFi
}

void reportar(int p1, int p2) {
  float perdida = vEnviados ? 100.0f * (vEnviados - vAcks) / vEnviados : 0;
  if (vAcks)
    Serial.printf("[%s] RTT prom %.2f ms (min %.2f / max %.2f) · jitter %.2f ms · pérdida %.1f %% · "
                  "estado %s · POT1 %+5d POT2 %+5d · B %d%d%d\n",
                  NOMBRES[ROL], vRttSuma / 1000.0 / vAcks, vRttMin / 1000.0, vRttMax / 1000.0,
                  jitterUs / 1000.0, perdida, estadoRemoto, p1, p2,
                  botones[0].estable, botones[1].estable, botones[2].estable);
  else
    Serial.printf("[%s] sin respuesta del contenedor en %d.%d.%d.%d:%u · POT1 %+5d POT2 %+5d\n",
                  NOMBRES[ROL], PC[0], PC[1], PC[2], PC[3], PUERTO, p1, p2);
  vEnviados = vAcks = 0;
  vRttSuma = 0;
  vRttMin = UINT32_MAX;
  vRttMax = 0;
}

// =====================================================================
void setup() {
  Serial.begin(115200);
  delay(200);
  pinMode(PIN_LED, OUTPUT);
  for (auto &b : botones) pinMode(b.pin, INPUT_PULLUP);
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);        // rango completo 0-3.3 V
  Serial.printf("\n=== ESP32 maestra · ROL %d (%s) -> %d.%d.%d.%d:%u ===\n",
                ROL, NOMBRES[ROL], PC[0], PC[1], PC[2], PC[3], PUERTO);
  conectarWiFi();
  udp.begin(PUERTO);                     // mismo puerto local: fácil de identificar
}

void loop() {
  static uint32_t tEnvio = 0, tReporte = 0, tReintento = 0;
  static int p1 = 0, p2 = 0;
  uint32_t ahora = millis();

  if (WiFi.status() != WL_CONNECTED) {
    if (ahora - tReintento > 5000) {
      tReintento = ahora;
      Serial.println("[WiFi] reconectando...");
      WiFi.disconnect();
      WiFi.begin(WIFI_SSID, WIFI_PASS);
    }
  }

  leerBotones();
  recibirAcks();                         // se atiende en cada vuelta: RTT preciso

  if (ahora - tEnvio >= PERIODO_ENVIO_MS) {
    tEnvio += PERIODO_ENVIO_MS;
    if (ahora - tEnvio > 100) tEnvio = ahora;      // no acumular atraso
    p1 = leerPot(PIN_POT1, INVERTIR_POT1);
    p2 = leerPot(PIN_POT2, INVERTIR_POT2);
    if (WiFi.status() == WL_CONNECTED) enviar(p1, p2);
  }
  if (ahora - tReporte >= PERIODO_REPORTE_MS) {
    tReporte = ahora;
    reportar(p1, p2);
  }
  actualizarLed();
}
