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
CONFIG_FILE = SCRIPT_FOLDER / "electricity_config.json"
RAW_DATA_FILE = "raw_electricity_prices.json"
CACHE_META_FILE = "electricity_cache_meta.json"

BASE_URL = "https://stromligning.dk/api/prices"

# Minimum cache lifetime (15 minutes) to stay well clear of 5-10 req/15m limit
MIN_CACHE_TTL_MINUTES = 15


def load_json_file(filepath: str) -> dict:
    """Helper function to load data from a JSON file."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Required configuration file '{filepath}' was not found.")
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


class StromligningClient:
    def __init__(self, config_path: str = CONFIG_FILE, datafolder: str = None):
        self.config = load_json_file(config_path)

        config_folder = Path(config_path).resolve().parent
        configured_datafolder = datafolder or self.config.get("datafolder", ".")
        self.datafolder = Path(configured_datafolder).expanduser()
        if datafolder is None:
            self.datafolder = config_folder / self.datafolder
        self.datafolder = self.datafolder.resolve()
        self.raw_data_file = self.datafolder / RAW_DATA_FILE
        self.cache_meta_file = self.datafolder / CACHE_META_FILE

        # Mandatory attribution string per Strømligning terms of use
        self.attribution_text = "Data provided by Strømligning. https://stromligning.dk"

    def _build_url_parameters(self) -> dict:
        """Construct query parameters using UTC datetimes and config settings."""
        now = datetime.datetime.now(datetime.timezone.utc)
        
        # Start at midnight UTC today
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        days_ahead = int(self.config.get("days_ahead", 7))
        end_date = start_date + datetime.timedelta(days=days_ahead)

        return {
            "from": start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "productId": self.config.get("product_id"),
            "supplierId": self.config.get("supplier_id"),
            "customerGroupId": self.config.get("customer_group_id", "c"),
            "priceArea": self.config.get("price_area", "DK1"),
            "lean": str(self.config.get("lean", True)).lower(),
            "forecast": str(self.config.get("forecast", True)).lower(),
            "aggregation": self.config.get("aggregation", "1h"),
            "aggregationMethod": self.config.get("aggregation_method", "mean")
        }

    def _load_cache_meta(self) -> dict:
        """Loads local cache metadata from disk."""
        if self.cache_meta_file.exists():
            try:
                with open(self.cache_meta_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {}
        return {}

    def _save_cache_meta(self, last_fetched_iso: str):
        """Saves local cache metadata to disk."""
        meta = {
            "last_fetched": last_fetched_iso,
            "attribution": self.attribution_text
        }
        with open(self.cache_meta_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

    def _is_cache_fresh(self, last_fetched_iso: str) -> bool:
        """Rate limit safety check: Prevents fetching if last request was < 15 mins ago."""
        if not last_fetched_iso or not self.raw_data_file.exists():
            return False

        try:
            last_fetched = datetime.datetime.fromisoformat(last_fetched_iso)
            now = datetime.datetime.now(datetime.timezone.utc)
            time_diff = now - last_fetched
            
            # Re-use local file if downloaded within the last 15 minutes window
            return time_diff < datetime.timedelta(minutes=MIN_CACHE_TTL_MINUTES)
        except ValueError:
            return False

    def get_prices(self) -> dict:
        """Fetches electricity prices while enforcing rate limit throttling rules."""
        meta = self._load_cache_meta()
        last_fetched = meta.get("last_fetched")

        # Rate Limit Guard: Avoid sending >5-10 requests in 15 mins
        if self._is_cache_fresh(last_fetched):
            print("--> Local file fresh (< 15 mins old). Reusing raw JSON from disk...")
            with open(self.raw_data_file, "r", encoding="utf-8") as f:
                return json.load(f)

        params = self._build_url_parameters()

        print(f"--> Sending HTTP GET request to Strømligning API ({params['priceArea']} area)...")
        response = requests.get(BASE_URL, params=params)

        if response.status_code == 429:
            print("WARNING [429]: Rate limit reached (5-10 reqs / 15m limit). Reading cached file.")
            if self.raw_data_file.exists():
                with open(self.raw_data_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            raise RuntimeError("Rate limited by Strømligning API with no local cache available.")

        response.raise_for_status()

        # Save raw output directly to disk
        raw_json_data = response.json()
        print(f"<-- {response.status_code} OK: Writing raw electricity JSON to '{RAW_DATA_FILE}'...")

        with open(self.raw_data_file, "w", encoding="utf-8") as f:
            json.dump(raw_json_data, f, indent=2, ensure_ascii=False)

        # Record fetch timestamp
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._save_cache_meta(now_iso)

        return raw_json_data


# ==============================================================================
# EXECUTION
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch electricity prices.")
    parser.add_argument("--datafolder", help="Root data folder; overrides datafolder in electricity_config.json.")
    args = parser.parse_args()

    client = StromligningClient(config_path=CONFIG_FILE, datafolder=args.datafolder)
    data = client.get_prices()

    print("\n--- Electricity Pricing Summary ---")
    print(f"Price Area:  {client.config.get('price_area')}")
    print(f"Attribution: {client.attribution_text}")
    if isinstance(data, list) and len(data) > 0:
        print(f"Entries:     {len(data)} hourly data points downloaded.")
    elif isinstance(data, dict):
        print(f"Keys:        {list(data.keys())}")
    print("-----------------------------------\n")