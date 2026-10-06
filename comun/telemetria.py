"""
Publicación de telemetría al broker MQTT del plano de administración (VLAN 3).

El broker vive en 10.30.30.20; desde las zonas se llega a través del
enrutador inter-VLAN, que SOLO permite TCP 1883 hacia ese broker.
Si el broker no está, la simulación sigue funcionando (reintenta sola).
"""
import json
import os
import threading
import time

import paho.mqtt.client as mqtt

BROKER = os.environ.get("MQTT_BROKER", "10.30.30.20")
PUERTO = int(os.environ.get("MQTT_PUERTO", "1883"))


class Telemetria:
    def __init__(self, nombre, zona):
        self.nombre = nombre
        self.zona = zona
        self.conectado = False
        try:   # paho-mqtt 2.x
            self.cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{nombre}")
        except AttributeError:   # paho-mqtt 1.x
            self.cli = mqtt.Client(client_id=f"sim-{nombre}")
        self.cli.on_connect = lambda *a: setattr(self, "conectado", a[3] == 0)
        self.cli.on_disconnect = lambda *a: setattr(self, "conectado", False)
        self.cli.will_set(f"lab/telemetria/{nombre}",
                          json.dumps({"nombre": nombre, "zona": zona, "vivo": False}), retain=True)
        threading.Thread(target=self._conectar, daemon=True).start()

    def _conectar(self):
        while True:
            try:
                self.cli.connect(BROKER, PUERTO, keepalive=10)
                self.cli.loop_forever(retry_first_connection=True)
            except Exception:
                pass
            time.sleep(2)

    def publicar(self, datos):
        datos = dict(datos, nombre=self.nombre, zona=self.zona, vivo=True, t=time.time())
        if self.conectado:
            self.cli.publish(f"lab/telemetria/{self.nombre}", json.dumps(datos), qos=0, retain=True)
