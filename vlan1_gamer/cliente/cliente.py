"""
CONTENEDOR CLIENTE  (VLAN 1 - Zona Gamer, 10.10.10.11 / .12 / .13)

  ESP32 maestra --UDP 500X--> [cliente X] --UDP 6000 (VLAN 1)--> servidor de pista
                                    ^                                   |
                                    +------- estado 30 Hz --------------+

- Traduce los mandos físicos (2 potenciómetros + 3 pulsadores) a comandos del juego.
- Recibe el estado autoritativo del servidor y dibuja SU carro con cámara de persecución
  (réplica local de la escena, sin física: modelo cliente-servidor de un juego en red).

Mandos:  POT1 = acelerador · POT2 = dirección · B1 = reversa · B2 = turbo · B3 = reaparecer
"""
import json
import math
import os
import socket
import sys
import threading
import time

import pybullet as p

sys.path.insert(0, "/app")
import pista_comun as pc                      # noqa: E402
from enlace_esp32 import EnlaceESP32          # noqa: E402
from telemetria import Telemetria             # noqa: E402
from render_remoto import RenderRemoto        # noqa: E402
from visor_web import VisorWeb                # noqa: E402

ID = int(os.environ.get("CLIENTE_ID", "1"))
PUERTO_ESP32 = int(os.environ.get("PUERTO_ESP32", str(5000 + ID)))
SERVIDOR = (os.environ.get("SERVIDOR_PISTA", "10.10.10.10"), int(os.environ.get("PUERTO_JUEGO", "6000")))
ZONA_MUERTA = 60          # en unidades de -1000..1000


def zona_muerta(v):
    return 0.0 if abs(v) < ZONA_MUERTA else (v - math.copysign(ZONA_MUERTA, v)) / (1000 - ZONA_MUERTA)


class Cliente:
    def __init__(self):
        self.enlace = EnlaceESP32(PUERTO_ESP32, f"cliente{ID}")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.2)
        self.snap = None
        self.t_snap = 0.0
        self.snaps_por_s = 0
        self._cuenta = 0
        self.lock = threading.Lock()
        self.mando = {}
        threading.Thread(target=self._rx, daemon=True).start()
        # réplica de la pista (sin física) y un proceso de render con la misma escena
        self.cid, self.carros = pc.escena_render()
        self.render_proc = RenderRemoto(pc.escena_render, (), 512, 384, 62, 80)
        self.tele = Telemetria(f"cliente{ID}", "vlan1")
        self.visor = VisorWeb(
            f"Cliente {ID} · {pc.NOMBRES[ID]}", f"Contenedor cliente 10.10.10.1{ID} · ESP32 en UDP {PUERTO_ESP32}",
            "VLAN 1", "#%02x%02x%02x" % tuple(int(255 * c) for c in pc.COLORES[ID][:3]),
            {"a1": "Acelerador", "a1c": False, "a2": "Dirección", "a2c": True,
             "b1": "Reversa", "b2": "Turbo", "b3": "Reaparecer"},
            self.estado_web, self.salud)

    def _rx(self):
        while True:
            try:
                data, _ = self.sock.recvfrom(16384)
            except (socket.timeout, OSError):
                continue
            try:
                snap = json.loads(data)
            except ValueError:
                continue
            with self.lock:
                self.snap = snap
                self.t_snap = time.time()
                self._cuenta += 1

    def servidor_ok(self):
        return time.time() - self.t_snap < 1.0

    def mi_carro(self):
        with self.lock:
            if not self.snap:
                return None, None
            carros = sorted(self.snap["carros"], key=lambda c: -c["dist"])
        for pos, c in enumerate(carros, 1):
            if c["id"] == ID:
                return c, pos
        return None, None

    # ---------------- web / salud ----------------
    def estado_web(self):
        c, pos = self.mi_carro()
        hud = {}
        grande = "—"
        if c:
            grande = f"{c['vel'] * 3.6:.0f} km/h"
            hud = {"Posición": f"{pos} de 4", "Vueltas": c["vueltas"],
                   "Última vuelta": f"{c['ultima']:.2f} s" if c["ultima"] else "—",
                   "Mejor vuelta": f"{c['mejor']:.2f} s" if c["mejor"] else "—",
                   "Piloto automático": "sí" if c["auto"] else "no"}
        hud["Servidor de pista"] = f"conectado · {self.snaps_por_s} est/s" if self.servidor_ok() else "sin respuesta"
        return {"titulo_hud": "Carrera", "grande": grande, "hud": hud,
                "ctrl": self.enlace.controles(), "enlace": self.enlace.metricas()}

    def salud(self):
        return {"ok": True, "nombre": f"cliente{ID}", "servidor_ok": self.servidor_ok(),
                "esp32": self.enlace.metricas()}

    # ---------------- dibujo ----------------
    def actualizar_escena(self):
        with self.lock:
            snap = self.snap
        if not snap:
            return
        for c in snap["carros"]:
            body = self.carros[c["id"]]
            p.resetBasePositionAndOrientation(body, c["pos"], c["q"], physicsClientId=self.cid)
            for d in pc.DIRECCION:
                p.resetJointState(body, d, c["giro"], physicsClientId=self.cid)

    def render(self):
        """Cámara de persecución detrás de su carro (o vista general si aún no hay servidor)."""
        c, _ = self.mi_carro()
        if c:
            x, y, z = c["pos"]
            yaw = p.getEulerFromQuaternion(c["q"])[2]
            ojo = [x - 2.0 * math.cos(yaw), y - 2.0 * math.sin(yaw), z + 1.05]
            mira = [x + 1.5 * math.cos(yaw), y + 1.5 * math.sin(yaw), z + 0.2]
        else:
            ojo, mira = [0, -11.7, 15.6], [0, -0.8, 0]
        self.render_proc.pedir(self.cid, list(self.carros.values()), ojo, mira)

    # ---------------- bucle ----------------
    def _tx_mandos(self):
        """50 Hz fijos hacia el servidor, en su propio hilo (el render no lo frena)."""
        seq = 0
        siguiente = time.time()
        while True:
            ctl = self.enlace.controles()
            reap = 1 if self.enlace.pulsacion("b3") else 0
            acel = (ctl["a1"] + 1000) / 2000.0 if self.enlace.conectada() else 0.0
            giro = -zona_muerta(ctl["a2"])           # pote a la derecha = girar a la derecha
            seq += 1
            msg = f"IN,{ID},{acel:.3f},{giro:.3f},{ctl['b2']},{ctl['b1']},{reap},{ctl['auto']},{seq}"
            try:
                self.sock.sendto(msg.encode(), SERVIDOR)
            except OSError:
                pass
            c, pos = self.mi_carro()
            self.enlace.estado_txt = f"P{pos}V{c['vueltas']}" if c else "SIN_SERVIDOR"
            siguiente += 0.02
            espera = siguiente - time.time()
            if espera > 0:
                time.sleep(espera)
            else:
                siguiente = time.time()

    def correr(self):
        threading.Thread(target=self._tx_mandos, daemon=True).start()
        ult_tel = ult_frame = ult_cuenta = time.time()
        while True:
            t = time.time()
            if t - ult_cuenta >= 1.0:
                with self.lock:
                    self.snaps_por_s, self._cuenta = self._cuenta, 0
                ult_cuenta = t
            if t - ult_tel >= 1.0:
                ult_tel = t
                c, pos = self.mi_carro()
                self.tele.publicar({"esp32": self.enlace.metricas(), "servidor_ok": self.servidor_ok(),
                                    "estados_por_s": self.snaps_por_s, "carro": c, "posicion": pos})
            jpeg = self.render_proc.recoger()
            if jpeg:
                self.visor.guardar_jpeg(jpeg)
            if self.visor.hay_demanda() and t - ult_frame >= 1 / 15:
                ult_frame = t
                self.actualizar_escena()
                self.render()
            time.sleep(0.01)


if __name__ == "__main__":
    print(f"[cliente{ID}] ESP32 en UDP {PUERTO_ESP32} -> servidor {SERVIDOR}", flush=True)
    Cliente().correr()
