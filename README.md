# dbus-solcast-forecast

## Tested hardware setup
- Multiplus-II GX 48/3000/35-32
- 3 x US2000C Pylontech battery connected via CAN-bus
- 1 x HD Wave SE3000H Solaredge inverter with Solaredge modbus meter. Both connected to the Multiplus via Modbus TCP using a modified version of https://github.com/victronenergy/dbus-modbus-client

## Purpose
Adjust the max discharge power pulled from the battery on ESS systems ('com.victronenergy.settings' /Settings/CGwacs/MaxDischargePower) to fulfill the following conditions during the next 48 hours:

- the energy pulled from the battery is maximized but battery should recharge fully within the period. To fullfill this condition, the energy pulled from the battery must be balanced by the excess of solar production that can be fed into the battery rather than exported to grid.
- the battery state of charge does not reach the minimum soc (unless grid fails) during the next 48 hours

This allows to maximize the cycling of the battery in case there is no financial interest to prioritize battery or grid at different times during the day.
(valid when import and feed-in tariffs are constant, like actually with the regulated tariff in France).

## Details
At init:
- The 24 hours consumption history is loaded from a file in the directory defined to save the values.
  if no consumption history is available, the program is interrupted at initialization.
  A model of the file with consumption values set to 0.0 is available in the repository to show the expected format.
  This file must be manually adjusted prior to run the program the first time.
  During the loop, the 30 mn consumption values are automatically updated in the file.
  so that after 24 hours the file reflects the reality of the site.
- A dbus service named com.victronenergy.forecast is created to store the forecasted values.
- The following paths are created:
  - /AuthorizeWriteMaxDischargePower is created with boolean value set to 1 (value is writable to change config if needed, see below)
  - /TotalForecastedProduction and /TotalForecastedConsumption to show the cumulated production and cummulated consumption expected in the next 48 hours
  - /Lists/'subpath'/0 and /Lists/'subpath'/1 with 'subpaths' reflecting the access for the 8 calculated values
    - Battery Soc 
    - Self-consumed
    - Consumed
    - Produced from PV
    - Discharged from the battery
    - Stored in the battery
    - Imported from grid
    - Exported to grid
    The period of interest is 48 hours starting current day at 00:00.
    The values of each 30 mn are average power, in 10W, integer to allow creating a json list of 48 entries with less than 256 characters for compatibility with home assistant maximum length of MQTT sensor states.
    - Values for period in the past are real values.
    - Values in the future are forecast.
    - path /Lists/'subpath'/0 contains values of today.
    - path /Lists/'subpath'/1 contains values of tomorrow.

After initialization a glib loop is created and runs on a 250ms interval:
- Every 30 mn:
  - calculate cumulated values for the last 30 mn period
  - calculate forecasted values for each 30 mn period based on the last forecast retrieved fro solcast api 
  - Calculation also returns
    - The expected cumulated production
    - The expected cumulated consumption
    - The optimized value for the maximum discharge power of the battery
- Every 3 hours:
  - update the production forecast through a Curl query to Solcast API
- Every day at 00:00:
  - reset all values
- If the solcast API returns an error, the error is logged and the calculation is not processed but the glib loop continues.
- If everything go smooth, the results are published on the DBus.

About 'com.victronenergy.forecast /AuthorizeWriteMaxDischargePower':
- The value is of type boolean and is writable. 
- If value is set to 1 as done when python code is launched without the -s attribute, the result of the calculation is pushed to 'com.victronenergy.settings /Settings/CGwacs/MaxDischargePower'.
- Calling the python code with argument -s or --skip will set the value to 0 
- The value can also be changed using dbus-spy or a dbus command from console while the forecast is running
- If value is set to 0, the code will run as usual except 'com.victronenergy.settings' /Settings/CGwacs/MaxDischargePower' value will not be written. This is a way to keep the code running without interacting with the normal Multiplus operation.

## Installation
### Nota:
- Registration of a user account at Solcast is required to obtain an API key https://solcast.com.au/api/register.
- Without an API key there is no chance to successfully obtain valid API results.

This repository must be installed on /data to survive to firmware updates.
- Create a repository '/data/projects/dbus-solcast-forecast' in the venus device and copy all files and subfolders of this repository.
- Adjust the empty file 'solcast_url.cfg' with the complete solcast API url for the site including api_key parameter.
- Open 'solcastforecast.py' and adjust the constant DEFAULT_SAVE_PATH to show where program must read and save the consumption history file and where to find the log file. The actual default saving path is set to usb key: /run/media/sda1. If DEFAULT_SAVE_PATH is not accessible, the current folder is used.
- Copy the file named 'consumption_history.json' to the default saving path and adjust 30 mn values with realistic 30mn consumption values for the site. To start working with wrong consumption values and wait for the values to be automatically adjusted by the code, call the code with argument -s and let it run for hours. In any case, the file named 'consumption_history.json' must exists at the expected path with correct json data structure).

To lauch manually from console without options, type the command './run.sh' while in the /data/projects/dbus-solcast-forecast folder.

Nota: do not forget to make 'run.sh' file executable after transferring the module in the Multiplus.

To lauch automatically at system start up, insert a 'rc.local' file in '/data' with the following instructions (or add the instruction to the 'rc.local' file if it exists): python3 /data/projects/dbus-solcast-forecast/solcastforecast.py

To stop the program nicely, just create an empty file named 'kill' in the '/data/projects/dbus-solcast-forecast' folder. This will result in having the actual consumption history saved at the location used to save the values. The file named 'kill' will be deleted automatically.

## Sources used to develop this code and thanks

This project has been possible thanks to the information and codes provided by Victron on their web site and their GitHub space.

A great thanks to Victron for sharing all these stuff.

The following repositories have been a very valuable source of information:

https://github.com/victronenergy/dbus-modbus-client

https://github.com/victronenergy/velib_python


