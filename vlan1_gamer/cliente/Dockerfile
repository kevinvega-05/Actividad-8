# Contenedor cliente (VLAN 1): recibe su ESP32 maestra y dibuja su carro
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends iproute2 iputils-ping \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY vlan1_gamer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY comun/rutas.sh /rutas.sh
COPY comun/*.py ./
COPY vlan1_gamer/pista_comun.py vlan1_gamer/cliente/cliente.py ./
RUN sed -i 's/\r$//' /rutas.sh && chmod +x /rutas.sh
ENV PYTHONUNBUFFERED=1
EXPOSE 8080 5001/udp 5002/udp 5003/udp
ENTRYPOINT ["/rutas.sh"]
CMD ["python", "cliente.py"]
