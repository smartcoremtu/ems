import gpiozero.pins.lgpio
import lgpio
import time
from time import sleep
import subprocess

def __patched_init(self, chip=None):
    gpiozero.pins.lgpio.LGPIOFactory.__bases__[0].__init__(self)
    chip = 0
    self._handle = lgpio.gpiochip_open(chip)
    self._chip = chip
    self.pin_class = gpiozero.pins.lgpio.LGPIOPin

gpiozero.pins.lgpio.LGPIOFactory.__init__ = __patched_init

from gpiozero import LED

led1 = LED(17)
led2 = LED(27)
HA_IP = "172.18.4.2"
Google_IP = "8.8.8.8"

def ping(ip):
    try:
        output = subprocess.check_output(["ping", "-c", "1", "-W", "1", ip], stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        return False

while True:
    if ping(HA_IP):
        led1.on()
    else:
        led1.off()

    if ping(Google_IP):
        led2.on()
    else:
        led2.off()
    
time.sleep(1)
