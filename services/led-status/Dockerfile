FROM ubuntu


RUN apt update && apt install -y \
    python3 \
    python3-pip \
    python3-lgpio \
    python3-pigpio \
    python3-rpi.gpio \
    build-essential \
    gcc \
    python3-dev \
    iputils-ping \
    curl \
    && apt-get clean

RUN pip3 install --break-system-packages \
    gpiozero \
    docker


COPY "./led.py" "/led.py"


CMD ["python3", "/led.py"]