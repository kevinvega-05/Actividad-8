#pragma once
// =====================================================================
//  CONFIGURACIÓN DE LA ESP32 ESCLAVA DE LEDs (plano de administración)
// =====================================================================
#define WIFI_SSID   "TU_RED_WIFI"
#define WIFI_PASS   "TU_CLAVE_WIFI"
// IP del PC con Docker Desktop: el broker MQTT se publica en su puerto 1883
#define MQTT_HOST   "192.168.1.50"
#define MQTT_PUERTO 1883

// 8 LEDs (con resistencia de 220-330 Ω a GND), en el mismo orden del tablero:
//   LED1 pista · LED2 cliente1 · LED3 cliente2 · LED4 cliente3
//   LED5 spot  · LED6 pepper   · LED7 nao      · LED8 enrutador
// Pines seguros del ESP32 DevKit V1 (no afectan el arranque):
const uint8_t PINES_LED[8] = {4, 16, 17, 18, 19, 21, 22, 23};
#define PIN_LED_PLACA 2          // LED azul: estado de la conexión MQTT

#define SIN_DATOS_MS 5000        // si el monitor no publica en este tiempo -> animación de espera
