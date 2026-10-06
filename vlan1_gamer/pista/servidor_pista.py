"""
SERVIDOR DE PISTA  (VLAN 1 - Zona Gamer Multijugador, 10.10.10.10)

Servidor autoritativo: es el único que corre la física de PyBullet.
  - 3 carros de jugadores (los manejan los contenedores cliente 1..3)
  - 1 carro bot que siempre da vueltas (el 4.º carro del diagrama)

Red interna de la VLAN 1 (UDP 6000):
  cliente -> servidor   IN,<id>,<acel>,<giro>,<turbo>,<reversa>,<reaparecer>,<auto>,<seq>
  servidor -> clientes  JSON con el estado de los 4 carros (30 Hz)
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
from telemetria import Telemetria             # noqa: E402
from render_remoto import RenderRemoto        # noqa: E402
from visor_web import VisorWeb                # noqa: E402

PUERTO_JUEGO = int(os.environ.get("PUERTO_JUEGO", "6000"))
PRUEBA_AISLAMIENTO = os.environ.get("PRUEBA_AISLAMIENTO", "10.20.20.21:8080")
DT = 1.0 / 240.0
VEL_MAX = 55.0           # rad/s de rueda (~4.4 m/s con el carro escalado)
FUERZA = 40.0
GIRO_MAX = 0.55          # rad


class Carro:
    def __init__(self, cid, id_carro):
        self.id = id_carro
        self.cid = cid
        self.body = pc.crear_carro(cid, id_carro)
        self.mando = {"acel": 0.0, "giro": 0.0, "turbo": 0, "reversa": 0, "auto": 0}
        self.vueltas = 0
        self.t_vuelta = None          # inicio de la vuelta actual
        self.ultima = None
        self.mejor = None
        self.s_prev = None
        self.dist_total = 0.0         # para ordenar la carrera
        self.dist_cruce = None        # distancia recorrida al cruzar la meta la última vez
        self.volcado_desde = None
        self.giro_actual = 0.0
        self.vel = 0.0
        self.atascado_desde = None

    def reaparecer(self):
        pos, q = pc.posicion_salida(self.id)
        p.resetBasePositionAndOrientation(self.body, pos, q, physicsClientId=self.cid)
        p.resetBaseVelocity(self.body, [0, 0, 0], [0, 0, 0], physicsClientId=self.cid)
        self.s_prev = None
        self.t_vuelta = None
        self.dist_cruce = None
        self.dist_total = self.vueltas * pc.PERIMETRO

    CARRIL = {1: -0.7, 2: 0.7, 3: -0.2, 4: 0.3}     # cada carro tiene su propia línea

    def piloto_automatico(self, acel=0.7):
        """Persecución pura sobre una línea paralela a la central (un carril por carro)."""
        (x, y, _), q = p.getBasePositionAndOrientation(self.body, physicsClientId=self.cid)
        yaw = p.getEulerFromQuaternion(q)[2]
        s = pc.progreso(x, y)
        tx, ty, rumbo = pc.punto(s + 2.6)
        d = self.CARRIL[self.id]
        tx, ty = tx - math.sin(rumbo) * d, ty + math.cos(rumbo) * d
        err = math.atan2(ty - y, tx - x) - yaw
        err = (err + math.pi) % (2 * math.pi) - math.pi
        return {"acel": acel, "giro": max(-1, min(1, err / GIRO_MAX)), "turbo": 0, "reversa": 0}

    def aplicar(self, t):
        m = self.piloto_automatico(0.62 if self.id == 4 else 0.75) if (self.id == 4 or self.mando["auto"]) else self.mando
        objetivo = VEL_MAX * m["acel"] * (1.5 if m.get("turbo") else 1.0)
        if m.get("reversa"):
            objetivo = -VEL_MAX * 0.4
        for r in pc.RUEDAS:
            p.setJointMotorControl2(self.body, r, p.VELOCITY_CONTROL, targetVelocity=objetivo,
                                    force=FUERZA, physicsClientId=self.cid)
        self.giro_actual += (m["giro"] * GIRO_MAX - self.giro_actual) * 0.25
        for d in pc.DIRECCION:
            p.setJointMotorControl2(self.body, d, p.POSITION_CONTROL, targetPosition=self.giro_actual,
                                    physicsClientId=self.cid)

    def actualizar_carrera(self, t):
        (x, y, z), q = p.getBasePositionAndOrientation(self.body, physicsClientId=self.cid)
        v, _ = p.getBaseVelocity(self.body, physicsClientId=self.cid)
        self.vel = math.hypot(v[0], v[1])
        s = pc.progreso(x, y)
        if self.s_prev is not None:
            ds = s - self.s_prev
            if ds < -pc.PERIMETRO / 2:
                ds += pc.PERIMETRO
            elif ds > pc.PERIMETRO / 2:
                ds -= pc.PERIMETRO
            self.dist_total += ds
            linea = pc.RECTA / 2               # la meta está en s = RECTA/2
            # ¿la meta quedó dentro del tramo recorrido hacia adelante (con vuelta de s)?
            if 0 < ds < 3:
                if self.s_prev <= s:
                    cruzo = self.s_prev < linea <= s
                else:
                    cruzo = linea > self.s_prev or linea <= s
            else:
                cruzo = False
            if cruzo:
                # la primera vez que cruza solo arranca el cronómetro; luego, una vuelta
                # cuenta solo si de verdad recorrió casi toda la pista (evita que un carro
                # que va y viene sobre la meta sume vueltas falsas)
                if self.t_vuelta is None or self.dist_cruce is None:
                    self.t_vuelta, self.dist_cruce = t, self.dist_total
                elif self.dist_total - self.dist_cruce > 0.8 * pc.PERIMETRO:
                    self.vueltas += 1
                    self.ultima = t - self.t_vuelta
                    if self.mejor is None or self.ultima < self.mejor:
                        self.mejor = self.ultima
                    self.t_vuelta, self.dist_cruce = t, self.dist_total
        self.s_prev = s
        # piloto automático atascado (choque) más de 3 s: reaparece en su puesto
        auto = self.id == 4 or self.mando.get("auto")
        if auto and self.vel < 0.25:
            self.atascado_desde = self.atascado_desde or t
            if t - self.atascado_desde > 3.0:
                self.reaparecer()
                self.atascado_desde = None
        else:
            self.atascado_desde = None
        # volcado: si está de cabeza 2 s, reaparece solo
        up = p.getMatrixFromQuaternion(q)[8]
        if up < 0.3 or z < -1:
            self.volcado_desde = self.volcado_desde or t
            if t - self.volcado_desde > 2.0:
                self.reaparecer()
                self.volcado_desde = None
        else:
            self.volcado_desde = None

    def snapshot(self):
        pos, q = p.getBasePositionAndOrientation(self.body, physicsClientId=self.cid)
        return {"id": self.id, "pos": [round(v, 4) for v in pos], "q": [round(v, 5) for v in q],
                "giro": round(self.giro_actual, 3), "vel": round(self.vel, 2),
                "vueltas": self.vueltas, "ultima": self.ultima, "mejor": self.mejor,
                "dist": round(self.dist_total, 2), "auto": int(self.id == 4 or bool(self.mando["auto"]))}


class Servidor:
    def __init__(self):
        self.cid = p.connect(p.DIRECT)
        p.setGravity(0, 0, -9.81, physicsClientId=self.cid)
        p.setTimeStep(DT, physicsClientId=self.cid)
        pc.construir_pista(self.cid)
        self.carros = {i: Carro(self.cid, i) for i in (1, 2, 3, 4)}
        self.lock = threading.Lock()
        self.clientes = {}            # id -> {"addr", "ultimo", "seq", "perdidos", "jitter", ...}
        self.aislamiento = {"destino": PRUEBA_AISLAMIENTO, "bloqueado": None, "t": None}
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", PUERTO_JUEGO))
        threading.Thread(target=self._rx, daemon=True).start()
        threading.Thread(target=self._prueba_aislamiento, daemon=True).start()
        self.tele = Telemetria("pista", "vlan1")
        self.fps = 0.0
        self.visor = VisorWeb(
            "Servidor de pista", "Física autoritativa · 3 jugadores + 1 bot · 10.10.10.10",
            "VLAN 1", "#4d8cf2", {}, self.estado_web, self.salud)
        # mismo orden de carga que la escena de render => mismos ids de cuerpos
        self.render = RenderRemoto(pc.escena_render, (), 640, 480, 52, 100)
        self.ojo, self.objetivo = [0, -11.7, 15.6], [0, -0.8, 0]       # vista general desde la tribuna

    # ---------------- red interna VLAN 1 ----------------
    def _rx(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(512)
            except OSError:
                continue
            ahora = time.time()
            f = data.decode(errors="ignore").split(",")
            if len(f) < 9 or f[0] != "IN":
                continue
            try:
                cid = int(f[1])
                acel, giro = float(f[2]), float(f[3])
                turbo, rev, reap, auto, seq = (int(x) for x in f[4:9])
            except ValueError:
                continue
            if cid not in (1, 2, 3):
                continue
            with self.lock:
                c = self.clientes.setdefault(cid, {"perdidos": 0, "recibidos": 0, "jitter": 0.0})
                if "seq" in c and 1 < seq - c["seq"] < 1000:
                    c["perdidos"] += seq - c["seq"] - 1
                if "ultimo" in c:   # jitter de inter-llegada (el cliente envía cada 20 ms)
                    d = abs((ahora - c["ultimo"]) * 1000 - 20.0)
                    c["jitter"] += (d - c["jitter"]) / 16
                c.update(addr=addr, ultimo=ahora, seq=seq, recibidos=c["recibidos"] + 1)
                car = self.carros[cid]
                car.mando = {"acel": max(0.0, min(1.0, acel)), "giro": max(-1.0, min(1.0, giro)),
                             "turbo": turbo, "reversa": rev, "auto": auto}
                if reap:
                    car.pedir_reaparecer = True

    def _tx(self, t):
        snap = json.dumps({"t": round(t, 3), "carros": [c.snapshot() for c in self.carros.values()]}).encode()
        with self.lock:
            destinos = [c["addr"] for c in self.clientes.values() if time.time() - c["ultimo"] < 2]
        for a in destinos:
            try:
                self.sock.sendto(snap, a)
            except OSError:
                pass

    def _prueba_aislamiento(self):
        """Cada 10 s intenta abrir TCP hacia la VLAN 2. Debe FALLAR (lo bloquea el enrutador)."""
        host, puerto = PRUEBA_AISLAMIENTO.split(":")
        while True:
            try:
                s = socket.create_connection((host, int(puerto)), timeout=1.5)
                s.close()
                bloqueado = False
            except OSError:
                bloqueado = True
            self.aislamiento = {"destino": PRUEBA_AISLAMIENTO, "bloqueado": bloqueado, "t": time.time()}
            time.sleep(10)

    # ---------------- web / salud ----------------
    def ranking(self):
        return sorted(self.carros.values(), key=lambda c: -c.dist_total)

    def estado_web(self):
        filas = {}
        for i, c in enumerate(self.ranking(), 1):
            mejor = f"{c.mejor:.2f} s" if c.mejor else "—"
            filas[f"{i}. {pc.NOMBRES[c.id]}"] = f"V{c.vueltas} · mejor {mejor} · {c.vel * 3.6:.0f} km/h"
        with self.lock:
            conectados = sum(1 for c in self.clientes.values() if time.time() - c["ultimo"] < 2)
        a = self.aislamiento
        iso = "verificando…" if a["bloqueado"] is None else ("BLOQUEADO ✓" if a["bloqueado"] else "ABIERTO ✗")
        filas["Clientes conectados"] = f"{conectados} / 3"
        filas["Prueba VLAN1 → VLAN2"] = iso
        filas["Física"] = f"{self.fps:.0f} pasos/s"
        return {"titulo_hud": "Clasificación", "hud": filas, "pill": "Servidor autoritativo"}

    def salud(self):
        with self.lock:
            cli = {str(k): {"conectado": time.time() - v["ultimo"] < 2, "jitter_ms": round(v["jitter"], 2),
                            "perdidos": v["perdidos"], "recibidos": v["recibidos"]}
                   for k, v in self.clientes.items()}
        return {"ok": True, "nombre": "pista", "fps_fisica": round(self.fps), "clientes": cli,
                "aislamiento": self.aislamiento}

    # ---------------- bucle principal ----------------
    def correr(self):
        t0 = time.time()
        sim_t = 0.0
        ult_tx = ult_tel = ult_frame = 0.0
        pasos, t_fps = 0, time.time()
        while True:
            ahora = time.time() - t0
            # física en tiempo real (hasta 24 pasos por iteración para ponerse al día)
            n = 0
            while sim_t < ahora and n < 24:
                for c in self.carros.values():
                    if getattr(c, "pedir_reaparecer", False):
                        c.pedir_reaparecer = False
                        c.reaparecer()
                    c.aplicar(sim_t)
                p.stepSimulation(physicsClientId=self.cid)
                sim_t += DT
                n += 1
                pasos += 1
            if sim_t < ahora - 0.5:
                sim_t = ahora              # si la máquina va lenta, no acumular retraso
            for c in self.carros.values():
                c.actualizar_carrera(ahora)
            if ahora - ult_tx >= 1 / 30:
                ult_tx = ahora
                self._tx(ahora)
            if time.time() - t_fps >= 1.0:
                self.fps = pasos / (time.time() - t_fps)
                pasos, t_fps = 0, time.time()
            if ahora - ult_tel >= 1.0:
                ult_tel = ahora
                s = self.salud()
                self.tele.publicar({"fps": s["fps_fisica"], "clientes": s["clientes"],
                                    "aislamiento": self.aislamiento,
                                    "carrera": [c.snapshot() for c in self.ranking()]})
            jpeg = self.render.recoger()
            if jpeg:
                self.visor.guardar_jpeg(jpeg)
            if self.visor.hay_demanda() and ahora - ult_frame >= 1 / 15:
                ult_frame = ahora
                self.render.pedir(self.cid, [c.body for c in self.carros.values()], self.ojo, self.objetivo)
            time.sleep(0.002)


if __name__ == "__main__":
    print(f"[pista] servidor en UDP {PUERTO_JUEGO}, web en :8080", flush=True)
    Servidor().correr()
