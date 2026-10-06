#pragma once
// =====================================================================
//  CONFIGURACIÓN DE LA ESP32 MAESTRA  (cambia ROL en cada placa)
// =====================================================================
//   ROL 1 = carro 1  -> contenedor cliente1  (VLAN 1)  UDP 5001
//   ROL 2 = carro 2  -> contenedor cliente2  (VLAN 1)  UDP 5002
//   ROL 3 = carro 3  -> contenedor cliente3  (VLAN 1)  UDP 5003
//   ROL 4 = Spot     -> contenedor spot      (VLAN 2)  UDP 5011
//   ROL 5 = Pepper   -> contenedor pepper    (VLAN 2)  UDP 5012
//   ROL 6 = NAO      -> contenedor nao       (VLAN 2)  UDP 5013
#define ROL 1

// ---- Red WiFi: la MISMA a la que está conectado el PC con Docker ----
#define WIFI_SSID  "TU_RED_WIFI"
#define WIFI_PASS  "TU_CLAVE_WIFI"
// IP del PC con Docker Desktop en esa red (en Windows: ipconfig -> "Dirección IPv4")
#define PC_IP      192, 168, 1, 50

// ---- Pines (ESP32 DevKit V1) ----
//  Potenciómetros de 10 kΩ: extremos a 3V3 y GND, cursor al pin (solo ADC1: 32-39)
#define PIN_POT1   34      // carro: acelerador   · robot: avanzar/retroceder
#define PIN_POT2   35      // carro: dirección    · robot: girar
//  Pulsadores entre el pin y GND (se usa la resistencia pull-up interna)
#define PIN_BTN1   25      // carro: reversa      · robot: saludar
#define PIN_BTN2   26      // carro: turbo        · robot: cambiar postura
#define PIN_BTN3   27      // carro: reaparecer   · robot: reiniciar
#define PIN_LED    2       // LED azul de la placa: estado del enlace

// Invierte un potenciómetro si quedó cableado al revés (1 = invertir)
#define INVERTIR_POT1  0
#define INVERTIR_POT2  0

// ---- Tiempos ----
#define PERIODO_ENVIO_MS   20      // 50 Hz
#define TIMEOUT_ACK_MS     500     // sin ACK por más de esto => enlace caído
#define PERIODO_REPORTE_MS 1000    // resumen por el monitor serie
