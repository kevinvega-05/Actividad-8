"""
MONITOR DEL PLANO DE ADMINISTRACIÓN  (VLAN 3, contenedor Alpine, 10.30.30.10)

Observa ambas zonas A TRAVÉS del enrutador inter-VLAN (sin estar conectado a ellas):

  * Sondeo activo cada segundo a cada contenedor:
      - ICMP (ping)            -> latencia de red, jitter, pérdida
      - HTTP GET /health       -> disponibilidad de la aplicación
  * Telemetría pasiva por MQTT (broker 10.30.30.20):
      - lab/telemetria/<nombre>  enlace ESP32 -> contenedor (RTT, jitter, pérdidas), estado de la sim
      - lab/router               contadores de iptables (permitido / bloqueado)
      - lab/leds/esclava         latido de la ESP32 esclava de LEDs
  * Publica:
      - lab/leds                 8 dígitos, uno por LED de la ESP32 esclava (retenido)
      - lab/estado/<nombre>      estado de cada contenedor (retenido)
  * Tablero web en :8000, API JSON en /api/estado, reporte de validación en /api/reporte
    y registro completo en /metricas.csv

Estados (y patrón del LED):
  0 CAIDO      apagado          no responde ping ni /health
  1 OK         encendido fijo   responde, sin ESP32 maestra activa
  2 ACTIVO     parpadeo rápido  responde y su ESP32 lo está controlando (o el servidor tiene clientes)
  3 DEGRADADO  parpadeo lento   responde pero con latencia/jitter/pérdida por encima del umbral
"""
import csv
import json
import os
import re
import statistics
import subprocess
import threading
import time
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paho.mqtt.client as mqtt

BROKER = os.environ.get("MQTT_BROKER", "10.30.30.20")
DATOS = os.environ.get("DIR_DATOS", "/datos")
UMBRAL_LAT_MS = float(os.environ.get("UMBRAL_LATENCIA_MS", "50"))
UMBRAL_JIT_MS = float(os.environ.get("UMBRAL_JITTER_MS", "20"))
UMBRAL_PERD = float(os.environ.get("UMBRAL_PERDIDA_PCT", "5"))
VENTANA = 60          # segundos para las métricas "recientes"

# Orden = orden de los LEDs en la ESP32 esclava
OBJETIVOS = [
    # nombre,    ip,             zona,    tiene /health
    ("pista",    "10.10.10.10", "vlan1", True),
    ("cliente1", "10.10.10.11", "vlan1", True),
    ("cliente2", "10.10.10.12", "vlan1", True),
    ("cliente3", "10.10.10.13", "vlan1", True),
    ("spot",     "10.20.20.21", "vlan2", True),
    ("pepper",   "10.20.20.22", "vlan2", True),
    ("nao",      "10.20.20.23", "vlan2", True),
    ("router",   "10.30.30.254", "vlan3", False),
]
EXTRA = [("mqtt", "10.30.30.20", "vlan3", False)]
CAIDO, OK, ACTIVO, DEGRADADO = 0, 1, 2, 3
NOMBRE_ESTADO = {CAIDO: "CAÍDO", OK: "OK", ACTIVO: "ACTIVO", DEGRADADO: "DEGRADADO"}
RE_PING = re.compile(r"time[=<]([\d.]+) ?ms")


class Objetivo:
    def __init__(self, nombre, ip, zona, health):
        self.nombre, self.ip, self.zona, self.health = nombre, ip, zona, health
        self.hist = deque(maxlen=VENTANA)       # (t, ping_ms|None, http_ms|None, ok)
        self.serie = deque(maxlen=180)          # para la gráfica del tablero
        self.jitter = 0.0                       # RFC 3550 sobre RTT consecutivos
        self._ultimo_rtt = None
        self.sondeos = 0
        self.exitos = 0
        self.fallos_seguidos = 0
        self.salud = {}
        self.telemetria = {}
        self.t_telemetria = 0.0
        self.estado = CAIDO
        self.visto = False          # la disponibilidad se cuenta desde la primera respuesta

    def registrar(self, t, ping_ms, http_ms, salud):
        ok = (ping_ms is not None) and (http_ms is not None or not self.health)
        if self.nombre == "router":        # el router no tiene web: basta ping + su telemetría
            ok = ping_ms is not None
        if not self.visto:
            if not ok:
                return                     # todavía arrancando: no cuenta como indisponibilidad
            self.visto = True
        self.sondeos += 1
        self.exitos += ok
        self.fallos_seguidos = 0 if ok else self.fallos_seguidos + 1
        if ping_ms is not None:
            if self._ultimo_rtt is not None:
                self.jitter += (abs(ping_ms - self._ultimo_rtt) - self.jitter) / 16.0
            self._ultimo_rtt = ping_ms
        if salud:
            self.salud = salud
        self.hist.append((t, ping_ms, http_ms, ok))
        self.serie.append(round(ping_ms, 3) if ping_ms is not None else None)

    def metricas(self):
        lat = [h[1] for h in self.hist if h[1] is not None]
        http = [h[2] for h in self.hist if h[2] is not None]
        n = len(self.hist)
        perdida = 100.0 * sum(1 for h in self.hist if h[1] is None) / n if n else 0.0
        disp = 100.0 * sum(1 for h in self.hist if h[3]) / n if n else 0.0
        q = sorted(lat)
        return {
            "lat_ms": round(statistics.mean(lat), 3) if lat else None,
            "lat_p95_ms": round(q[max(0, int(len(q) * 0.95) - 1)], 3) if q else None,
            "jitter_ms": round(self.jitter, 3),
            "desv_ms": round(statistics.pstdev(lat), 3) if len(lat) > 1 else 0.0,
            "http_ms": round(statistics.mean(http), 2) if http else None,
            "perdida_pct": round(perdida, 2),
            "disp_ventana_pct": round(disp, 2),
            "disp_total_pct": round(100.0 * self.exitos / self.sondeos, 3) if self.sondeos else 0.0,
            "sondeos": self.sondeos,
        }

    def esp32(self):
        """Métricas del enlace ESP32 -> contenedor que publica el propio contenedor."""
        tel = self.telemetria if time.time() - self.t_telemetria < 5 else {}
        return tel.get("esp32") or self.salud.get("esp32")

    def calcular_estado(self):
        m = self.metricas()
        if self.fallos_seguidos >= 3 or not self.hist:
            return CAIDO
        if (m["lat_p95_ms"] or 0) > UMBRAL_LAT_MS or m["jitter_ms"] > UMBRAL_JIT_MS or m["perdida_pct"] > UMBRAL_PERD:
            return DEGRADADO
        e = self.esp32()
        if e:
            if e.get("perdida_pct", 0) > UMBRAL_PERD or e.get("jitter_ms", 0) > UMBRAL_JIT_MS:
                return DEGRADADO
            if e.get("conectada"):
                return ACTIVO
        if self.nombre == "pista":
            cli = self.salud.get("clientes", {})
            if any(c.get("conectado") for c in cli.values()):
                return ACTIVO
        return OK


class Monitor:
    def __init__(self):
        self.objetivos = [Objetivo(*o) for o in OBJETIVOS]
        self.extra = [Objetivo(*o) for o in EXTRA]
        self.por_nombre = {o.nombre: o for o in self.objetivos + self.extra}
        self.router = {}
        self.t_router = 0.0
        self.esclava = {}
        self.t_esclava = 0.0
        self.eventos = deque(maxlen=40)
        self.t_inicio = time.time()
        self.lock = threading.Lock()
        os.makedirs(DATOS, exist_ok=True)
        self.csv_ruta = os.path.join(DATOS, "metricas.csv")
        nuevo = not os.path.exists(self.csv_ruta)
        self.csv_f = open(self.csv_ruta, "a", newline="")
        self.csv = csv.writer(self.csv_f)
        if nuevo:
            self.csv.writerow(["t", "contenedor", "zona", "ping_ms", "http_ms", "ok", "estado",
                               "esp32_conectada", "esp32_rtt_ms", "esp32_jitter_ms", "esp32_perdida_pct"])
        self._mqtt()

    # ------------------------------------------------------------------ MQTT
    def _mqtt(self):
        try:
            self.cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="monitor-admin")
        except AttributeError:
            self.cli = mqtt.Client(client_id="monitor-admin")
        self.mqtt_ok = False

        def al_conectar(c, *a):
            self.mqtt_ok = True
            c.subscribe([("lab/telemetria/#", 0), ("lab/router", 0), ("lab/leds/esclava", 0)])
            self.evento("Conectado al broker MQTT")

        def al_desconectar(*a):
            self.mqtt_ok = False

        def al_mensaje(c, u, msg):
            try:
                datos = json.loads(msg.payload.decode())
            except ValueError:
                return
            with self.lock:
                if msg.topic.startswith("lab/telemetria/"):
                    o = self.por_nombre.get(msg.topic.split("/")[-1])
                    if o and datos.get("vivo", True):
                        o.telemetria, o.t_telemetria = datos, time.time()
                elif msg.topic == "lab/router":
                    self.router, self.t_router = datos, time.time()
                elif msg.topic == "lab/leds/esclava":
                    if not self.esclava.get("vivo") and datos.get("vivo"):
                        self.evento("ESP32 esclava de LEDs en línea")
                    self.esclava, self.t_esclava = datos, time.time()

        self.cli.on_connect = al_conectar
        self.cli.on_disconnect = al_desconectar
        self.cli.on_message = al_mensaje

        def bucle():
            while True:
                try:
                    self.cli.connect(BROKER, 1883, keepalive=10)
                    self.cli.loop_forever(retry_first_connection=True)
                except Exception:
                    time.sleep(2)
        threading.Thread(target=bucle, daemon=True).start()

    def evento(self, txt):
        self.eventos.appendleft(f"{time.strftime('%H:%M:%S')}  {txt}")

    # ------------------------------------------------------------------ sondeo
    @staticmethod
    def ping(ip):
        try:
            r = subprocess.run(["ping", "-c", "1", "-W", "1", ip], capture_output=True, text=True, timeout=2)
            m = RE_PING.search(r.stdout)
            return float(m.group(1)) if (r.returncode == 0 and m) else None
        except (subprocess.TimeoutExpired, OSError):
            return None

    @staticmethod
    def http(ip):
        t = time.perf_counter()
        try:
            with urllib.request.urlopen(f"http://{ip}:8080/health", timeout=1.5) as resp:
                datos = json.loads(resp.read())
            return (time.perf_counter() - t) * 1000, datos
        except Exception:
            return None, None

    def _sondear(self, o):
        while True:
            t = time.time()
            ping_ms = self.ping(o.ip)
            http_ms, salud = self.http(o.ip) if o.health else (None, None)
            with self.lock:
                o.registrar(t, ping_ms, http_ms, salud)
                nuevo = o.calcular_estado()
                if nuevo != o.estado:
                    self.evento(f"{o.nombre}: {NOMBRE_ESTADO[o.estado]} → {NOMBRE_ESTADO[nuevo]}")
                    o.estado = nuevo
                e = o.esp32() or {}
                if o.visto:            # el registro empieza cuando el contenedor respondió por primera vez
                    self.csv.writerow([round(t, 3), o.nombre, o.zona, ping_ms,
                                       None if http_ms is None else round(http_ms, 2),
                                       int(o.hist[-1][3]), o.estado, int(bool(e.get("conectada"))),
                                       e.get("rtt_ms"), e.get("jitter_ms"), e.get("perdida_pct")])
            time.sleep(max(0.0, 1.0 - (time.time() - t)))

    # ------------------------------------------------------------------ salida
    def leds(self):
        return "".join(str(o.estado) for o in self.objetivos)

    def publicar(self):
        ultimo = None
        while True:
            time.sleep(0.5)
            with self.lock:
                leds = self.leds()
                estados = {o.nombre: {"estado": NOMBRE_ESTADO[o.estado], "codigo": o.estado, **o.metricas()}
                           for o in self.objetivos + self.extra}
                self.csv_f.flush()
            if self.mqtt_ok:
                self.cli.publish("lab/leds", leds, retain=True)
                if leds != ultimo:
                    for n, e in estados.items():
                        self.cli.publish(f"lab/estado/{n}", json.dumps(e), retain=True)
                    ultimo = leds

    def aislamiento(self):
        """Pruebas de aislamiento: las hacen las zonas (deben fallar) y el monitor (deben funcionar)."""
        res = []
        for nombre, origen in (("pista", "VLAN 1 → VLAN 2"), ("spot", "VLAN 2 → VLAN 1")):
            o = self.por_nombre[nombre]
            a = (o.telemetria.get("aislamiento") if time.time() - o.t_telemetria < 30 else None) \
                or o.salud.get("aislamiento") or {}
            res.append({"prueba": origen, "esperado": "bloqueado",
                        "resultado": None if a.get("bloqueado") is None else ("bloqueado" if a["bloqueado"] else "abierto"),
                        "ok": a.get("bloqueado") is True})
        for zona, nombre in (("VLAN 1", "pista"), ("VLAN 2", "spot")):
            o = self.por_nombre[nombre]
            alcanzable = bool(o.hist) and o.hist[-1][1] is not None
            res.append({"prueba": f"Administración → {zona}", "esperado": "permitido",
                        "resultado": "permitido" if alcanzable else "sin respuesta", "ok": alcanzable})
        tel_ok = [n for n in ("pista", "spot") if time.time() - self.por_nombre[n].t_telemetria < 5]
        res.append({"prueba": "Zonas → broker MQTT (telemetría)", "esperado": "permitido",
                    "resultado": "permitido" if tel_ok else "sin telemetría", "ok": bool(tel_ok)})
        return res

    def estado_api(self):
        with self.lock:
            objs = []
            for o in self.objetivos + self.extra:
                objs.append({"nombre": o.nombre, "ip": o.ip, "zona": o.zona, "estado": NOMBRE_ESTADO[o.estado],
                             "codigo": o.estado, "metricas": o.metricas(), "esp32": o.esp32(),
                             "serie": list(o.serie)})
            router = self.router if time.time() - self.t_router < 6 else {}
            esclava = dict(self.esclava, vivo=time.time() - self.t_esclava < 10) if self.esclava else {"vivo": False}
            return {"t": time.time(), "uptime_s": round(time.time() - self.t_inicio), "mqtt": self.mqtt_ok,
                    "leds": self.leds(), "objetivos": objs, "router": router, "esclava": esclava,
                    "aislamiento": self.aislamiento(), "eventos": list(self.eventos),
                    "umbrales": {"latencia_ms": UMBRAL_LAT_MS, "jitter_ms": UMBRAL_JIT_MS, "perdida_pct": UMBRAL_PERD}}

    def reporte(self):
        """Resumen de validación desde que arrancó el monitor (todo el CSV de esta sesión)."""
        filas = {}
        try:
            with open(self.csv_ruta, newline="") as f:
                for r in csv.DictReader(f):
                    if float(r["t"]) < self.t_inicio:
                        continue
                    d = filas.setdefault(r["contenedor"], {"ping": [], "ok": 0, "n": 0, "esp_rtt": [], "esp_jit": []})
                    d["n"] += 1
                    d["ok"] += int(r["ok"])
                    if r["ping_ms"] not in ("", "None"):
                        d["ping"].append(float(r["ping_ms"]))
                    if r["esp32_rtt_ms"] not in ("", "None") and r["esp32_conectada"] == "1":
                        d["esp_rtt"].append(float(r["esp32_rtt_ms"]))
                        d["esp_jit"].append(float(r["esp32_jitter_ms"] or 0))
        except FileNotFoundError:
            pass
        salida = []
        for nombre, d in filas.items():
            p = sorted(d["ping"])
            jit = statistics.mean(abs(a - b) for a, b in zip(d["ping"], d["ping"][1:])) if len(p) > 1 else 0
            e = sorted(d["esp_rtt"])
            salida.append({
                "contenedor": nombre, "muestras": d["n"],
                "lat_media_ms": round(statistics.mean(p), 3) if p else None,
                "lat_p95_ms": round(p[max(0, int(len(p) * .95) - 1)], 3) if p else None,
                "lat_max_ms": round(p[-1], 3) if p else None,
                "jitter_ms": round(jit, 3),
                "perdida_pct": round(100 * (d["n"] - len(p)) / d["n"], 2) if d["n"] else None,
                "disponibilidad_pct": round(100 * d["ok"] / d["n"], 3) if d["n"] else None,
                "esp32_rtt_medio_ms": round(statistics.mean(e), 2) if e else None,
                "esp32_rtt_p95_ms": round(e[max(0, int(len(e) * .95) - 1)], 2) if e else None,
                "esp32_jitter_medio_ms": round(statistics.mean(d["esp_jit"]), 2) if d["esp_jit"] else None,
            })
        orden = [o[0] for o in OBJETIVOS + EXTRA]
        salida.sort(key=lambda r: orden.index(r["contenedor"]) if r["contenedor"] in orden else 99)
        return {"desde": self.t_inicio, "duracion_s": round(time.time() - self.t_inicio), "filas": salida}

    def correr(self):
        for o in self.objetivos + self.extra:
            threading.Thread(target=self._sondear, args=(o,), daemon=True).start()
        threading.Thread(target=self.publicar, daemon=True).start()
        servir(self)


def servir(mon):
    base = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base, "tablero.html"), "rb") as f:
        tablero = f.read()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _env(self, cuerpo, tipo, extra=None):
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def do_GET(self):
            ruta = self.path.split("?")[0]
            try:
                if ruta == "/api/estado":
                    self._env(json.dumps(mon.estado_api()).encode(), "application/json")
                elif ruta == "/api/reporte":
                    self._env(json.dumps(mon.reporte()).encode(), "application/json")
                elif ruta == "/metricas.csv":
                    with mon.lock:
                        mon.csv_f.flush()
                    with open(mon.csv_ruta, "rb") as f:
                        self._env(f.read(), "text/csv",
                                  {"Content-Disposition": "attachment; filename=metricas.csv"})
                elif ruta == "/health":
                    self._env(b'{"ok": true}', "application/json")
                else:
                    self._env(tablero, "text/html; charset=utf-8")
            except (BrokenPipeError, ConnectionResetError):
                pass

    print("[monitor] tablero en :8000", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8000), H).serve_forever()


if __name__ == "__main__":
    Monitor().correr()
