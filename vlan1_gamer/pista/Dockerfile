# Servidor de pista (VLAN 1): física autoritativa en PyBullet
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends iproute2 iputils-ping \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY vlan1_gamer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY comun/rutas.sh /rutas.sh
COPY comun/*.py ./
COPY vlan1_gamer/pista_comun.py vlan1_gamer/pista/servidor_pista.py ./
RUN sed -i 's/\r$//' /rutas.sh && chmod +x /rutas.sh
ENV PYTHONUNBUFFERED=1
EXPOSE 8080 6000/udp
ENTRYPOINT ["/rutas.sh"]
CMD ["python", "servidor_pista.py"]
