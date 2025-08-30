import os
import coloredlogs

from dotenv import load_dotenv
from VehicleClient import VehicleClient

# Install colored logs
coloredlogs.install(level='DEBUG', isatty=True)

def main():
    # Load environment variables
    load_dotenv()

    # Initialize vehicle client
    vehicle_client = VehicleClient(
        username=os.environ["UVO_USERNAME"],
        password=os.environ["UVO_PASSWORD"],
        pin=os.environ["UVO_PIN"],
        vehicle_uuid=os.environ["UVO_VEHICLE_UUID"],
        use_kiauvoapieu=os.environ.get("UVO_USE_KIAUVOAPIEU", "True").lower() in ("true", "1", "yes"),
        kia_language=os.environ.get("UVO_KIA_LANGUAGE", "hu")
    )
    
    # Initialize vehicle connection
    try:
        vehicle_client.initialize()
    except Exception as e:
        print(f"Failed to initialize vehicle connection: {e}")
        return

    # Get vehicle data and upload to Spritmonitor
    try:
        # Force refresh to get latest data
        vehicle_client.refresh()
        
        # The refresh() method will automatically:
        # 1. Get latest vehicle data from KIA UVO
        # 2. Process daily stats
        # 3. Upload consumption data to Spritmonitor via send_consumption_to_spritmonitor()
        
        print("Successfully refreshed and uploaded vehicle data to Spritmonitor")
        
    except Exception as e:
        print(f"Failed to process and upload vehicle data: {str(e)}")

if __name__ == "__main__":
    main()
