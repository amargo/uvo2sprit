# uvo2sprit
A Python-based application that automatically retrieves historical drive data from the Kia UVO system and uploads it to spritmonitor.de

## Features
- Retrieves trip data from Kia UVO API
- Uploads trip data to SpritMonitor.de
- Supports historical data retrieval
- Handles both AC and DC charging data
- Rate limiting aware to prevent API blocks
- Smart duplicate detection
- Automatic odometer calculation from trip distances
- Focus on historical data for accuracy

## Requirements
- Python 3.8 or higher
- Kia UVO account credentials
- SpritMonitor.de API access (bearer token and app token)
- Vehicle registered on both platforms

## Installation
1. Clone this repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your credentials:
   ```bash
   cp .env.example .env
   ```

## Configuration
Edit the `.env` file with your credentials:
- `KIA_USERNAME`: Your Kia UVO username
- `KIA_PASSWORD`: Your Kia UVO password
- `KIA_VEHICLE_UUID`: Your vehicle's UUID from Kia UVO
- `SPRITMONITOR_BEARER_TOKEN`: Your SpritMonitor bearer token
- `SPRITMONITOR_APP_TOKEN`: Your SpritMonitor app token
- `SPRITMONITOR_VEHICLE_ID`: Your vehicle ID on SpritMonitor
- `SPRITMONITOR_TANK_ID`: Tank ID for your vehicle (default: 1 for EVs)

## Usage
Run the application:
```bash
python main.py
```

The application will:
1. Fetch trip data from Kia UVO
2. Calculate accurate odometer values for each day
3. Check for existing entries in SpritMonitor
4. Upload any missing historical entries
   - Today's data is skipped to ensure accuracy
   - Only complete historical data is uploaded

## Data Processing
- Odometer values are calculated from trip distances
- Historical data is uploaded once complete
- Today's data is skipped to avoid partial data
- Proper handling of electricity as fuel type (fuelsortid=5)
- Detailed consumption breakdown in notes (engine, climate, electronics, etc.)

## Rate Limiting
- Kia UVO API is limited to 200 requests per day
- The application is designed to handle these limits gracefully
- By default, it only fetches the last 30 days of data
