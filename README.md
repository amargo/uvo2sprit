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

## Spritmonitor Currency IDs

The following currency IDs are supported by Spritmonitor:

| ID | Currency |
|----|----------|
| 0 | EUR |
| 1 | CHF |
| 2 | USD |
| 3 | CAD |
| 4 | GBP |
| 5 | DKK |
| 6 | NOK |
| 7 | SEK |
| 8 | PLN |
| 9 | SKK |
| 10 | CZK |
| 11 | HUF |
| 12 | SIT |
| 13 | DEM |
| 14 | BRL |
| 15 | HRK |
| 16 | BGN |
| 17 | ARS |
| 18 | CLP |
| 19 | AUD |
| 20 | LTL |
| 21 | LVL |
| 22 | RON |
| 23 | RUB |
| 24 | EEK |
| 25 | ILS |
| 26 | BYR |
| 27 | TRY |
| 28 | SGD |
| 29 | MYR |
| 30 | ISK |
| 31 | YEN |
| 32 | CNY |
| 33 | RSD |

## Configuration
The following environment variables can be set in the `.env` file:
- `UVO_USERNAME`: Your Kia UVO/Bluelink username
- `UVO_PASSWORD`: Your Kia UVO/Bluelink password
- `UVO_VEHICLE_UUID`: Your vehicle's UUID from Kia UVO/Bluelink
- `UVO_PIN`: Your Kia UVO/Bluelink PIN
- `SPRITMONITOR_APP_TOKEN`: Your Spritmonitor API token
- `SPRITMONITOR_BEARER_TOKEN`: Your Spritmonitor bearer token
- `SPRITMONITOR_VEHICLE_ID`: Your vehicle ID on Spritmonitor
- `SPRITMONITOR_TANK_ID`: Tank ID for your vehicle (default: 1)
- `ELECTRICITY_PRICE`: Price of electricity per kWh (default: 41)
- `CURRENCY_ID`: Currency ID for electricity price (default: 11 for HUF, see Currency IDs section)

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

## Docker
[![Docker Image](https://github.com/amargo/uvo2sprit/actions/workflows/ci.yml/badge.svg)](https://github.com/amargo/uvo2sprit/pkgs/container/uvo2sprit)
[![Docker Hub](https://img.shields.io/docker/v/gszoboszlai/uvo2sprit?label=Docker%20Hub)](https://hub.docker.com/r/gszoboszlai/uvo2sprit)

You can run this application using Docker. The image is available on both GitHub Container Registry and Docker Hub:

```bash
# Using GitHub Container Registry:
docker pull ghcr.io/amargo/uvo2sprit:main

# Or using Docker Hub:
docker pull gszoboszlai/uvo2sprit:latest

# Create a .env file with your credentials (see Configuration section)
# Then run the container (using either image):
docker run --rm -v ${PWD}/.env:/app/.env ghcr.io/amargo/uvo2sprit:main
# or
docker run --rm -v ${PWD}/.env:/app/.env gszoboszlai/uvo2sprit:latest
```

The container will automatically fetch and upload your trip data according to the configuration in your `.env` file.

---

## Kia e-Niro MY20 Electric Consumption Calculation (Example & Explanation)

You can calculate the consumption of an electric vehicle in several ways. The following examples and explanations are based on a real-world trip in April 2025 (192 km driven, 64 kWh net battery).

### 1. Calculation Based on Battery Percentage
- Start: 100%, End: 55% → Used: 45% × 64 kWh = **28.8 kWh**
- Consumption: (28.8 kWh / 192 km) × 100 = **15.0 kWh/100 km**

### 2. Calculation Based on UVO Data
- UVO measured energy: **25.45 kWh**
- Consumption: (25.45 kWh / 192 km) × 100 = **13.3 kWh/100 km**

#### Including Regeneration (Gross Consumption):
- Regenerated energy: 7.65 kWh
- Gross consumption: (25.45 + 7.65) kWh = **33.1 kWh**
- Gross consumption: (33.1 kWh / 192 km) × 100 = **17.2 kWh/100 km**

### 3. Why Are the Values Different?
- The calculation based on battery percentage includes all losses (battery heating, BMS, inverter, etc.).
- The UVO value only measures the energy actually used by the drivetrain, climate, and onboard electronics.
- Therefore, the UVO value is always lower, while the percentage-based value is typically 10–12% higher.

### 4. Average Consumption and Losses
Based on the ChatGPT analysis:

| Method                              | Average Consumption (kWh/100 km) |
|--------------------------------------|----------------------------------|
| UVO (net average consumption)        | 15.0                             |
| Real average (with 11% losses)       | 16.7                             |

- The real (gross) consumption is about 1.6 kWh/100 km higher than the UVO value.
- This difference reflects various losses during driving: inverter, battery heating, control electronics, etc.

### 5. Estimated Range
- Based on battery percentage: 64 kWh / 15.0 × 100 = **427 km**
- UVO (net): 64 kWh / 13.3 × 100 = **481 km**
- Gross (with regeneration): 64 kWh / 17.2 × 100 = **372 km**
