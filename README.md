# Weather and electricity data

# .py files
Simple python programs to download 

1. weather yr.no forecast following their requirements on: https://developer.yr.no/doc/locationforecast/HowTO/
2. prices from stromligning.dk API

Please update location.json and site_info.json to suit match your own usage. Read the documentation at the link above, if you fake the info or make it generic, you might get permanently banned from using their service.

## Data folder

The scripts use the folder `datadir` root by default.
The root can be configured with `datafolder` in the relevant JSON config file,
or overridden on the command line with `--datafolder`. Command-line values
take precedence over config values.

## Run with

```
./collect_and_aggregate.sh DATADIR
```

`collect_and_aggregate.sh` requires the data folder as its only command-line argument.

### Run each script individually

```
python3 weather/yr_weather_client.py --datafolder datadir
python3 electricity/stromligning_client.py --datafolder datadir
python3 price_weather_aggregator.py --datafolder datadir --location-file weather/ location.json --electricity-config-file electricity/electricity_config.json
```