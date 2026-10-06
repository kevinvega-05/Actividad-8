"""
Enlace UDP con una ESP32 maestra.

La ESP32 envía cada 20 ms (50 Hz):
    C,<seq>,<t_ms>,<a1>,<a2>,<b1>,<b2>,<b3>,<rtt_us>,<auto>
      seq     número de secuencia (detecta pérdidas)
      t_ms    millis() de la ESP32 (para jitter de llegada, RFC 3550)
      a1, a2  potenciómetros normalizados  -1000..1000
      b1..b3  pulsadores (1 = presionado)
      rtt_us  último RTT que midió la ESP32 (µs), -1 si aún no hay
      auto    1 = piloto automático (solo lo usa el simulador de ESP32)

El contenedor responde de inmediato (eco para medir RTT en la ESP32):
    A,<seq>,<estado>
"""
import socket
import threading
import time


class EnlaceESP32:
    def __init__(self, puerto, nombre=""):
        self.nombre = nombre
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", puerto))
        self.lock = threading.Lock()
        self.estado_txt = "OK"          # lo que se devuelve en el ACK
        self.ctrl = {"a1": 0, "a2": 0, "b1": 0, "b2": 0, "b3": 0, "auto": 0}
        self._flancos = {"b1": 0, "b2": 0, "b3": 0}   # pulsaciones pendientes
        self.ultimo_rx = 0.0
        self.origen = None
        # métricas
        self.recibidos = 0
        self.perdidos = 0
        self.ultimo_seq = None
        self.jitter_ms = 0.0            # RFC 3550
        self._transito_prev = None
        self.rtt_ms = None
        self._ventana = []              # instantes de llegada (pps)
        self._perd_ventana = []         # (instante, paquetes perdidos) de los últimos 10 s
        threading.Thread(target=self._bucle, daemon=True).start()

    def _bucle(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(512)
            except OSError:
                continue
            ahora = time.time()
            p = data.decode(errors="ignore").strip().split(",")
            if len(p) < 9 or p[0] != "C":
                continue
            try:
                seq, t_ms = int(p[1]), int(p[2])
                vals = [int(x) for x in p[3:9]]
                auto = int(p[9]) if len(p) > 9 else 0
            except ValueError:
                continue
            # ACK inmediato
            try:
                self.sock.sendto(f"A,{seq},{self.estado_txt}".encode(), addr)
            except OSError:
                pass
            with self.lock:
                self.origen = addr
                self.recibidos += 1
                if self.ultimo_seq is not None:
                    salto = seq - self.ultimo_seq
                    if 1 < salto < 1000:
                        self.perdidos += salto - 1
                        self._perd_ventana.append((ahora, salto - 1))
                    elif salto <= -1000 or salto > 1000:
                        self._transito_prev = None      # la ESP32 se reinició
                self.ultimo_seq = seq
                # jitter RFC 3550: variación del tiempo de tránsito
                transito = ahora * 1000.0 - t_ms
                if self._transito_prev is not None:
                    d = abs(transito - self._transito_prev)
                    self.jitter_ms += (d - self.jitter_ms) / 16.0
                self._transito_prev = transito
                if vals[5] >= 0:
                    self.rtt_ms = vals[5] / 1000.0
                for i, k in enumerate(("b1", "b2", "b3")):
                    if vals[2 + i] and not self.ctrl[k]:
                        self._flancos[k] += 1
                self.ctrl = {"a1": vals[0], "a2": vals[1], "b1": vals[2],
                             "b2": vals[3], "b3": vals[4], "auto": auto}
                self.ultimo_rx = ahora
                self._ventana.append(ahora)
                while self._ventana and ahora - self._ventana[0] > 1.0:
                    self._ventana.pop(0)
                while self._perd_ventana and ahora - self._perd_ventana[0][0] > 10.0:
                    self._perd_ventana.pop(0)

    # ------------------------------------------------------------------
    def conectada(self):
        return time.time() - self.ultimo_rx < 1.0

    def controles(self):
        """Últimos controles; si la ESP32 se calla > 1 s, todo vuelve a cero (seguridad)."""
        with self.lock:
            if not self.conectada():
                return {"a1": 0, "a2": 0, "b1": 0, "b2": 0, "b3": 0, "auto": 0}
            return dict(self.ctrl)

    def pulsacion(self, boton):
        """True una sola vez por cada vez que se presiona el botón (flanco de subida)."""
        with self.lock:
            if self._flancos[boton] > 0:
                self._flancos[boton] -= 1
                return True
            return False

    def metricas(self):
        with self.lock:
            # pérdida reciente (10 s): un evento pasado no deja el enlace "degradado" para siempre
            perd10 = sum(n for _, n in self._perd_ventana)
            total10 = 50 * 10 if self.recibidos > 500 else self.recibidos + perd10
            return {
                "conectada": self.conectada(),
                "origen": f"{self.origen[0]}:{self.origen[1]}" if self.origen else None,
                "pps": len(self._ventana),
                "recibidos": self.recibidos,
                "perdidos": self.perdidos,
                "perdida_pct": round(min(100.0, 100.0 * perd10 / total10), 2) if total10 else 0.0,
                "perdidos_10s": perd10,
                "jitter_ms": round(self.jitter_ms, 2),
                "rtt_ms": round(self.rtt_ms, 2) if self.rtt_ms is not None else None,
            }
