import logging
import os
import requests
from requests.exceptions import HTTPError
from datetime import timedelta, datetime
import constants

logging.basicConfig(
        format='%(levelname)-8s %(message)s',
        level=logging.DEBUG,
        datefmt='%Y-%m-%d %H:%M:%S')


def logToFile(message):
    logging.info(message)
    logFile = open("/data/restartLog.txt", "a+")
    logFile.write(str(datetime.now()) + ": " + message + "\n")
    logFile.close()


def check_wifi_repeater_n_stop(wifi_started_at):
    # Check wifi repeater is stopped if necessary
    # Can we also check if it needs to run and start if so???
    try:
        response = requests.get(constants.STATUS_URL)
        response.raise_for_status()
    except HTTPError as http_err:
        logging.error(f'HTTP error occurred: {http_err}')
    except Exception as err:
        logging.error(f'Other error occurred: {err}')
    else:
        delta_t = timedelta(seconds=0)
        for container in response.json()["containers"]:            
            ####################
            if container["serviceName"] == "wifi-repeater":
                wifi_running = container["status"] == "Running"
                if wifi_started_at:
                    delta_t = datetime.now() - wifi_started_at
                elif wifi_running:
                    wifi_started_at = datetime.now()

        if wifi_running:
            logging.info(str(delta_t) + " since wifi-repeater started.")
        else:
            logging.info("wifi-repeater is not running")
        if wifi_running and delta_t > timedelta(minutes=10):
            #########################
            ## TODO: Log this in a file somewhere with timestamp
            ##########################
            logging.debug("Past delta t, stop container")
            response = requests.post(constants.STOP_URL, data="{\"serviceName\": \"wifi-repeater\"}", headers={"Content-Type": "application/json"})
            wifi_started_at = None

    return wifi_started_at


def restart_hass(version_changed_at):
    # Check version, if it's updated, restart home assistant
    try:
        response = requests.get(constants.APPLICATION_URL)
        response.raise_for_status()
    except HTTPError as http_err:
        logging.error(f'HTTP error occurred: {http_err}')
    except Exception as err:
        logging.error(f'Other error occurred: {err}')
    else:
        try:
            logging.debug(response.json())
            version = response.json()[constants.APP_NAME]["services"]["homeassistant"]["releaseId"]
        except Exception:
            version = None

    with open("/data/version.txt", "a+") as version_file:
        version_file.seek(0)
        version_old = version_file.read()
        logging.debug("current version: " + version_old)
        if version and version_old != str(version):
            if not version_changed_at:
                version_changed_at = datetime.now()
            
            if datetime.now() - version_changed_at < timedelta(minutes=2):
                logging.debug("Version changed not long ago. Don't restart")
                return version_changed_at
            logging.debug("new version: " + str(version))
            #########################
            ## TODO: Log this in a file somewhere with timestamp
            ## create persistent log function?
            ##########################
            response = requests.post(constants.RESTART_URL, data="{\"serviceName\": \"homeassistant\"}", headers={"Content-Type": "application/json"})
            version_file.seek(0)
            version_file.truncate()
            version_file.write(str(version))
            logToFile("version updated from " + version_old + " to " + str(version) + ". Restarting homeassistant.")
        else:
            version_changed_at = None

    return version_changed_at


def check_internet(last_seen):
    # Ping google, if no google for 30m reboot

    response = os.system("ping -c 1 google.com")

    logging.debug("Current time " + str(datetime.now()) + ". Last seen google at " + str(last_seen))

    if response == 0:
        logging.debug("Internet up")
        last_seen = datetime.now()
    else:
        logging.debug("Internet down!")

        if datetime.now() - last_seen > timedelta(minutes=30):
            logToFile("Unable to connect to the internet for 30 minutes. Restarting")
            response = requests.post(constants.REBOOT_URL, headers={"Content-Type": "application/json"})
            logging.debug(response.status_code)
            logging.debug(response.text)
    
    return last_seen
