import logging
import os
import sys

import coloredlogs
from dotenv import load_dotenv

from SpritMonitorClient import SpritMonitorError
from VehicleClient import VehicleClient

REQUIRED_ENV_VARS = (
    "UVO_USERNAME",
    "UVO_PASSWORD",
    "UVO_PIN",
    "UVO_VEHICLE_UUID",
    "SPRITMONITOR_BEARER_TOKEN",
    "SPRITMONITOR_APP_TOKEN",
    "SPRITMONITOR_VEHICLE_ID",
)

logger = logging.getLogger("uvo2sprit")


def setup_logging():
    """Configure logging once, for the whole process."""
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    coloredlogs.install(
        level=level,
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        isatty=True,
    )


def check_env():
    """
    Fail fast with a useful message instead of sending 'Bearer None' to an API.

    :return: list of missing variable names
    """
    return [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]


def main() -> int:
    # Load environment variables
    load_dotenv()
    setup_logging()

    missing = check_env()
    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        logger.error("Copy .env.example to .env and fill it in.")
        return 1

    # Initialize vehicle client
    try:
        vehicle_client = VehicleClient(
            username=os.environ["UVO_USERNAME"],
            password=os.environ["UVO_PASSWORD"],
            pin=os.environ["UVO_PIN"],
            vehicle_uuid=os.environ["UVO_VEHICLE_UUID"],
            kia_language=os.environ.get("UVO_KIA_LANGUAGE", "hu"),
        )
        vehicle_client.initialize()
    except KeyError as e:
        logger.error(f"Vehicle UUID {e} not found. Check UVO_VEHICLE_UUID.")
        return 1
    except SpritMonitorError as e:
        logger.error(f"Spritmonitor configuration error: {e}")
        return 1
    except Exception as e:
        logger.error(f"Failed to initialize vehicle connection: {e}", exc_info=True)
        return 1

    # Get vehicle data and upload to Spritmonitor.
    # refresh() fetches the latest state, reads the driving info and uploads the
    # complete historical days that Spritmonitor does not have yet.
    try:
        if not vehicle_client.refresh():
            logger.error("Refresh did not complete, see the errors above")
            return 1
    except Exception as e:
        logger.error(f"Failed to process and upload vehicle data: {e}", exc_info=True)
        return 1

    logger.info("Successfully refreshed and uploaded vehicle data to Spritmonitor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
