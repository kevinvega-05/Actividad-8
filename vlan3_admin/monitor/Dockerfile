# Monitor de red del plano de administración: Alpine + Python (stdlib) + paho-mqtt
FROM alpine:3.20
RUN apk add --no-cache python3 py3-paho-mqtt iproute2 iputils-ping tzdata
WORKDIR /app
COPY comun/rutas.sh /rutas.sh
COPY vlan3_admin/monitor/monitor.py vlan3_admin/monitor/tablero.html ./
RUN sed -i 's/\r$//' /rutas.sh && chmod +x /rutas.sh
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
ENTRYPOINT ["/rutas.sh"]
CMD ["python3", "monitor.py"]
