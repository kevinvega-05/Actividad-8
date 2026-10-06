# Guardia de VLAN: aplica en el motor Docker (red del host) la regla que impide
# encaminar entre los bridges de las VLAN sin pasar por el enrutador inter-VLAN
FROM alpine:3.20
RUN apk add --no-cache iptables iptables-legacy iproute2
COPY vlan3_admin/guardia/guardia.sh /guardia.sh
RUN sed -i 's/\r$//' /guardia.sh && chmod +x /guardia.sh
CMD ["/guardia.sh"]
