# Enrutador inter-VLAN: Alpine + iptables + cliente MQTT para publicar contadores
FROM alpine:3.20
RUN apk add --no-cache iptables iproute2 mosquitto-clients
COPY vlan3_admin/router/router.sh /router.sh
RUN chmod +x /router.sh && sed -i 's/\r$//' /router.sh
CMD ["/router.sh"]
