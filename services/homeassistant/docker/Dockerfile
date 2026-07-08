FROM ghcr.io/home-assistant/home-assistant:2026.6.3

COPY docker/start.sh /tmp/start.sh

RUN chmod +x /tmp/start.sh

COPY config /config

CMD ["bash","/tmp/start.sh"]
