FROM ubuntu

RUN apt update &&  apt install -y python3-pip python3-lgpio python3-pigpio python3-rpi.gpio
RUN pip3 install gpiozero --break-system-packages

RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    python3-dev \
    iputils-ping \
    && apt-get clean

COPY "./led.py" "/led.py"
CMD ["python3", "led.py"]