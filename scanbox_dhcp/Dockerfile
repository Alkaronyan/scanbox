FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    dnsmasq \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY dnsmasq.conf /etc/dnsmasq.d/scanbox.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
