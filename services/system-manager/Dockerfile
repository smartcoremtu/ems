FROM arm64v8/alpine:3

RUN apk add --update iptables ip6tables iptables-legacy curl

RUN apk add --update --no-cache python3 && ln -sf python3 /usr/bin/python
RUN apk add --update py3-pip
# RUN python -m ensurepip --upgrade
COPY requirements.txt /tmp/requirements.txt
RUN pip install --break-system-packages -r /tmp/requirements.txt
# ADD REQUIREMENTS.TXT AND INSTALL WITH PIP!!!

RUN mkdir -p /tmp/scripts
COPY scripts/* /tmp/scripts/

COPY start.sh /tmp/start.sh

# The scripts are exec'd directly. Git on Windows does not record the executable bit, so
# set it here rather than relying on the mode of the checkout the release was pushed from.
RUN chmod +x /tmp/start.sh /tmp/scripts/*.sh /tmp/scripts/watchdog.py

CMD ["/tmp/start.sh"]
