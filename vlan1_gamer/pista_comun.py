"""
Geometría de la pista ovalada (tipo estadio) y construcción de la escena.
La usan el SERVIDOR de pista (física) y los 3 CLIENTES (solo dibujan),
así todos ven exactamente la misma pista.

Coche: racecar.urdf de pybullet_data (el mismo de los entornos Racecar
de PyBullet que usa rl-baselines3-zoo).
"""
import math

import pybullet as p
import pybullet_data

RECTA = 11.0          # largo de cada recta (m)
RADIO = 4.5           # radio de las curvas, medido en la línea central (m)
MEDIO_ANCHO = 1.8     # media anchura de la pista (m)
ALTO_MURO = 0.35
PERIMETRO = 2 * RECTA + 2 * math.pi * RADIO
N_MUESTRAS = 720

COLORES = {1: (0.10, 0.35, 0.95, 1), 2: (0.95, 0.75, 0.10, 1),
           3: (0.10, 0.80, 0.45, 1), 4: (0.85, 0.15, 0.20, 1)}
NOMBRES = {1: "Jugador 1", 2: "Jugador 2", 3: "Jugador 3", 4: "Bot"}

# Índices de articulaciones del racecar.urdf
ESCALA_CARRO = 1.6    # el racecar original mide ~0.5 m; así se ve mejor en la pista
RUEDAS = (2, 3, 5, 7)
DIRECCION = (4, 6)


def punto(s):
    """(x, y, rumbo) de la línea central a la distancia s (antihorario)."""
    s = s % PERIMETRO
    if s < RECTA:                                    # recta inferior  -> +x
        return -RECTA / 2 + s, -RADIO, 0.0
    s -= RECTA
    if s < math.pi * RADIO:                          # curva derecha
        a = -math.pi / 2 + s / RADIO
        return RECTA / 2 + RADIO * math.cos(a), RADIO * math.sin(a), a + math.pi / 2
    s -= math.pi * RADIO
    if s < RECTA:                                    # recta superior  -> -x
        return RECTA / 2 - s, RADIO, math.pi
    s -= RECTA
    a = math.pi / 2 + s / RADIO                      # curva izquierda
    return -RECTA / 2 + RADIO * math.cos(a), RADIO * math.sin(a), a + math.pi / 2


MUESTRAS = [punto(PERIMETRO * i / N_MUESTRAS) for i in range(N_MUESTRAS)]


def progreso(x, y):
    """Distancia s de la muestra de la línea central más cercana a (x, y)."""
    mejor, idx = 1e9, 0
    for i, (px, py, _) in enumerate(MUESTRAS):
        d = (px - x) ** 2 + (py - y) ** 2
        if d < mejor:
            mejor, idx = d, i
    return PERIMETRO * idx / N_MUESTRAS


def posicion_salida(id_carro):
    """Parrilla 2x2 detrás de la línea de meta (que está en x = 0 de la recta inferior)."""
    fila, carril = divmod(id_carro - 1, 2)
    s = RECTA / 2 - 1.6 - 2.0 * fila
    x, y, rumbo = punto(s)
    lateral = -0.8 if carril == 0 else 0.8
    return [x, y + lateral, 0.2], p.getQuaternionFromEuler([0, 0, rumbo])


def _caja(cid, medias, pos, yaw, color, colision=True):
    q = p.getQuaternionFromEuler([0, 0, yaw])
    vis = p.createVisualShape(p.GEOM_BOX, halfExtents=medias, rgbaColor=color, physicsClientId=cid)
    col = p.createCollisionShape(p.GEOM_BOX, halfExtents=medias, physicsClientId=cid) if colision else -1
    return p.createMultiBody(0, col, vis, pos, q, physicsClientId=cid)


def construir_pista(cid, con_colision=True):
    p.setAdditionalSearchPath(pybullet_data.getDataPath(), physicsClientId=cid)
    # pasto
    _caja(cid, [RECTA / 2 + RADIO + 8, RADIO + 8, 0.05], [0, 0, -0.05], 0,
          (0.30, 0.55, 0.25, 1), con_colision)
    n = 140
    for i in range(n):
        s0, s1 = PERIMETRO * i / n, PERIMETRO * (i + 1) / n
        x0, y0, h0 = punto(s0)
        x1, y1, h1 = punto(s1)
        xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
        rumbo = math.atan2(y1 - y0, x1 - x0)
        largo = math.hypot(x1 - x0, y1 - y0) / 2 + 0.03
        nx, ny = -math.sin(rumbo), math.cos(rumbo)     # normal hacia el interior
        # asfalto (solo visual)
        curva = abs(math.sin(h1 - h0)) > 1e-6
        f = (RADIO - MEDIO_ANCHO) / RADIO if curva else 1.0
        fe = (RADIO + MEDIO_ANCHO) / RADIO if curva else 1.0
        # asfalto (solo visual); en curva se usa el largo del borde exterior para no dejar huecos
        _caja(cid, [largo * fe, MEDIO_ANCHO, 0.004], [xm, ym, 0.004], rumbo,
              (0.22, 0.23, 0.26, 1), colision=False)
        # muro interior (piano rojo/blanco) y exterior
        piano = (0.9, 0.15, 0.15, 1) if (i // 2) % 2 else (0.95, 0.95, 0.95, 1)
        _caja(cid, [largo * f + 0.02, 0.12, ALTO_MURO / 2],
              [xm + nx * (MEDIO_ANCHO + 0.12), ym + ny * (MEDIO_ANCHO + 0.12), ALTO_MURO / 2],
              rumbo, piano, con_colision)
        _caja(cid, [largo * fe + 0.02, 0.15, ALTO_MURO / 2],
              [xm - nx * (MEDIO_ANCHO + 0.15), ym - ny * (MEDIO_ANCHO + 0.15), ALTO_MURO / 2],
              rumbo, (0.20, 0.30, 0.55, 1), con_colision)
    # línea de meta a cuadros en x = 0 de la recta inferior
    k = 10
    lado = 2 * MEDIO_ANCHO / k
    for i in range(k):
        for j in range(2):
            col = (0.05, 0.05, 0.05, 1) if (i + j) % 2 else (0.97, 0.97, 0.97, 1)
            _caja(cid, [lado / 2, lado / 2, 0.006],
                  [-lado / 2 + lado * j, -RADIO - MEDIO_ANCHO + lado * (i + 0.5), 0.006], 0, col, False)
    # tribuna y pórtico de meta para dar referencia visual
    # tribuna escalonada
    for k in range(3):
        _caja(cid, [4.0, 0.35, 0.2 + 0.2 * k], [0, -RADIO - MEDIO_ANCHO - 1.0 - 0.7 * k, 0.2 + 0.2 * k], 0,
              (0.62 - 0.08 * k, 0.62 - 0.08 * k, 0.68 - 0.08 * k, 1), con_colision)
    for lado_y in (-RADIO - MEDIO_ANCHO - 0.4, -RADIO + MEDIO_ANCHO + 0.4):
        _caja(cid, [0.08, 0.08, 1.1], [0, lado_y, 1.1], 0, (0.85, 0.85, 0.85, 1), con_colision)
    _caja(cid, [0.1, MEDIO_ANCHO + 0.5, 0.18], [0, -RADIO, 2.1], 0, (0.95, 0.75, 0.10, 1), False)


def escena_render():
    """Escena espejo para el proceso de render (pista + 4 carros, sin física)."""
    cid = p.connect(p.DIRECT)
    construir_pista(cid, con_colision=False)
    carros = {i: crear_carro(cid, i, dinamico=False) for i in (1, 2, 3, 4)}
    return cid, carros


def crear_carro(cid, id_carro, dinamico=True):
    pos, q = posicion_salida(id_carro)
    car = p.loadURDF("racecar/racecar.urdf", pos, q, globalScaling=ESCALA_CARRO,
                     useFixedBase=not dinamico, physicsClientId=cid)
    color = COLORES[id_carro]
    for link in range(-1, p.getNumJoints(car, physicsClientId=cid)):
        p.changeVisualShape(car, link, rgbaColor=color, physicsClientId=cid)
    for rueda in RUEDAS:
        p.changeVisualShape(car, rueda, rgbaColor=(0.08, 0.08, 0.08, 1), physicsClientId=cid)
    if dinamico:
        for j in range(p.getNumJoints(car, physicsClientId=cid)):
            p.setJointMotorControl2(car, j, p.VELOCITY_CONTROL, targetVelocity=0, force=0,
                                    physicsClientId=cid)
    return car
