"""
SIMULADOR DE ESP32 MAESTRAS  (para probar el laboratorio sin las placas)

Envía exactamente los mismos paquetes que el firmware (50 Hz) a los puertos que
publica Docker en el PC, mide el RTT con los ACK y lo reporta igual que la ESP32.

    python herramientas/simulador_esp32.py                      # las 6 maestras, en modo demo
    python herramientas/simulador_esp32.py --roles carro1 spot  # solo algunas
    python herramientas/simulador_esp32.py --pc 192.168.1.50    # contra otro PC

Modos:
    demo  (por defecto) carros con piloto automático; robots caminan en círculo
          y cada ~12 s presionan un botón (saludo / postura)
    manual  el carro o robot hace lo que digas con el teclado (un solo rol):
            w/s = POT1 arriba/abajo, a/d = POT2, 1/2/3 = botones, q = salir
Solo usa la librería estándar de Python (3.8+).
"""
import argparse
import socket
import sys
import threading
import time

PUERTOS = {"carro1": 5001, "carro2": 5002, "carro3": 5003, "spot": 5011, "pepper": 5012, "nao": 5013}


class MaestraVirtual(threading.Thread):
    def __init__(self, rol, pc, modo):
        super().__init__(daemon=True)
        self.rol, self.destino, self.modo = rol, (pc, PUERTOS[rol]), modo
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.5)
        self.lock = threading.Lock()
        self.a1 = self.a2 = 0
        self.b = [0, 0, 0]
        self.enviados = {}
        self.rtt_us = -1
        self.rtts = []
        self.acks = 0
        self.estado = "—"
        self.t0 = time.time()

    def mandos_demo(self, t):
        if self.rol.startswith("carro"):
            return 1000, 0, [0, 0, 0], 1                     # piloto automático
        ciclo = (t + 3 * list(PUERTOS).index(self.rol)) % 12
        b = [1 if 6.0 < ciclo < 6.2 else 0, 1 if 10.0 < ciclo < 10.2 else 0, 0]
        return 0, 0, b, 1                                     # paseo en círculo + gestos

    def _recibir(self):
        """Hilo dedicado a los ACK: el RTT se mide en cuanto llega la respuesta."""
        while True:
            try:
                data, _ = self.sock.recvfrom(128)
            except (socket.timeout, OSError):
                continue
            t = time.perf_counter()
            f = data.decode(errors="ignore").split(",")
            if len(f) >= 3 and f[0] == "A":
                with self.lock:
                    t_env = self.enviados.pop(int(f[1]), None)
                    if t_env:
                        self.rtt_us = int((t - t_env) * 1e6)
                        self.rtts.append(self.rtt_us / 1000)
                        self.rtts = self.rtts[-250:]
                        self.acks += 1
                        self.estado = f[2]

    def run(self):
        threading.Thread(target=self._recibir, daemon=True).start()
        seq = 0
        siguiente = time.time()
        while True:
            t = time.time() - self.t0
            if self.modo == "demo":
                a1, a2, b, auto = self.mandos_demo(t)
            else:
                a1, a2, b, auto = self.a1, self.a2, list(self.b), 0
            seq += 1
            ms = int(time.time() * 1000) % 2_000_000_000
            msg = f"C,{seq},{ms},{a1},{a2},{b[0]},{b[1]},{b[2]},{self.rtt_us},{auto}"
            with self.lock:
                self.enviados[seq] = time.perf_counter()
                for k in [k for k in self.enviados if k < seq - 100]:
                    del self.enviados[k]
            try:
                self.sock.sendto(msg.encode(), self.destino)
            except OSError:
                pass
            siguiente += 0.02
            espera = siguiente - time.time()
            if espera > 0:
                time.sleep(espera)
            else:
                siguiente = time.time()

    def resumen(self):
        with self.lock:
            rtts = list(self.rtts)
        if not rtts:
            return f"{self.rol:7s} -> {self.destino[0]}:{self.destino[1]}  sin respuesta"
        r = sorted(rtts)
        jit = sum(abs(a - b) for a, b in zip(rtts, rtts[1:])) / max(1, len(rtts) - 1)
        return (f"{self.rol:7s} -> :{self.destino[1]}  RTT med {r[len(r) // 2]:6.2f} ms  "
                f"p95 {r[int(len(r) * .95) - 1]:6.2f} ms  jitter {jit:5.2f} ms  estado {self.estado}")


def teclado(m):
    """Control manual simple (Windows: msvcrt, Linux/Mac: tty)."""
    print("w/s = POT1 · a/d = POT2 · 1/2/3 = botones · espacio = centrar · q = salir")
    if sys.platform == "win32":
        import msvcrt
        leer = lambda: msvcrt.getwch()       # noqa: E731
    else:
        import termios
        import tty
        fd = sys.stdin.fileno()
        viejo = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        leer = lambda: sys.stdin.read(1)     # noqa: E731
    try:
        while True:
            k = leer().lower()
            if k == "q":
                return
            if k == "w":
                m.a1 = min(1000, m.a1 + 200)
            elif k == "s":
                m.a1 = max(-1000, m.a1 - 200)
            elif k == "d":
                m.a2 = min(1000, m.a2 + 250)
            elif k == "a":
                m.a2 = max(-1000, m.a2 - 250)
            elif k == " ":
                m.a1 = m.a2 = 0
            elif k in "123":
                i = int(k) - 1
                m.b[i] = 1
                threading.Timer(0.15, lambda: m.b.__setitem__(i, 0)).start()
            print(f"\rPOT1 {m.a1:+5d}  POT2 {m.a2:+5d}   {m.resumen()[:60]}   ", end="", flush=True)
    finally:
        if sys.platform != "win32":
            termios.tcsetattr(fd, termios.TCSADRAIN, viejo)


def main():
    ap = argparse.ArgumentParser(description="Simulador de las 6 ESP32 maestras")
    ap.add_argument("--pc", default="127.0.0.1", help="IP del PC con Docker (por defecto este mismo PC)")
    ap.add_argument("--roles", nargs="*", default=list(PUERTOS), choices=list(PUERTOS))
    ap.add_argument("--modo", choices=["demo", "manual"], default="demo")
    ap.add_argument("--duracion", type=float, default=0, help="segundos (0 = hasta Ctrl+C)")
    a = ap.parse_args()
    if a.modo == "manual" and len(a.roles) != 1:
        ap.error("en modo manual indica un solo rol, p. ej. --roles carro1")
    maestras = [MaestraVirtual(r, a.pc, a.modo) for r in a.roles]
    for m in maestras:
        m.start()
    if a.modo == "manual":
        teclado(maestras[0])
        return
    t0 = time.time()
    try:
        while not a.duracion or time.time() - t0 < a.duracion:
            time.sleep(5)
            print(time.strftime("%H:%M:%S"))
            for m in maestras:
                print("  " + m.resumen())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
