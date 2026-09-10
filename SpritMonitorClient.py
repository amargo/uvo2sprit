import logging

import requests

SM_API_URL = "https://api.spritmonitor.de/v1"

# (connect timeout, read timeout) in seconds. Without this a cron run can hang forever.
DEFAULT_TIMEOUT = (5, 30)

_LOGGER = logging.getLogger(__name__)


class SpritMonitorError(Exception):
    """Raised when the spritmonitor.de API returns something we cannot use."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class SpritMonitorClient:
    """
    SpritMonitorClient class to handle communication with the spritmonitor.de API.

    This client provides methods to:
      - Retrieve all vehicles for the user.
      - Retrieve data for a specific vehicle.
      - Upload data for a specific vehicle.
      - Retrieve fueling data for a specific vehicle.
      - Upload fueling data for a specific vehicle.
      - Retrieve available tanks/charging types for a vehicle.
      - Upload consumption data for a specific vehicle.
    """

    # Fields forwarded verbatim to the fueling endpoint when present in the payload.
    OPTIONAL_FIELDS = (
        "charge_info",
        "charging_power",
        "charging_duration",
        "bc_consumption",
        "bc_quantity",
        "bc_speed",
        "note",
        "location",
        "position",
        "country",
        "stationname",
        "price",
        "currencyid",
        "pricetype",
    )

    def __init__(
        self,
        bearer_token: str,
        app_token: str,
        base_url: str = SM_API_URL,
        timeout=DEFAULT_TIMEOUT,
    ):
        """
        Initialize the SpritMonitorClient with authentication tokens and base URL.

        :param bearer_token: Bearer token for authorization.
        :param app_token: Application token.
        :param base_url: Base URL for the spritmonitor.de API.
        :param timeout: (connect, read) timeout passed to requests.
        :raises SpritMonitorError: if a token is missing.
        """
        if not bearer_token:
            raise SpritMonitorError("SPRITMONITOR_BEARER_TOKEN is not set - cannot authenticate")
        if not app_token:
            raise SpritMonitorError("SPRITMONITOR_APP_TOKEN is not set - cannot authenticate")

        self.base_url = base_url
        self.bearer_token = bearer_token
        self.app_token = app_token
        self.timeout = timeout

        # One session keeps the TLS connection alive across the whole backfill.
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.bearer_token}",
                "Application-ID": self.app_token,
                "User-Agent": "uvo2sprit (https://github.com/amargo/uvo2sprit)",
            }
        )

    def _send_request(
        self,
        method: str,
        url: str,
        params: dict | None = None,
        json_payload: dict | None = None,
    ) -> dict:
        """
        Send a request to the Spritmonitor REST endpoint.

        :param method: HTTP method ('GET', 'POST', etc.)
        :param url: The full URL of the endpoint.
        :param params: Optional query string parameters.
        :param json_payload: Optional JSON payload for POST requests.
        :return: JSON response from the API.
        :raises SpritMonitorError: if the request fails or the response is not JSON.
        """
        try:
            response = self.session.request(
                method, url, params=params, json=json_payload, timeout=self.timeout
            )
        except requests.RequestException as e:
            raise SpritMonitorError(f"Request to {url} failed: {e}") from e

        if response.status_code != 200:
            raise SpritMonitorError(
                f"Request returned an error: {response.status_code} {response.text}",
                status_code=response.status_code,
            )

        try:
            return response.json()
        except ValueError as e:
            raise SpritMonitorError(
                f"Response from {url} was not valid JSON: {response.text[:200]}"
            ) from e

    def get_vehicles(self) -> dict:
        """
        Retrieve all vehicles for the user.

        :return: JSON response containing the list of vehicles.
        """
        return self._send_request("GET", f"{self.base_url}/vehicles.json")

    def get_vehicle_data(self, vehicle_id: str) -> dict:
        """
        Retrieve data for a specific vehicle.

        :param vehicle_id: The unique identifier of the vehicle.
        :return: JSON response containing the vehicle data.
        """
        return self._send_request("GET", f"{self.base_url}/vehicle/{vehicle_id}/data.json")

    def upload_vehicle_data(self, vehicle_id: str, payload: dict) -> dict:
        """
        Upload data for a specific vehicle.

        :param vehicle_id: The unique identifier of the vehicle.
        :param payload: Dictionary containing the vehicle data to be uploaded.
        :return: JSON response from the API.
        """
        return self._send_request(
            "POST",
            f"{self.base_url}/vehicle/{vehicle_id}/upload.json",
            json_payload=payload,
        )

    def get_fuelings(self, vehicle_id: str) -> dict:
        """
        Retrieve fueling data for a specific vehicle.

        :param vehicle_id: The unique identifier of the vehicle.
        :return: JSON response containing fueling records.
        """
        return self._send_request("GET", f"{self.base_url}/vehicle/{vehicle_id}/fuelings.json")

    def upload_fueling_data(self, vehicle_id: str, payload: dict) -> dict:
        """
        Upload fueling data for a specific vehicle.

        :param vehicle_id: The unique identifier of the vehicle.
        :param payload: Dictionary containing the fueling data to be uploaded.
        :return: JSON response from the API.
        """
        return self._send_request(
            "POST",
            f"{self.base_url}/vehicle/{vehicle_id}/fuelings.json",
            json_payload=payload,
        )

    def get_tanks(self, vehicle_id: str) -> dict:
        """
        Get available tanks/charging types for a vehicle.

        :param vehicle_id: The unique identifier of the vehicle.
        :return: JSON response containing the tanks data.
        """
        return self._send_request("GET", f"{self.base_url}/vehicle/{vehicle_id}/tanks.json")

    def get_latest_fuelings(self, vehicle_id: str, tank_id: str, limit: int = 5) -> dict:
        """
        Get the latest fueling entries for a vehicle.

        :param vehicle_id: ID of the vehicle
        :param tank_id: ID of the tank
        :param limit: Maximum number of entries to return
        :return: JSON response containing the list of fuelings
        """
        url = f"{self.base_url}/vehicle/{vehicle_id}/tank/{tank_id}/fuelings.json"
        return self._send_request("GET", url, params={"offset": 0, "limit": limit})

    def delete_fueling(self, vehicle_id: str, tank_id: str, fueling_id: str) -> dict:
        """
        Delete a fueling entry. This is irreversible.

        :param vehicle_id: ID of the vehicle
        :param tank_id: ID of the tank
        :param fueling_id: ID of the fueling entry to delete
        :return: JSON response from the API
        """
        url = f"{self.base_url}/vehicle/{vehicle_id}/tank/{tank_id}/fueling/{fueling_id}.delete"
        _LOGGER.warning("Deleting Spritmonitor fueling %s of vehicle %s", fueling_id, vehicle_id)
        return self._send_request("GET", url)

    def build_consumption_params(self, data: dict) -> dict:
        """
        Turn a consumption payload into the query parameters the API expects.

        Required keys: date (DD.MM.YYYY), odometer, trip, quantity, type,
        quantityunitid, percent.
        Optional keys: see OPTIONAL_FIELDS. 'fuelsortid' defaults to 19
        (Elektrizitaet; 24 would be Oekostrom).

        The endpoint takes everything as query parameters, so empty values are
        dropped rather than sent as blanks.

        :param data: Dictionary containing the consumption data.
        :return: dict of query parameters.
        """
        params = {
            "date": data["date"],
            "odometer": data["odometer"],
            "trip": data["trip"],
            "quantity": data["quantity"],
            "type": data["type"],  # can be: invalid, full, notfull, first
            "fuelsortid": data.get("fuelsortid", "19"),
            "quantityunitid": data["quantityunitid"],
            "percent": data["percent"],
        }

        for key in self.OPTIONAL_FIELDS:
            value = data.get(key)
            if value is None or value == "":
                continue
            # Skip charging power/duration that carry no information
            if key in ("charging_power", "charging_duration") and not value:
                continue
            params[key] = value

        # We always read the data from the car itself, so tag the source once.
        if "charge_info" in params:
            params["charge_info"] = f"{params['charge_info']},source_vehicle"

        return params

    def upload_consumption_data(self, vehicle_id: str, tank_id: str, data: dict) -> dict:
        """
        Upload consumption data for a specific vehicle.

        See build_consumption_params() for the accepted keys.

        :param vehicle_id: The unique identifier of the vehicle.
        :param tank_id: The tank/charging type ID from get_tanks().
        :param data: Dictionary containing the consumption data to be uploaded.
        :return: JSON response from the API.
        """
        url = f"{self.base_url}/vehicle/{vehicle_id}/tank/{tank_id}/fueling.json"
        return self._send_request("GET", url, params=self.build_consumption_params(data))
