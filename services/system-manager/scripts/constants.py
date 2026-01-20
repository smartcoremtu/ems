import os

SUPERVISOR_ADDRESS = os.getenv("BALENA_SUPERVISOR_ADDRESS")
API_KEY = os.getenv("BALENA_SUPERVISOR_API_KEY")
APP_ID = os.getenv("BALENA_APP_ID")
APP_NAME = os.getenv("BALENA_APP_NAME")

STATUS_URL = SUPERVISOR_ADDRESS + "/v2/state/status?apikey=" + API_KEY
APPLICATION_URL = SUPERVISOR_ADDRESS + "/v2/applications/state?apikey=" + API_KEY
STOP_URL = SUPERVISOR_ADDRESS + "/v2/applications/" + APP_ID + "/stop-service?apikey=" + API_KEY
RESTART_URL = SUPERVISOR_ADDRESS + "/v2/applications/" + APP_ID + "/restart-service?apikey=" + API_KEY
REBOOT_URL = SUPERVISOR_ADDRESS + "/v1/reboot?apikey=" + API_KEY