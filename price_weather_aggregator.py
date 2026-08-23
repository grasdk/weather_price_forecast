import datetime
import argparse
import json
import os
from pathlib import Path
import zoneinfo

# ==============================================================================
# FILE PATH CONSTANTS
# ==============================================================================
LOCATION_FILE = "weather/location.json"
ELECTRICITY_CONFIG_FILE = "electricity/electricity_config.json"
WEATHER_FILE = "raw_forecast.json"
ELECTRICITY_FILE = "raw_electricity_prices.json"
OUTPUT_FILE = "aggregated_data.json"
CONFIG_FILE = Path(__file__).resolve().parent / "aggregator_config.json"

TIMEZONE_NAME = "Europe/Copenhagen"
RETENTION_DAYS = 7
MATCH_TOLERANCE_SECONDS = 3600  # 1 hour maximum distance for nearest match


def load_json(filepath: str):
    """Safely loads JSON content from disk."""
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_iso_utc(date_str: str) -> datetime.datetime:
    """Parses an ISO 8601 string into a UTC datetime object."""
    clean_str = date_str.replace(".000Z", "Z").replace("Z", "+00:00")
    return datetime.datetime.fromisoformat(clean_str).astimezone(datetime.timezone.utc)


def find_nearest_entry(target_dt: datetime.datetime, sorted_entries: list, max_delta_sec: int = MATCH_TOLERANCE_SECONDS):
    """Finds the closest entry to target_dt within max_delta_sec tolerance."""
    if not sorted_entries:
        return None
    
    best_entry = None
    best_diff = float("inf")
    
    for dt, data in sorted_entries:
        diff = abs((dt - target_dt).total_seconds())
        if diff < best_diff:
            best_diff = diff
            best_entry = data
        elif diff > best_diff:
            # Entries are sorted by datetime, so distance will only increase
            break
            
    if best_diff <= max_delta_sec:
        return best_entry
    return None


def extract_weather_fields(step: dict) -> dict:
    """Extracts structured weather variables from a MET Norway forecast step."""
    instant = step.get("data", {}).get("instant", {}).get("details", {})
    next_1h = step.get("data", {}).get("next_1_hours", {})
    next_6h = step.get("data", {}).get("next_6_hours", {})

    icon_name = None
    if "summary" in next_1h:
        icon_name = next_1h["summary"].get("symbol_code")
    elif "summary" in next_6h:
        icon_name = next_6h["summary"].get("symbol_code")

    precip = None
    precip_max = None
    if "details" in next_1h:
        precip = next_1h["details"].get("precipitation_amount")
        precip_max = next_1h["details"].get("precipitation_amount_max")
    elif "details" in next_6h:
        precip = next_6h["details"].get("precipitation_amount")
        precip_max = next_6h["details"].get("precipitation_amount_max")

    return {
        "temperature_c": instant.get("air_temperature"),
        "wind_speed_ms": instant.get("wind_speed"),
        "wind_gust_ms": instant.get("wind_speed_of_gust"),
        "wind_direction_deg": instant.get("wind_from_direction"),
        "precipitation_mm": precip,
        "precipitation_max_mm": precip_max,
        "icon_name": icon_name,
    }


def aggregate_data(
    config_path: str = CONFIG_FILE,
    datafolder: str = None,
    location_file: str = None,
    electricity_config_file: str = None
):
    config = load_json(config_path) or {}
    config_folder = Path(config_path).resolve().parent
    configured_datafolder = datafolder or config.get("datafolder", ".")
    data_root = Path(configured_datafolder).expanduser()
    if datafolder is None:
        data_root = config_folder / data_root
    data_root = data_root.resolve()
    configured_location_file = location_file or config.get("location_file", LOCATION_FILE)
    location_path = Path(configured_location_file).expanduser()
    if not location_path.is_absolute():
        location_path = config_folder / location_path
    configured_electricity_config_file = electricity_config_file or config.get(
        "electricity_config_file", ELECTRICITY_CONFIG_FILE
    )
    electricity_config_path = Path(configured_electricity_config_file).expanduser()
    if not electricity_config_path.is_absolute():
        electricity_config_path = config_folder / electricity_config_path
    weather_file = data_root / WEATHER_FILE
    electricity_file = data_root / ELECTRICITY_FILE
    output_file = data_root / OUTPUT_FILE

    local_tz = zoneinfo.ZoneInfo(TIMEZONE_NAME)
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    cutoff_dt = now_utc - datetime.timedelta(days=RETENTION_DAYS)

    # --------------------------------------------------------------------------
    # 1. LOAD AND PARSE DATASETS
    # --------------------------------------------------------------------------
    location_raw = load_json(location_path)
    location_name = "Them"
    if location_raw and isinstance(location_raw, dict):
        location_name = location_raw.get("name", "Them")

    electricity_config_raw = load_json(electricity_config_path)
    electricity_prod_name = None
    if electricity_config_raw and isinstance(electricity_config_raw, dict):
        electricity_prod_name = electricity_config_raw.get("product_name")

    weather_raw = load_json(weather_file)
    price_raw = load_json(electricity_file)

    price_entries = []
    if isinstance(price_raw, list):
        for entry in price_raw:
            price_entries.append((parse_iso_utc(entry["date"]), entry.get("price")))
    price_entries.sort(key=lambda x: x[0])

    weather_entries = []
    if weather_raw and "properties" in weather_raw and "timeseries" in weather_raw["properties"]:
        for step in weather_raw["properties"]["timeseries"]:
            weather_entries.append((parse_iso_utc(step["time"]), step))
    weather_entries.sort(key=lambda x: x[0])

    existing_records = {}
    old_aggregated_file = load_json(output_file)
    if old_aggregated_file and "hourly" in old_aggregated_file:
        for rec in old_aggregated_file["hourly"]:
            existing_records[rec["time_utc"]] = rec

    # --------------------------------------------------------------------------
    # 2. DETERMINE COMPLETE UNIFIED TIME HORIZON
    # --------------------------------------------------------------------------
    all_dts = [dt for dt, _ in price_entries] + [dt for dt, _ in weather_entries]
    for key in existing_records.keys():
        all_dts.append(parse_iso_utc(key))

    if not all_dts:
        print("No valid timestamps found across inputs.")
        return

    # Bound start date by retention policy
    start_dt = max(min(all_dts), cutoff_dt).replace(minute=0, second=0, microsecond=0)
    max_dt = max(all_dts).replace(minute=0, second=0, microsecond=0)

    # Generate hourly step grid forward through maximum available forecast
    hourly_grid = []
    curr = start_dt
    while curr <= max_dt:
        hourly_grid.append(curr)
        curr += datetime.timedelta(hours=1)

    # --------------------------------------------------------------------------
    # 3. AGGREGATE USING CLOSEST MATCHING
    # --------------------------------------------------------------------------
    aggregated_map = {}

    for dt_utc in hourly_grid:
        time_utc_str = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        dt_local = dt_utc.astimezone(local_tz)

        # Base record from historical file (if existing)
        record = existing_records.get(time_utc_str, {
            "time_local": dt_local.isoformat(),
            "time_utc": time_utc_str,
            "temperature_c": None,
            "wind_speed_ms": None,
            "wind_gust_ms": None,
            "wind_direction_deg": None,
            "precipitation_mm": None,
            "precipitation_max_mm": None,
            "icon_name": None,
            "price_dkk": None
        })

        # Nearest weather lookup
        matched_weather_step = find_nearest_entry(dt_utc, weather_entries)
        if matched_weather_step:
            weather_fields = extract_weather_fields(matched_weather_step)
            record.update(weather_fields)

        # Nearest electricity price lookup
        matched_price = find_nearest_entry(dt_utc, price_entries)
        if matched_price is not None:
            record["price_dkk"] = matched_price

        aggregated_map[time_utc_str] = record

    # Filter out records older than retention period & sort output
    filtered_hourly = [
        aggregated_map[k] for k in sorted(aggregated_map.keys())
        if parse_iso_utc(k) >= cutoff_dt
    ]

    output_payload = {
        "metadata": {
            "location": location_name,
            "electricity_prod_name": electricity_prod_name,
            "generated_at_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timezone": TIMEZONE_NAME,
            "retention_days": RETENTION_DAYS,
            "total_hours": len(filtered_hourly),
            "attribution": "Electricity: Strømligning.dk | Weather: MET Norway / Yr.no"
        },
        "hourly": filtered_hourly
    }

    # --------------------------------------------------------------------------
    # 4. SAVE OUTPUT
    # --------------------------------------------------------------------------
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2, ensure_ascii=False)

    print(f"Successfully aggregated {len(filtered_hourly)} hours into '{output_file}'.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Aggregate weather and electricity data.")
    parser.add_argument("--datafolder", help="Root data folder; overrides datafolder in aggregator_config.json.")
    parser.add_argument(
        "--location-file",
        help="Location JSON file; overrides location_file in aggregator_config.json."
    )
    parser.add_argument(
        "--electricity-config-file",
        help="Electricity config JSON file; overrides electricity_config_file in aggregator_config.json."
    )
    args = parser.parse_args()
    aggregate_data(
        datafolder=args.datafolder,
        location_file=args.location_file,
        electricity_config_file=args.electricity_config_file
    )