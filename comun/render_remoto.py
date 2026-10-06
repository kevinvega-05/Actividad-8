"""
Render en un PROCESO aparte.

El renderizador de PyBullet (TinyRenderer, por CPU) tarda 70-250 ms por cuadro.
Si se hiciera en el mismo bucle que la física, la simulación iría en cámara
lenta mientras alguien mira la página. Por eso:

  proceso principal: física en tiempo real + red + ESP32
  proceso de render: copia "espejo" de la escena (mismos modelos, sin física)

El principal le manda, solo cuando hay alguien mirando y el render está libre,
la pose de los cuerpos móviles y la cámara; el render devuelve un JPEG.
Además así se aprovecha un segundo núcleo de la CPU.
"""
import io
import multiprocessing as mp
import os

import pybullet as p


def _trabajador(constructor, args, conn, ancho, alto, fov, lejos):
    from PIL import Image
    try:
        os.nice(10)        # prioridad baja: la física y la red siempre van primero
    except (AttributeError, OSError):
        pass
    cid, _ = constructor(*args)
    proj = p.computeProjectionMatrixFOV(fov, ancho / alto, 0.05, lejos)
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):     # el proceso principal terminó
            return
        if msg is None:
            return
        for body, (pos, q, articulaciones) in msg["cuerpos"].items():
            p.resetBasePositionAndOrientation(body, pos, q, physicsClientId=cid)
            for j, a in articulaciones:
                p.resetJointState(body, j, a, physicsClientId=cid)
        ojo, objetivo = msg["camara"]
        view = p.computeViewMatrix(ojo, objetivo, [0, 0, 1])
        _, _, rgb, _, _ = p.getCameraImage(ancho, alto, view, proj, renderer=p.ER_TINY_RENDERER,
                                           shadow=0, lightDirection=[2, -3, 6], physicsClientId=cid)
        img = Image.frombytes("RGBA", (ancho, alto), bytes(bytearray(rgb))).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        try:
            conn.send(buf.getvalue())
        except (EOFError, OSError):
            return


class RenderRemoto:
    """constructor(*args) debe ser una función de módulo que construya la escena y
    devuelva (cid, {clave: body_id}); se llama igual en ambos procesos, así los ids coinciden."""

    def __init__(self, constructor, args, ancho=480, alto=360, fov=55, lejos=40):
        ctx = mp.get_context("spawn")
        self.conn, hijo = ctx.Pipe()
        self.proc = ctx.Process(target=_trabajador, args=(constructor, args, hijo, ancho, alto, fov, lejos),
                                daemon=True)
        self.proc.start()
        self.ocupado = False
        self._movibles = {}

    def _articulaciones(self, cid, body):
        if body not in self._movibles:
            self._movibles[body] = [j for j in range(p.getNumJoints(body, physicsClientId=cid))
                                    if p.getJointInfo(body, j, physicsClientId=cid)[2] in
                                    (p.JOINT_REVOLUTE, p.JOINT_PRISMATIC)]
        js = self._movibles[body]
        if not js:
            return []
        estados = p.getJointStates(body, js, physicsClientId=cid)
        return [(j, e[0]) for j, e in zip(js, estados)]

    def pedir(self, cid, cuerpos, ojo, objetivo):
        """Envía la escena a renderizar si el proceso de render está libre."""
        if self.ocupado:
            return
        datos = {}
        for body in cuerpos:
            pos, q = p.getBasePositionAndOrientation(body, physicsClientId=cid)
            datos[body] = (pos, q, self._articulaciones(cid, body))
        self.conn.send({"cuerpos": datos, "camara": (list(ojo), list(objetivo))})
        self.ocupado = True

    def pedir_datos(self, datos, ojo, objetivo):
        """Igual que pedir(), pero con poses ya armadas {body: (pos, q, [(j, ángulo), ...])}."""
        if self.ocupado:
            return
        self.conn.send({"cuerpos": datos, "camara": (list(ojo), list(objetivo))})
        self.ocupado = True

    def recoger(self):
        """Devuelve el JPEG si ya está listo (no bloquea)."""
        if self.ocupado and self.conn.poll():
            self.ocupado = False
            return self.conn.recv()
        return None
