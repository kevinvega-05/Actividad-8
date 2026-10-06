# Simulación robótica real-to-sim (VLAN 2): una imagen para Spot, Pepper y NAO
# Python 3.9 porque el instalador de las mallas de Pepper/NAO de qibullet solo
# existe hasta 3.9.
FROM python:3.9-slim

# Las mallas de Pepper y NAO son de SoftBank Robotics y vienen cifradas dentro de
# qibullet; instalarlas implica aceptar su licencia (EULA). Léela en
# https://github.com/softbankrobotics-research/qibullet y, si estás de acuerdo,
# pon QIBULLET_ACEPTO_LICENCIA=si en el archivo .env antes de construir.
ARG QIBULLET_ACEPTO_LICENCIA=no
RUN if [ "$QIBULLET_ACEPTO_LICENCIA" != "si" ]; then \
      echo "ERROR: debes leer y aceptar la licencia de las mallas de qibullet (SoftBank)." ; \
      echo "       Pon QIBULLET_ACEPTO_LICENCIA=si en el archivo .env y vuelve a construir." ; \
      exit 1 ; fi

RUN apt-get update && apt-get install -y --no-install-recommends iproute2 iputils-ping \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY vlan2_robotica/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && python -c "from qibullet import tools; tools._install_resources(agreement=True)"
COPY comun/rutas.sh /rutas.sh
COPY comun/*.py ./
COPY vlan2_robotica/robot_sim.py ./
COPY vlan2_robotica/modelos ./modelos
RUN sed -i 's/\r$//' /rutas.sh && chmod +x /rutas.sh
ENV PYTHONUNBUFFERED=1 REX_URDF=/app/modelos/rex/urdf/rex.urdf
EXPOSE 8080 5011/udp 5012/udp 5013/udp
ENTRYPOINT ["/rutas.sh"]
CMD ["python", "robot_sim.py"]
