import requests

SM_API_URL = "https://api.spritmonitor.de/v1"

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
    
    def __init__(self, bearer_token: str, app_token: str, base_url: str = SM_API_URL):
        """
        Initialize the SpritMonitorClient with authentication tokens and base URL.
        
        :param bearer_token: Bearer token for authorization.
        :param app_token: Application token.
        :param base_url: Base URL for the spritmonitor.de API.
        """
        self.base_url = base_url
        self.bearer_token = bearer_token
        self.app_token = app_token
    
    def _bearer_auth(self, r):
        """
        Set authorization headers on the request.
        
        :param r: The request object.
        :return: The modified request object with authorization headers.
        """
        r.headers["Authorization"] = f"Bearer {self.bearer_token}"
        r.headers["Application-ID"] = self.app_token
        r.headers["User-Agent"] = "Python Spritmonitor API Access Example"
        return r
    
    def _send_request(self, method: str, url: str, json_payload: dict = None) -> dict:
        """
        Send a request to the Spritmonitor REST endpoint.
        
        :param method: HTTP method ('GET', 'POST', etc.)
        :param url: The full URL of the endpoint.
        :param json_payload: Optional JSON payload for POST requests.
        :return: JSON response from the API.
        :raises: Exception if the response status is not 200.
        """
        response = requests.request(method, url, auth=self._bearer_auth, json=json_payload)
        if response.status_code != 200:
            raise Exception(f"Request returned an error: {response.status_code} {response.text}")
        return response.json()
    
    def get_vehicles(self) -> dict:
        """
        Retrieve all vehicles for the user.
        
        :return: JSON response containing the list of vehicles.
        """
        url = f"{self.base_url}/vehicles.json"
        return self._send_request("GET", url)
    
    def get_vehicle_data(self, vehicle_id: str) -> dict:
        """
        Retrieve data for a specific vehicle.
        
        :param vehicle_id: The unique identifier of the vehicle.
        :return: JSON response containing the vehicle data.
        """
        url = f"{self.base_url}/vehicle/{vehicle_id}/data.json"
        return self._send_request("GET", url)
    
    def upload_vehicle_data(self, vehicle_id: str, payload: dict) -> dict:
        """
        Upload data for a specific vehicle.
        
        :param vehicle_id: The unique identifier of the vehicle.
        :param payload: Dictionary containing the vehicle data to be uploaded.
        :return: JSON response from the API.
        """
        url = f"{self.base_url}/vehicle/{vehicle_id}/upload.json"
        return self._send_request("POST", url, json_payload=payload)
    
    def get_fuelings(self, vehicle_id: str) -> dict:
        """
        Retrieve fueling data for a specific vehicle.
        
        :param vehicle_id: The unique identifier of the vehicle.
        :return: JSON response containing fueling records.
        """
        url = f"{self.base_url}/vehicle/{vehicle_id}/fuelings.json"
        return self._send_request("GET", url)
    
    def upload_fueling_data(self, vehicle_id: str, payload: dict) -> dict:
        """
        Upload fueling data for a specific vehicle.
        
        :param vehicle_id: The unique identifier of the vehicle.
        :param payload: Dictionary containing the fueling data to be uploaded.
        :return: JSON response from the API.
        """
        url = f"{self.base_url}/vehicle/{vehicle_id}/fuelings.json"
        return self._send_request("POST", url, json_payload=payload)

    def get_tanks(self, vehicle_id: str) -> dict:
        """
        Get available tanks/charging types for a vehicle.
        
        :param vehicle_id: The unique identifier of the vehicle.
        :return: JSON response containing the tanks data.
        """
        url = f"{self.base_url}/vehicle/{vehicle_id}/tanks.json"
        return self._send_request("GET", url)

    def get_latest_fuelings(self, vehicle_id: str, tank_id: str, limit: int = 5) -> dict:
        """
        Get the latest fueling entries for a vehicle.
        
        :param vehicle_id: ID of the vehicle
        :param tank_id: ID of the tank
        :param limit: Maximum number of entries to return
        :return: JSON response containing the list of fuelings
        """
        url = f"{self.base_url}/vehicle/{vehicle_id}/tank/{tank_id}/fuelings.json?offset=0&limit={limit}"
        return self._send_request("GET", url)

    def delete_fueling(self, vehicle_id: str, tank_id: str, fueling_id: str) -> dict:
        """
        Delete a fueling entry.
        
        :param vehicle_id: ID of the vehicle
        :param tank_id: ID of the tank
        :param fueling_id: ID of the fueling entry to delete
        :return: JSON response from the API
        """
        url = f"{self.base_url}/vehicle/{vehicle_id}/tank/{tank_id}/fueling/{fueling_id}.delete"
        return self._send_request("GET", url)

    def upload_consumption_data(self, vehicle_id: str, tank_id: str, data: dict) -> dict:
        """
        Upload consumption data for a specific vehicle.
        
        Expected data format:
        {
            "date": "DD.MM.YYYY",
            "odometer": int,
            "trip": float,  # distance in km
            "quantity": float,  # consumed kWh
            "charging_power": float,  # in kW
            "charging_duration": int,  # in minutes
            "charge_info": str,  # e.g. "ac,source_wallbox" or "dc,source_public"
            "percent": float,  # battery percentage after charging
            "bc_consumption": float,  # board computer consumption in kWh/100km
            "bc_quantity": float,  # board computer total consumption in kWh
            "bc_speed": float,  # board computer average speed
            "note": str,  # additional information
            "location": str,  # optional charging location
            "position": str,  # optional GPS coordinates "lat,lon"
        }
        
        :param vehicle_id: The unique identifier of the vehicle.
        :param tank_id: The tank/charging type ID from get_tanks().
        :param data: Dictionary containing the consumption data to be uploaded.
        :return: JSON response from the API.
        """
        url = f"{self.base_url}/vehicle/{vehicle_id}/tank/{tank_id}/fueling.json"
        
        # Build request data
        request_data = {
            "date": data["date"],
            "odometer": data["odometer"],
            "trip": data["trip"],
            "quantity": data["quantity"],
            "type": data["type"],  # can be: invalid, full, notfull, first
            "price": data["price"],
            "currencyid": data["currencyid"],
            "pricetype": data["pricetype"],
            "fuelsortid": "19",  # Elektrizität (ID 19) or Ökostrom (ID 24)
            "quantityunitid": data["quantityunitid"],
            "charge_info": f"{data['charge_info']},source_vehicle",  # add source_vehicle since we get data from the car
            "percent": data["percent"],
            "bc_consumption": data.get("bc_consumption", ""),
            "bc_quantity": data.get("bc_quantity", ""),
            "bc_speed": data.get("bc_speed", ""),
            "note": data.get("note", ""),
            "location": data.get("location", ""),
            "position": data.get("position", "")
        }

        # Only add charging power and duration if they are valid values
        charging_power = data.get("charging_power")
        if charging_power and charging_power > 0:
            request_data["charging_power"] = str(charging_power)

        charging_duration = data.get("charging_duration")
        if charging_duration and charging_duration > 0:
            request_data["charging_duration"] = str(charging_duration)
        
        # Convert request data to query string and URL encode it
        query_string = "&".join([f"{k}={requests.utils.quote(str(v))}" for k, v in request_data.items() if v != ""])
        url = f"{url}?{query_string}"
        
        return self._send_request("GET", url)
