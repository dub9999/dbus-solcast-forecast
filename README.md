# dbus-solcast-forecast

Developped to adjust the max discharge power pulled from the battery on ESS systems ('com.victronenergy.settings' /Settings/CGwacs/MaxDischargePower) to the following conditions during the next 48 hours:

- the energy pulled from the battery is maximized but battery should recharge fully within the period. To fullfill this condition, the energy pulled from the battery must be balanced by the excess of solar production that can be fed into the battery rather than exported to grid.
- the battery state of charge does not reach the minimum soc (unless grid fails) during the next 48 hours

This allows to maximize the cycling of the battery in case there is no financial interest to prioritize battery or grid at different times during the day.
(valid when import and feed-in tariffs are constant, like actually with the regulated tariff in France).

A calculation runs every 3 hours (to remain within the 10 query per day authorized by Solcast for hobbyist accounts) and set for the next 48 hours on a 30 mn period interval:
- The period end in local time formatted like "YYYY-mm-dd HH:MM:SS".
- The production forecast based on solcast pv-estimate (/Energy/Produced).
- The local consumption based on the last 24 hours consumption history (/Energy/Consumed).
- The energy discharged from the battery (/Energy/Released) based on consumption, production and GridSetpoint  .
- The energy transferred back to the battery (/Energy/Retained) based on production and consumption.
- The energy imported from the grid (/Energy/Imported.
- The energy exported to the grid (/Energy/Exported).
- The forecasted battery soc (/BatterySoc) at the end of the period.


At init:
- The 24 hours consumption history is loaded from a file in the directory defined to save the values.
  if no consumption history is available, the program is interrupted at initialization.
  A model of the file with consumption values set to 0.0 is available in the repository to show the expected format.
  This file must be manually adjusted prior to run the program the first time.
  During the loop, the 30 mn consumption values are automatically updated in the file.
  so that after 24 hours the file reflects the reality of the site.
- A dbus service named.com.victronenergy.forecast is created to store the forecasted values.
- A path /AuthorizeWriteMaxDischargePower is created with boolean value set to 1 (value is writable to change config if needed, see below)
- A path /forecast/NN/subpaths with NN from 00 to 95 and subpaths reflecting the paths for the 8 calculated values (see above)

Then a glib loop is created and runs on a 250ms interval:
- Consumption history is calculated every 30mn for the period that just ended.
- Production forecast is updated every 3 hours by a Curl query to Solcast API.
  If the API return an error, the error is logged but the program continues until next update time is reached.
  If the API query is successful, the full calculation runs.
- The results are automatically pushed to the DBus ending with a new value for /Settings/CGwacs/MaxDischargePower.

Nota:
- The value of /AuthorizeWriteMaxDischargePower is writable. If value is set to 1 as done when python code is launched without -s attribute, the result of the calculation is pushed to 'com.victronenergy.settings /Settings/CGwacs/MaxDischargePower'.
- Calling the python code with argument -s or --skip will set to 0 the value /AuthorizeWriteMaxDischargePower
- Value of /AuthorizeWriteMaxDischargePower can also be changed using dbus command from console while the forecast is running
- If /AuthorizeWriteMaxDischargePower is set to 0, the code will continue to run as usual except 'com.victronenergy.settings' /Settings/CGwacs/MaxDischargePower value will not be written. This is a way to keep the code running without interacting with the normal Multiplus operation

This repository must be installed on /data to survive to firmware updates

To install :
- Copy the repository as '/data/dbus-solcast-forecast' with all files and folders.
- Adjust the empty file 'solcast_url.cfg' with the complete solcast API url for the site including api_key parameter.
- Open 'solcastforecast.py' and adjust the constant DEFAULT_SAVE_PATH to show where program must read and save the consumption history file and where to find the log file. The actual default saving path is set to usb key: /run/media/sda1. If DEFAULT_SAVE_PATH is not accessible, the current folder is used.
- Copy the file named 'consumption_history.json' to the default saving path and adjust 30 mn values with realistic 30mn consumption values for the site. To start working with wrong consumption values and wait for the values to be automatically adjusted by the code, call the code with argument -s and let it run for hours. In any case, the file named 'consumption_history.json' must exists at the expected path with correct json data structure).

To lauch manually from console without options, type the command './run.sh' while in the /data/dbus-solcast-forecast folder.

Nota: do not forget to make 'run.sh' file executable after transferring the module in the Multiplus.

To lauch automatically at system start up, insert a 'rc.local' file in '/data' with the following instructions (or add the instruction to the 'rc.local' file if it exists): python3 /data/dbus-solcast-forecast/solcastforecast.py

To stop the program nicely, just create an empty file named 'kill' in the '/data/dbus-solcast-forecast' folder. This will result in having the actual consumption forecast saved at the location used to save the values. The file named 'kill' will be deleted automatically.

# Sources used to develop this code

This project has been possible thanks to the information and codes provided by Victron on their web site and their GitHub space.

A great thanks to Victron for sharing all these stuff.

The following repositories have been a very valuable source of information:

https://github.com/victronenergy/dbus-modbus-client

https://github.com/victronenergy/velib_python


