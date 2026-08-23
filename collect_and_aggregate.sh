#!/bin/sh

if [ "$#" -ne 1 ]; then
	printf 'Usage: %s DATAFOLDER\n' "$0" >&2
	exit 2
fi

datafolder="$1"

python3 weather/yr_weather_client.py --datafolder "$datafolder" &
python3 electricity/stromligning_client.py --datafolder "$datafolder"
python3 price_weather_aggregator.py --datafolder "$datafolder" --location-file weather/location.json