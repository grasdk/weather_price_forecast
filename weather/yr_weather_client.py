import datetime
import argparse
import json
import os
from pathlib import Path
import requests

# ==============================================================================
# FILE PATH CONSTANTS
# ==============================================================================
SCRIPT_FOLDER = Path(__file__).resolve().parent
SITE_CONFIG_FILE = SCRIPT_FOLDER / "site_info.json"
LOCATION_CONFIG_FILE = SCRIPT_FOLDER / "location.json"
RAW_DATA_FILE = "raw_forecast.json"
CACHE_META_FILE = "weather_cache_meta.json"

BASE_URL = "https://api.met.no/weatherapi/locationforecast/2.0/complete"


def load_json_file(filepath: str) -> dict:
    """Helper function to load data from a JSON file."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Required configuration file '{filepath}' was not found.")
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


class YrLocationForecastClient:
    def __init__(self, site_config_path: str, location_config_path: str, datafolder: str = None):
        # Load external configuration files
        self.site_info = load_json_file(site_config_path)
        self.location_info = load_json_file(location_config_path)

        config_folder = Path(site_config_path).resolve().parent
        configured_datafolder = datafolder or self.site_info.get("datafolder", ".")
        self.datafolder = Path(configured_datafolder).expanduser()
        if datafolder is None:
            self.datafolder = config_folder / self.datafolder
        self.datafolder = self.datafolder.resolve()
        self.raw_data_file = self.datafolder / RAW_DATA_FILE
        self.cache_meta_file = self.datafolder / CACHE_META_FILE

        # MET Norway requires coordinates rounded to max 4 decimal places
        self.lat = round(float(self.location_info["latitude"]), 4)
        self.lon = round(float(self.location_info["longitude"]), 4)

        # Construct User-Agent string from external site info
        app_name = self.site_info.get("app_name", "WeatherApp/1.0")
        contact = self.site_info.get("contact_info", "admin@example.com")
        self.user_agent = f"{app_name} ({contact})"

        self.url = f"{BASE_URL}?lat={self.lat}&lon={self.lon}"

    def _load_cache_meta(self) -> dict:
        """Loads persistent cache metadata (Expires & Last-Modified) from disk."""
        if self.cache_meta_file.exists():
            try:
                with open(self.cache_meta_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {}
        return {}

    def _save_cache_meta(self, expires: str, last_modified: str):
        """Saves persistent cache metadata to disk."""
        meta = {
            "expires": expires,
            "last_modified": last_modified
        }
        with open(self.cache_meta_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

    def _is_cache_valid(self, expires_header: str) -> bool:
        """Checks if current time is before the 'Expires' header timestamp."""
        if not expires_header or not self.raw_data_file.exists():
            return False

        try:
            expires_dt = datetime.datetime.strptime(
                expires_header, "%a, %d %b %Y %H:%M:%S %Z"
            ).replace(tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            return now < expires_dt
        except ValueError:
            return False

    def get_forecast(self) -> dict:
        """Fetches forecast data using local disk cache and HTTP conditional GETs."""
        meta = self._load_cache_meta()
        expires = meta.get("expires")
        last_modified = meta.get("last_modified")

        # Check local disk cache validity
        if self._is_cache_valid(expires):
            print("--> Local file cache is valid. Reading raw JSON from disk...")
            with open(self.raw_data_file, "r", encoding="utf-8") as f:
                return json.load(f)

        headers = {"User-Agent": self.user_agent}
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        print(f"--> Sending HTTP request to MET Norway for {self.location_info.get('name', 'location')}...")
        response = requests.get(self.url, headers=headers)

        # 304 Not Modified: Reuse local raw JSON file
        if response.status_code == 304:
            print("<-- 304 Not Modified: Server data unchanged. Reading raw JSON from disk...")
            new_expires = response.headers.get("Expires", expires)
            self._save_cache_meta(new_expires, last_modified)
            
            with open(self.raw_data_file, "r", encoding="utf-8") as f:
                return json.load(f)

        elif response.status_code == 203:
            print("WARNING [203]: Deprecated API endpoint.")

        elif response.status_code == 429:
            raise RuntimeError("ERROR [429]: Request throttled by MET Norway.")

        elif response.status_code == 403:
            raise RuntimeError("ERROR [403]: Forbidden. Check User-Agent format in site_info.json.")

        response.raise_for_status()

        # Parse JSON and write raw response directly to disk
        raw_json_data = response.json()
        print(f"<-- {response.status_code} OK: Writing raw forecast JSON to '{RAW_DATA_FILE}'...")
        
        with open(self.raw_data_file, "w", encoding="utf-8") as f:
            json.dump(raw_json_data, f, indent=2, ensure_ascii=False)

        # Update cache metadata
        new_expires = response.headers.get("Expires")
        new_last_modified = response.headers.get("Last-Modified")
        self._save_cache_meta(new_expires, new_last_modified)

        return raw_json_data


# ==============================================================================
# EXECUTION
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch the Yr weather forecast.")
    parser.add_argument("--datafolder", help="Root data folder; overrides datafolder in site_info.json.")
    args = parser.parse_args()

    client = YrLocationForecastClient(
        site_config_path=SITE_CONFIG_FILE,
        location_config_path=LOCATION_CONFIG_FILE,
        datafolder=args.datafolder
    )

    data = client.get_forecast()

    # Read current parameters from loaded data
    timeseries = data["properties"]["timeseries"][0]
    instant = timeseries["data"]["instant"]["details"]

    print("\n--- Current Weather Summary ---")
    print(f"Location:    {client.location_info.get('name')}")
    print(f"Time:        {timeseries['time']}")
    print(f"Temperature: {instant.get('air_temperature')} °C")
    print(f"Wind Speed:  {instant.get('wind_speed')} m/s")
    print("-------------------------------\n")
