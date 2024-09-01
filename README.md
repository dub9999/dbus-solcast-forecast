# dbus-solcast-forecast

Developped to adjust the max power pulled from the battery on ESS systems
to ensure that during the next 48 hours:
- the energy pulled from the battery is balanced by the excess of solar production that can be fed into the battery
- the battery soc does not go beyond the minimum soc (unless grid fails) during the next 48 hours

Purpose is to set the value at path /Settings/CGwacs/MaxDischargePower for service 'com.victronenergy.settings'

Calculation is made using:
- A forecast of the solar production retrieved from Solcast API: pv_estimate provided on a 30mn period interval
- A forecast of the consumption based on the last 24 hours consumption also calculated on a 30 mn period interval

Solar forecast query and MaxDichargePower calculation run every 3 hours
(to remain within the 10 queries per day authorized by Solcast for hobbyist accounts) 

Forecast consumption is updated every 30 mn (for the value of the period ending)

Allows to maximize the cycling of the battery in case there is no financial interest to prioritize any period of the day
(valid when import and feed in tariffs are constant, like in France)

Reads battery current and voltage values on the dbus at a high rate interval (100 ms)
  and calculate how much energy has been exchanged with the battery during this interval
  A positive value is added to the /Historic/ChargedEnergy value
  A negative value is substracted to /Historic/DischargedEnergy value
Writes both calculated values on the dbus
Saves the values once per hour either on a usb key if mounted on '/run/media/sda1' or on the current folder if no usb key is mounted

At init, checks if saved consumption forecast is available in the directory defined to save the values
  if yes use the values to initialize the consumption forecast
  if not consumption forecast is set to 0 and max discharge power as well until a full 24h consumption forecast is available
  
To stop the program nicely, create a file named kill in the module directory
  This will result in having the actual consumption forecast saved at the location used to save the values

Module must be installed on /data to survive to firmware updates

To lauch automatically at system start up, insert a rc.local file in /data
  with the following instructions (or add the instruction to the rc.local file if it exists)
  python3 /data/dbus-battery-monitor/solcastforecast.py

To lauch manually from console ./run.sh while in the /data/dbus-solcast-forecast folder
Nota: do not forget to make run.sh file executable after transferring the module in the Multiplus


