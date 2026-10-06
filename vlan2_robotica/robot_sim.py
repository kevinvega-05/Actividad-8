"""
SIMULACIÓN ROBÓTICA REAL-TO-SIM  (VLAN 2 - Zona de Simulación Robótica)

Un contenedor por robot, cada uno con su propia ESP32 maestra:
    ROBOT=spot    10.20.20.21   ESP32 -> UDP 5011   (modelo Rex de rex-gym, tipo Spot)
    ROBOT=pepper  10.20.20.22   ESP32 -> UDP 5012   (Pepper de SoftBank vía qibullet)
    ROBOT=nao     10.20.20.23   ESP32 -> UDP 5013   (NAO de SoftBank vía qibullet)

Real-to-sim: lo que el estudiante hace con los mandos físicos se replica en
el robot simulado en tiempo real:
    POT1 = avanzar / retroceder      POT2 = girar
    B1   = saludar                   B2   = cambiar postura (sentarse/agacharse)  B3 = reiniciar

La base se mueve con una restricción fija (JOINT_FIXED) que se desplaza según
la velocidad comandada, y las articulaciones animan la marcha y los gestos con
control de posición (motores de PyBullet).
"""
import math
import os
import socket
import sys
import threading
import time

import pybullet as p
import pybullet_data

sys.path.insert(0, "/app")
from enlace_esp32 import EnlaceESP32          # noqa: E402
from render_remoto import RenderRemoto        # noqa: E402
from telemetria import Telemetria             # noqa: E402
from visor_web import VisorWeb                # noqa: E402

ROBOT = os.environ.get("ROBOT", "spot").lower()
PUERTO_ESP32 = int(os.environ.get("PUERTO_ESP32", {"spot": "5011", "pepper": "5012", "nao": "5013"}[ROBOT]))
PRUEBA_AISLAMIENTO = os.environ.get("PRUEBA_AISLAMIENTO", "10.10.10.10:8080")
REX_URDF = os.environ.get("REX_URDF", "/app/modelos/rex/urdf/rex.urdf")
ZONA_MUERTA = 60


def zona_muerta(v):
    return 0.0 if abs(v) < ZONA_MUERTA else (v - math.copysign(ZONA_MUERTA, v)) / (1000 - ZONA_MUERTA)


# ===========================================================================
#  Robots
# ===========================================================================
class RobotBase:
    nombre = ""
    v_max = 0.3        # m/s
    w_max = 0.9        # rad/s
    z_base = 0.0
    yaw_modelo = 0.0   # rotación del URDF respecto a "adelante" (+x)
    dt = 1.0 / 240.0   # paso de física
    base_estatica = False

    def __init__(self, cid):
        self.cid = cid
        self.x, self.y, self.yaw = 0.0, 0.0, 0.0
        self.v = self.w = 0.0
        self.fase = 0.0
        self.gesto = None          # (nombre, t_inicio)
        self.postura_baja = False

    # --- base cinemática ---
    def mover_base(self, v_obj, w_obj, dt):
        # rampa suave (evita tirones si el potenciómetro salta)
        self.v += max(-1.5 * dt, min(1.5 * dt, v_obj - self.v))
        self.w += max(-3.0 * dt, min(3.0 * dt, w_obj - self.w))
        if self.postura_baja:
            self.v = self.w = 0.0
        self.yaw += self.w * dt
        self.x += self.v * math.cos(self.yaw) * dt
        self.y += self.v * math.sin(self.yaw) * dt
        # arena de 10 x 10 m
        self.x = max(-4.5, min(4.5, self.x))
        self.y = max(-4.5, min(4.5, self.y))
        q = p.getQuaternionFromEuler([0, self.pitch_actual(), self.yaw + self.yaw_modelo])
        if self.base_estatica:
            # base de masa 0 (Pepper en qibullet): se reubica directamente, como hace qibullet
            p.resetBasePositionAndOrientation(self.body, [self.x, self.y, self.z_actual()], q,
                                              physicsClientId=self.cid)
        else:
            p.changeConstraint(self.restriccion, jointChildPivot=[self.x, self.y, self.z_actual()],
                               jointChildFrameOrientation=q, maxForce=self.fuerza_base, physicsClientId=self.cid)
        rapidez = abs(self.v) / self.v_max + 0.5 * abs(self.w) / self.w_max
        self.fase += 2 * math.pi * self.frec_paso * min(1.0, rapidez) * dt
        return rapidez

    def z_actual(self):
        return self.z_base

    def pitch_actual(self):
        return 0.0

    def reiniciar(self):
        self.x = self.y = self.yaw = self.v = self.w = 0.0
        self.gesto = None
        self.postura_baja = False

    def saludar(self, t):
        self.gesto = ("saludo", t)

    def cambiar_postura(self):
        self.postura_baja = not self.postura_baja

    def posicion(self):
        pos, _ = p.getBasePositionAndOrientation(self.body, physicsClientId=self.cid)
        return pos


class Spot(RobotBase):
    """Cuadrúpedo Rex (rex-gym), equivalente a un Spot pequeño. Marcha al trote."""
    nombre = "Spot"
    v_max = 0.45
    w_max = 1.0
    frec_paso = 2.2
    fuerza_base = 400
    z_base = 0.21
    yaw_modelo = math.pi       # el URDF de Rex mira hacia -x
    PATAS = ("front_left", "front_right", "rear_left", "rear_right")
    PARADO = (0.0, -0.886, 1.302)          # rex_constants.INIT_POSES['stand']
    SENTADO_TRASERAS = (0.0, -1.45, 2.30)

    def __init__(self, cid):
        super().__init__(cid)
        self.body = p.loadURDF(REX_URDF, [0, 0, self.z_base], useFixedBase=False,
                               flags=p.URDF_USE_SELF_COLLISION_EXCLUDE_PARENT, physicsClientId=cid)
        self.idx = {}
        for j in range(p.getNumJoints(self.body, physicsClientId=cid)):
            self.idx[p.getJointInfo(self.body, j, physicsClientId=cid)[1].decode()] = j
        self.motores = {pata: (self.idx[f"motor_{pata}_shoulder"], self.idx[f"motor_{pata}_leg"],
                               self.idx[f"foot_motor_{pata}"]) for pata in self.PATAS}
        for pata in self.PATAS:
            for j, a in zip(self.motores[pata], self.PARADO):
                p.resetJointState(self.body, j, a, physicsClientId=cid)
        self.restriccion = p.createConstraint(self.body, -1, -1, -1, p.JOINT_FIXED, [0, 0, 0], [0, 0, 0],
                                              [0, 0, self.z_base], physicsClientId=cid)

    def z_actual(self):
        return self.z_base - (0.03 if self.postura_baja else 0.0)

    def pitch_actual(self):
        return 0.32 if self.postura_baja else 0.0      # sentado: nariz arriba

    def articulaciones(self, rapidez, t):
        objetivos = {}
        for i, pata in enumerate(self.PATAS):
            hombro, pierna, pie = self.PARADO
            # trote: patas diagonales en fase (FL+RR) y (FR+RL)
            diag = 0.0 if pata in ("front_left", "rear_right") else math.pi
            s = math.sin(self.fase + diag)
            a = 0.35 * min(1.0, rapidez)
            pierna += a * s
            pie += -0.6 * min(1.0, rapidez) * max(0.0, math.cos(self.fase + diag))   # levanta el pie
            # giro: abre/cierra hombros
            hombro += 0.12 * self.w / self.w_max * (1 if "left" in pata else -1)
            if self.postura_baja and pata.startswith("rear"):
                hombro, pierna, pie = self.SENTADO_TRASERAS
            objetivos[pata] = [hombro, pierna, pie]
        if self.gesto and self.gesto[0] == "saludo":
            dt = t - self.gesto[1]
            if dt < 2.5:     # "da la pata": levanta la delantera derecha y la agita
                objetivos["front_right"] = [0.0, -2.0 + 0.25 * math.sin(dt * 9), 0.8]
            else:
                self.gesto = None
        for pata in self.PATAS:
            p.setJointMotorControlArray(self.body, list(self.motores[pata]), p.POSITION_CONTROL,
                                        targetPositions=objetivos[pata], forces=[25] * 3,
                                        physicsClientId=self.cid)


class _QiRobot(RobotBase):
    """Base para Pepper y NAO usando los modelos de qibullet (los de humanoid-gym)."""

    def _preparar(self):
        self.jid = {n: j.getIndex() for n, j in self.robot.joint_dict.items()}
        self.jmax = {n: j.getMaxEffort() for n, j in self.robot.joint_dict.items()}

    def poner(self, angulos, velocidad=0.6):
        nombres = [n for n in angulos if n in self.jid]
        self.robot.setAngles(nombres, [angulos[n] for n in nombres], [velocidad] * len(nombres))


class Pepper(_QiRobot):
    nombre = "Pepper"
    v_max = 0.35
    w_max = 0.9
    frec_paso = 0.6
    fuerza_base = 3000
    z_base = 0.0
    dt = 1.0 / 120.0   # modelo pesado (muchas mallas): 120 Hz basta, la base es cinemática
    base_estatica = True   # en qibullet la base de Pepper tiene masa 0

    def __init__(self, cid):
        super().__init__(cid)
        from qibullet import PepperVirtual
        from qibullet.robot_posture import PepperPosture
        self.robot = PepperVirtual()
        self.robot.loadRobot([0, 0, 0], [0, 0, 0, 1], physicsClientId=cid)
        self.body = self.robot.getRobotModel()
        p.removeConstraint(self.robot.motion_constraint, physicsClientId=cid)
        self._preparar()
        pose = PepperPosture("Stand")
        self.parado = dict(zip(pose.joint_names, pose.joint_values))
        self.poner(self.parado, 1.0)

    def articulaciones(self, rapidez, t):
        ang = dict(self.parado)
        # brazos acompañan el desplazamiento y la cabeza mira hacia donde gira
        ang["LShoulderPitch"] = self.parado["LShoulderPitch"] + 0.15 * math.sin(self.fase) * min(1, rapidez)
        ang["RShoulderPitch"] = self.parado["RShoulderPitch"] - 0.15 * math.sin(self.fase) * min(1, rapidez)
        ang["HeadYaw"] = 0.5 * self.w / self.w_max
        ang["HipPitch"] = -0.08 * self.v / self.v_max
        if self.postura_baja:          # "abrazo": ambos brazos al frente y abiertos
            ang.update(LShoulderPitch=0.1, RShoulderPitch=0.1, LShoulderRoll=0.6, RShoulderRoll=-0.6,
                       LElbowRoll=-0.5, RElbowRoll=0.5, HeadPitch=-0.2)
        if self.gesto and self.gesto[0] == "saludo":
            dt = t - self.gesto[1]
            if dt < 3.0:
                ang.update(RShoulderPitch=-1.2, RShoulderRoll=-0.35, RElbowYaw=1.4,
                           RElbowRoll=0.5 + 0.45 * math.sin(dt * 8), RWristYaw=0.0, RHand=1.0,
                           HeadYaw=-0.3, HeadPitch=-0.15)
            else:
                self.gesto = None
        self.poner(ang, 0.5)


class Nao(_QiRobot):
    nombre = "NAO"
    v_max = 0.15
    w_max = 0.7
    frec_paso = 1.6
    fuerza_base = 1500
    z_base = 0.36
    dt = 1.0 / 120.0

    def __init__(self, cid):
        super().__init__(cid)
        from qibullet import NaoVirtual
        from qibullet.robot_posture import NaoPosture
        self.robot = NaoVirtual()
        self.robot.loadRobot([0, 0, 0], [0, 0, 0, 1], physicsClientId=cid)
        self.body = self.robot.getRobotModel()
        # qibullet usa una restricción de equilibrio solo al cargarlo y luego la quita:
        # se crea una propia para guiar la base según la ESP32
        self.restriccion = p.createConstraint(self.body, -1, -1, -1, p.JOINT_FIXED, [0, 0, 0], [0, 0, 0],
                                              [0, 0, self.z_base], physicsClientId=cid)
        self._preparar()
        self.parado = dict(zip(NaoPosture("Stand").joint_names, NaoPosture("Stand").joint_values))
        self.agachado = dict(zip(NaoPosture("Crouch").joint_names, NaoPosture("Crouch").joint_values))
        self.poner(self.parado, 1.0)

    def z_actual(self):
        return self.z_base - (0.07 if self.postura_baja else 0.0)

    def articulaciones(self, rapidez, t):
        if self.postura_baja:
            ang = dict(self.agachado)
        else:
            ang = dict(self.parado)
            k = min(1.0, rapidez)
            s = math.sin(self.fase)
            # marcha: cadera/rodilla/tobillo alternados y brazos en contrafase
            for lado, signo in (("L", 1), ("R", -1)):
                paso = signo * s
                ang[f"{lado}HipPitch"] = self.parado[f"{lado}HipPitch"] - 0.30 * k * paso
                ang[f"{lado}KneePitch"] = self.parado[f"{lado}KneePitch"] + 0.45 * k * max(0.0, paso)
                ang[f"{lado}AnklePitch"] = self.parado[f"{lado}AnklePitch"] - 0.20 * k * max(0.0, paso)
                ang[f"{lado}ShoulderPitch"] = self.parado[f"{lado}ShoulderPitch"] + 0.35 * k * paso
            ang["HeadYaw"] = 0.4 * self.w / self.w_max
        if self.gesto and self.gesto[0] == "saludo":
            dt = t - self.gesto[1]
            if dt < 3.0:
                ang.update(RShoulderPitch=-1.1, RShoulderRoll=-0.4, RElbowYaw=1.3,
                           RElbowRoll=0.6 + 0.5 * math.sin(dt * 8), RWristYaw=0.0, RHand=1.0)
            else:
                self.gesto = None
        self.poner(ang, 0.6)


# ===========================================================================
#  Escena y contenedor
# ===========================================================================
INFO = {
    "spot": ("Spot · cuadrúpedo", "#e6c229", Spot,
             {"b1": "Saludar", "b2": "Sentarse / pararse", "b3": "Reiniciar"}),
    "pepper": ("Pepper · humanoide social", "#c9d1dc", Pepper,
               {"b1": "Saludar", "b2": "Abrazo / normal", "b3": "Reiniciar"}),
    "nao": ("NAO · humanoide bípedo", "#6aa7ff", Nao,
            {"b1": "Saludar", "b2": "Agacharse / pararse", "b3": "Reiniciar"}),
}
IP = {"spot": "10.20.20.21", "pepper": "10.20.20.22", "nao": "10.20.20.23"}


def construir_arena(cid):
    p.setAdditionalSearchPath(pybullet_data.getDataPath(), physicsClientId=cid)
    plano = p.loadURDF("plane.urdf", physicsClientId=cid)
    p.changeVisualShape(plano, -1, rgbaColor=(0.85, 0.88, 0.95, 1), physicsClientId=cid)

    def caja(med, pos, color):
        vis = p.createVisualShape(p.GEOM_BOX, halfExtents=med, rgbaColor=color, physicsClientId=cid)
        col = p.createCollisionShape(p.GEOM_BOX, halfExtents=med, physicsClientId=cid)
        p.createMultiBody(0, col, vis, pos, physicsClientId=cid)

    # muros bajos de la arena 10x10 y algunos obstáculos/conos de referencia
    for sx, sy, px, py in ((5.1, 0.1, 0, 5.1), (5.1, 0.1, 0, -5.1), (0.1, 5.1, 5.1, 0), (0.1, 5.1, -5.1, 0)):
        caja([sx, sy, 0.15], [px, py, 0.15], (0.25, 0.30, 0.42, 1))
    for px, py, color in ((2, 1.5, (0.95, 0.45, 0.1, 1)), (-1.8, 2.2, (0.2, 0.7, 0.4, 1)),
                          (1.5, -2.5, (0.85, 0.2, 0.3, 1)), (-2.5, -1.5, (0.3, 0.5, 0.95, 1))):
        caja([0.15, 0.15, 0.15], [px, py, 0.15], color)


_ESCENAS = []      # mantiene vivas las referencias de qibullet


def construir_escena(nombre):
    """Arena + robot. La usan la simulación y el proceso de render (mismos ids de cuerpos)."""
    from qibullet import SimulationManager
    sm = SimulationManager()
    cid = sm.launchSimulation(gui=False, auto_step=False)
    p.setGravity(0, 0, -9.81, physicsClientId=cid)
    construir_arena(cid)
    robot = INFO[nombre][2](cid)
    p.setTimeStep(robot.dt, physicsClientId=cid)
    _ESCENAS.append((sm, robot))
    return cid, {"robot": robot.body}


class Contenedor:
    def __init__(self):
        titulo, color, Clase, botones = INFO[ROBOT]
        self.cid, _ = construir_escena(ROBOT)
        self.robot = _ESCENAS[-1][1]
        self.render_proc = RenderRemoto(construir_escena, (ROBOT,), 480, 360, 55, 40)
        self.enlace = EnlaceESP32(PUERTO_ESP32, ROBOT)
        self.tele = Telemetria(ROBOT, "vlan2")
        self.fps = 0.0
        self.aislamiento = {"destino": PRUEBA_AISLAMIENTO, "bloqueado": None, "t": None}
        threading.Thread(target=self._prueba_aislamiento, daemon=True).start()
        nombres = {"a1": "Avance", "a1c": True, "a2": "Giro", "a2c": True}
        nombres.update(botones)
        self.visor = VisorWeb(titulo, f"Real-to-sim · contenedor {IP[ROBOT]} · ESP32 en UDP {PUERTO_ESP32}",
                              "VLAN 2", color, nombres, self.estado_web, self.salud)
        self.cam_yaw = 0.0

    def _prueba_aislamiento(self):
        host, puerto = PRUEBA_AISLAMIENTO.split(":")
        while True:
            try:
                socket.create_connection((host, int(puerto)), timeout=1.5).close()
                bloqueado = False
            except OSError:
                bloqueado = True
            self.aislamiento = {"destino": PRUEBA_AISLAMIENTO, "bloqueado": bloqueado, "t": time.time()}
            time.sleep(10)

    def estado_web(self):
        r = self.robot
        a = self.aislamiento
        iso = "verificando…" if a["bloqueado"] is None else ("BLOQUEADO ✓" if a["bloqueado"] else "ABIERTO ✗")
        accion = "saludando" if r.gesto else ("postura baja" if r.postura_baja else
                                               ("caminando" if abs(r.v) > 0.02 or abs(r.w) > 0.05 else "quieto"))
        return {"titulo_hud": r.nombre, "grande": accion,
                "hud": {"Velocidad": f"{r.v:+.2f} m/s", "Giro": f"{math.degrees(r.w):+.0f} °/s",
                        "Posición": f"({r.x:+.2f}, {r.y:+.2f}) m", "Rumbo": f"{math.degrees(r.yaw) % 360:.0f}°",
                        "Física": f"{self.fps:.0f} pasos/s ({1 / self.robot.dt:.0f} Hz)", "Prueba VLAN2 → VLAN1": iso},
                "ctrl": self.enlace.controles(), "enlace": self.enlace.metricas()}

    def salud(self):
        return {"ok": True, "nombre": ROBOT, "fps_fisica": round(self.fps), "esp32": self.enlace.metricas(),
                "aislamiento": self.aislamiento}

    def pedir_render(self):
        r = self.robot
        bx, by, _ = r.posicion()
        # vista 3/4 frontal-derecha que sigue suavemente al robot (se ven la cara y los gestos)
        self.cam_yaw += ((r.yaw - self.cam_yaw + math.pi) % (2 * math.pi) - math.pi) * 0.3
        dist, alto, alto_ojo = {"spot": (0.85, 0.16, 0.38), "pepper": (1.9, 0.8, 1.25),
                                "nao": (1.3, 0.30, 0.62)}[ROBOT]
        ang = self.cam_yaw - 0.75
        ojo = [bx + dist * math.cos(ang), by + dist * math.sin(ang), alto_ojo]
        self.render_proc.pedir(self.cid, [r.body], ojo, [bx, by, alto])

    def correr(self):
        t0 = time.time()
        sim_t = 0.0
        ult_tel = ult_frame = 0.0
        pasos, t_fps = 0, time.time()
        rapidez = 0.0
        while True:
            ahora = time.time() - t0
            ctl = self.enlace.controles()
            if self.enlace.pulsacion("b1"):
                self.robot.saludar(ahora)
            if self.enlace.pulsacion("b2"):
                self.robot.cambiar_postura()
            if self.enlace.pulsacion("b3"):
                self.robot.reiniciar()
            if ctl.get("auto"):     # solo lo usa el simulador de ESP32: paseo en círculos
                v_obj = 0.6 * self.robot.v_max
                w_obj = 0.35 * self.robot.w_max
            else:
                v_obj = zona_muerta(ctl["a1"]) * self.robot.v_max
                w_obj = -zona_muerta(ctl["a2"]) * self.robot.w_max     # pote a la derecha = girar a la derecha
            n = 0
            dt = self.robot.dt
            while sim_t < ahora and n < 24:
                rapidez = self.robot.mover_base(v_obj, w_obj, dt)
                p.stepSimulation(physicsClientId=self.cid)
                sim_t += dt
                n += 1
                pasos += 1
            if sim_t < ahora - 0.5:
                sim_t = ahora
            self.robot.articulaciones(rapidez, ahora)
            self.enlace.estado_txt = ("SALUDO" if self.robot.gesto else
                                      "BAJO" if self.robot.postura_baja else "OK")
            if time.time() - t_fps >= 1.0:
                self.fps = pasos / (time.time() - t_fps)
                pasos, t_fps = 0, time.time()
            if ahora - ult_tel >= 1.0:
                ult_tel = ahora
                r = self.robot
                self.tele.publicar({"fps": round(self.fps), "esp32": self.enlace.metricas(),
                                    "aislamiento": self.aislamiento,
                                    "robot": {"x": round(r.x, 2), "y": round(r.y, 2), "v": round(r.v, 2),
                                              "w": round(r.w, 2), "gesto": bool(r.gesto),
                                              "postura_baja": r.postura_baja}})
            jpeg = self.render_proc.recoger()
            if jpeg:
                self.visor.guardar_jpeg(jpeg)
            if self.visor.hay_demanda() and ahora - ult_frame >= 1 / 15:
                ult_frame = ahora
                self.pedir_render()
            time.sleep(0.002)


if __name__ == "__main__":
    print(f"[{ROBOT}] ESP32 en UDP {PUERTO_ESP32}, web en :8080", flush=True)
    Contenedor().correr()
