# Weather and electricity data

# .py files
Simple python programs to download weather yr.no forecast following their requirements on: https://developer.yr.no/doc/locationforecast/HowTO/

Please update location.json and site_info.json to suit match your own usage. Read the documentation at the link above, if you fake the info or make it generic, you might get permanently banned from using their service.

## Data folder

The scripts use the script folder as the data root by default.
The root can be configured with `datafolder` in the relevant JSON config file,
or overridden on the command line with `--datafolder`. Command-line values
take precedence over config values.

## Run with

```cd weather/
python3 yr_weather_client.py [--datafolder PATH]
cd ../electricity/
python3 stromligning_client.py [--datafolder PATH]
cd ..
python3 price_weather_aggregator.py [--datafolder PATH] [--location-file PATH] [--electricity-config-file PATH]
./collect_and_aggregate.sh PATH
```

`collect_and_aggregate.sh` requires the data folder as its only command-line argument.