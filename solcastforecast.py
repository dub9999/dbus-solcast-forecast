#!/usr/bin/env python3 -u
# -u to force the stdout and stderr streams to be unbuffered

from argparse import ArgumentParser
import dbus
import dbus.mainloop.glib
import dbus.service
import faulthandler
import signal
import os
import sys
from time import tzset
from datetime import datetime, timedelta, timezone
import traceback
from gi.repository import GLib

# Import des modules locaux (sous dossier /ext/velib_python)
sys.path.insert(1, os.path.join(os.path.dirname(__file__), 'ext', 'velib_python'))
from vedbus import VeDbusService, VeDbusItemImport

import logging
log = logging.getLogger()

import json

NAME = os.path.basename(__file__)
VERSION = "0.1"

__all__ = ['NAME', 'VERSION']

UPDATE_INTERVAL = 250

# Ajustement du fuseau horaire 
os.environ['TZ'] = 'Europe/Paris'
tzset()

class ConsumptionCalculator(object):

  def __init__(self):
    self._bus=dbus.SessionBus() if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else dbus.SystemBus()
    self._meters={
      'released' : {'service' : 'com.victronenergy.battery.socketcan_can0', 'path' : '/History/DischargedEnergy', 'value' : None, 'gap' : None},
      'retained' : {'service' : 'com.victronenergy.battery.socketcan_can0', 'path' : '/History/ChargedEnergy', 'value' : None, 'gap' : None},
      'imported' : {'service' : 'com.victronenergy.grid.se_203', 'path' : '/Ac/Energy/Forward', 'value' : None, 'gap' : None},
      'exported' : {'service' : 'com.victronenergy.grid.se_203', 'path' : '/Ac/Energy/Reverse', 'value' : None, 'gap' : None},
      'produced' : {'service' : 'com.victronenergy.pvinverter.se_101', 'path' : '/Ac/Energy/Forward', 'value' : None, 'gap' : None},
    }
    self._dbus_objects={}
    self._consumption=0.0

  #to initialize        
  def init(self):
    for name, meter in self._meters.items():
      #initialize dbus objects
      self._dbus_objects[name] = VeDbusItemImport(self._bus, meter['service'], meter['path'])
    return True

  #to update the indexes values and gap registered during the period
  def update(self, old_value):
    updated_meters_count=0 # to count if all meters have been updated
    for name, meter in self._meters.items():
      log.debug(f'name: {name} - meter: {meter}')
      #read meter values, calculate gap to old values, store as gaps and store values
      new_value = self._dbus_objects[name].get_value()
      if  new_value is not None: 
        if meter['value'] is not None:
          meter['gap'] = new_value - meter['value']
          updated_meters_count+=1
        meter['value'] = new_value
      log.debug(f'name: {name} - meter: {meter}')
    #
    log.debug(f'updated_meters_count: {updated_meters_count}')
    if updated_meters_count==5:
      self._consumption = self._meters['released']['gap'] - self._meters['retained']['gap'] \
        + self._meters['imported']['gap'] - self._meters['exported']['gap'] + self._meters['produced']['gap']
    else:
      self._consumption = old_value
    #
    log.debug(f'consumption: {self._consumption}')
    return self._consumption

class SolcastForecast(object):

  def __init__(self):
    #name of the dbus service where to publish calculated values
    self._dbus_service_name = 'com.victronenergy.forecast'
    #initialize forecast variable
    self._dbus_service_params={
      'period_end' : {'path' : '/PeriodEnd', 'value' : "0000-00-00 00:00:00"},
      'battery_soc' : {'path' : '/BatterySoc', 'value' : 0},
      'consumed' : {'path' : '/Energy/Consumed', 'value' : 0},
      'produced' : {'path' : '/Energy/Produced', 'value' : 0},
      'imported' : {'path' : '/Energy/Imported', 'value' : 0},
      'exported' : {'path' : '/Energy/Exported', 'value' : 0},
      'retained' : {'path' : '/Energy/Retained', 'value' : 0},
      'released' : {'path' : '/Energy/Released', 'value' : 0},
    }
    self._dbus_import_params={
      'grid_sp' : {'service' : 'com.victronenergy.settings', 'path' : '/Settings/CGwacs/AcPowerSetPoint', 'value' : 30},
      'out_max' : {'service' : 'com.victronenergy.settings', 'path' : '/Settings/CGwacs/MaxDischargePower', 'value' : 1000},
      'soc_min' : {'service' : 'com.victronenergy.settings', 'path' : '/Settings/CGwacs/BatteryLife/MinimumSocLimit', 'value' : 15},
      'bat_soc' : {'service' : 'com.victronenergy.battery.socketcan_can0', 'path' : '/Soc', 'value' : 15},
      'bat_soh' : {'service' : 'com.victronenergy.battery.socketcan_can0', 'path' : '/Soh', 'value' : 98},
      'bat_cap' : {'service' : 'com.victronenergy.battery.socketcan_can0', 'path' : '/InstalledCapacity', 'value' : 150},
    }
    self._dbus_imports={}
    #initialize path to access files where some data are stored
    #preferrably usb key if available
    if os.path.exists('/run/media/sda1'):
      self._file_path='/run/media/sda1'
    #otherwise in this file directory
    else:
      self._file_path=os.getcwd()
    #other attributes
    self._url = None                      #to store the Solcast API url (site dependent)
    self._prod={}                         #to store the 96 h forecast retrieved from solcast API
    self._cons={}                         #to store the 24 h recorded consumption 
    self._out_max=0
    self._forecast_update_called=True     #to authorize calling the update method at the expected time only once
    #set to True to launch the forecast update if program starts at time when update is supposed to be called
    self._consumption_update_called=False #to authorize calling the update method at the expected time only once
    #set to False to prevent launching the consumption update if program starts at time when update is supposed to be called

  #to read the solcast url in a configuration file stored in the working folder as it is site specific
  def __read_url__(self):
    url = None
    filename=os.getcwd()+'/solcast_url.cfg'
    if os.path.isfile(filename):
      f = open(filename, "r")
      self._url=f.read()
      f.close()
      return True
    else:
      return False


  #to load the consumption history saved in a file (24h consumption on 30mn interval, json format)
  def __read_cons__(self):
    filename=self._file_path+'/cons_history.json'
    if os.path.isfile(filename):
      with open(filename, mode="r", encoding="utf-8") as file:
        self._cons = json.load(file)
      return True
    else:
      return False

  #to save consumption history as a json into a file
  def __save_cons__(self, content):
    filename=self._file_path+'/cons_history.json'
    with open(filename, mode="w", encoding="utf-8") as file:
      json.dump(content, file)

  #to load the production forecast from a json saved in a file (for testing purpose only)
  def __read_prod__(self):
    filename=self._file_path+'/prod_forecast.json'
    if os.path.isfile(filename):
      with open(filename, mode="r", encoding="utf-8") as file:
        self._prod = json.load(file)
      return True
    else:
      return False

  #to save production forecast as a json into a file
  def __save_prod__(self, content):
    filename=self._file_path+'/prod_forecast.json'
    with open(filename, mode="w", encoding="utf-8") as file:
      json.dump(content, file)

  #to retrieve the production forecast from the solcast url
  def __curl_prod__(self):
    log.debug('Calling Solcast API url')
    self._prod=json.load(os.popen("curl -s "+self._url))
    if "forecasts" in self._prod:
      return True
    elif "response_status" in self._prod and "error_code" in self._prod["response_status"]:
      log.error(f'Error received from Solcast API: {self._prod["response_status"]["error_code"]}')
    else:
      log.error('Unidentified error when contacting Solcast API')
    return False

  #to initialize the interface with dbus
  def __init_dbus__(self):
    #initialize the bus to connect to
    self._dbus_bus=dbus.SessionBus() if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else dbus.SystemBus()

    #create the VeDbusService
    self._dbus_service = VeDbusService(self._dbus_service_name, register=False)
    #add the required paths
    for i in range(96):
      for name, item in self._dbus_service_params.items():
        self._dbus_service.add_path(f'/{i:02d}{item["path"]}',item["value"])
    #claim the service name on dbus only if not already existing
    if self._dbus_service_name not in self._dbus_bus.list_names():
      self._dbus_service.register()
    
    #import the dbus objects
    for name, item in self._dbus_import_params.items():
      self._dbus_imports[name] = VeDbusItemImport(self._dbus_bus, item['service'], item['path'])

  #to refresh the imported objects
  def __read_dbus__(self):
    for name, item in self._dbus_import_params.items():
      item['value'] = self._dbus_imports[name].get_value()

  #to update the energy consumption in a period (period_end must be local time provided in format "%H:%M")
  def __update_cons__(self, period_end):
    log.debug(f'Consumption of the period before update: {self._cons[period_end]}')
    self._cons[period_end] = round(self._consumption_calculator.update(self._cons['period_end']), 2)
    log.debug(f'Consumption of the period after update: {self._cons[period_end]}')

  #to calculate the max power pulled from the battery
  def __calculate_out_max__(self):
    #refresh the imported objects
    self.__read_dbus__()
    run_loop=1                              #to continue loop searching optimal value (0 to stop looping)
    iteration=0                             #loop counter (stop after 10 loops maxi)
    out_top=2000                            #absolute max for out_max
    sp_min=0                                #lower cap of the interval for the regression
    sp_max=out_top                          #upper cap of the interval for the regression
    soc_max=0                               #max forecasted battery soc (initialized at 0, recalculated after each loop)
    soc_min=100                             #min forecasted battery soc (initialized at 100, recalculated during each loop)
    self._out_max = (sp_min+sp_max)/2        #max power output (start in the middle of the regression interval)
    #start the loops trying to find the optimal power output to maintain forecasted battey soc between soc_min+5% and 95%
    #stop after 10 iterations in any case
    for iteration in range(10):
      #loop all the records of the solar production forecast (96 x 30 minutes periods)
      #use enumerate to keep track of the item index
      for index, item in enumerate(self._prod['forecasts']):
        if index>95:
          break
        #adjust period_end value to local time
        period_end=datetime.strptime(item["period_end"], "%Y-%m-%dT%H:%M:%S.0000000Z") \
          .replace(tzinfo=timezone(timedelta(seconds=0), 'UTC')).astimezone()
        self._dbus_service[f'/{index:02d}/PeriodEnd']=datetime.strftime(period_end,"%Y-%m-%d %H:%M:%S")
        #store the battery soc at the beginning of the period
        soc_prev = self._dbus_import_params['bat_soc']['value'] if index == 0 else self._dbus_service[f'/{index-1:02d}/BatterySoc']
        #retrieve the forecasted production for the period
        self._dbus_service[f'/{index:02d}/Energy/Produced'] = item['pv_estimate']
        #retrieve the forecasted consumption for the period
        self._dbus_service[f'/{index:02d}/Energy/Consumed']= self._cons[datetime.strftime(period_end,"%H:%M")]
        #calculate energy discharged from the battery
        self._dbus_service[f'/{index:02d}/Energy/Released'] = min(self._dbus_import_params['bat_soh']['value']/100 \
          *self._dbus_import_params['bat_cap']['value']*0.048*(soc_prev-self._dbus_import_params['soc_min']['value'])/100, \
          min(self._out_max/2000, max(0, self._dbus_service[f'/{index:02d}/Energy/Consumed'] \
          -self._dbus_service[f'/{index:02d}/Energy/Produced']-self._dbus_import_params['grid_sp']['value']/2000 )))
        #calculate energy charged into the battery
        self._dbus_service[f'/{index:02d}/Energy/Retained'] = min(self._dbus_import_params['bat_soh']['value']/100 \
          *self._dbus_import_params['bat_cap']['value']*0.052*(100-soc_prev)/100, \
          max(0, self._dbus_service[f'/{index:02d}/Energy/Produced']-self._dbus_service[f'/{index:02d}/Energy/Consumed']))
        #calculate exchanges with grid
        self._dbus_service[f'/{index:02d}/Energy/Imported'] = max(0, self._dbus_service[f'/{index:02d}/Energy/Consumed'] \
          -self._dbus_service[f'/{index:02d}/Energy/Produced']-self._dbus_service[f'/{index:02d}/Energy/Released'])
        self._dbus_service[f'/{index:02d}/Energy/Exported'] = max(0, self._dbus_service[f'/{index:02d}/Energy/Produced'] \
          -self._dbus_service[f'/{index:02d}/Energy/Consumed']-self._dbus_service[f'/{index:02d}/Energy/Retained'])
        #calculate the battery soc at the period end
        #soc is calculated using Ah battery capacity
        #with 52V charge voltage and 48V discharge voltage
        self._dbus_service[f'/{index:02d}/BatterySoc'] = soc_prev + \
          (self._dbus_service[f'/{index:02d}/Energy/Retained']/0.052 - self._dbus_service[f'/{index:02d}/Energy/Released']/0.048) \
          / (self._dbus_import_params['bat_soh']['value'] * self._dbus_import_params['bat_cap']['value']) * 10000
        #update soc_max and soc_min
        soc_max=max(soc_max, self._dbus_service[f'/{index:02d}/BatterySoc'])
        soc_min=min(soc_min, self._dbus_service[f'/{index:02d}/BatterySoc'])
        #update the total_cons and total_prod

      #update regression interval and continue loop if soc is going below lower limit
      if soc_min < self._dbus_import_params['soc_min']['value']+5:
        run_loop=1
        sp_max=self._out_max
        self._out_max=(self._out_max+sp_min)/2
      #update regression interval and continue loop if soc is going above upper limit
      elif soc_max > 95:
        run_loop=1
        sp_min=self._out_max
        self._out_max=(sp_max+self._out_max)/2
      else:
        break
    #if regression did not find optimum and reached lower value, set out_max to 0 
    if self._out_max < 2:
      self._out_max = 0
    #if regression did not find optimum and reached upper value, set out_max to out_top 
    elif self._out_max > out_top - 2:
      self._out_max = out_top
    return True

  #to update out_max on dbus
  def __update_dbus__(self):
    #write out_max only if value differs from actual one in dbus
    if abs(self._out_max - self._dbus_import_params['out_max']['value']) > 2:
      self._dbus_imports['out_max'].set_value(self._out_max)
    return True

  #pour terminer la boucle permanente de façon propre
  def __exit_program__ (self):
    log.info('Program terminated on request')
    self.__save_cons__(self._cons)
    log.info('24 h consumption history saved to file')
    log.info('end---------------------------------------------------------')
    os._exit(1)

  #to initialize        
  def init(self):
    try:
      #initialize the interface with dbus
      self.__init_dbus__()
      
      #initialize the consumption calculator
      self._consumption_calculator = ConsumptionCalculator()
      self._consumption_calculator.init()
      
      #initialize the consumption history (read from file)
      if not self.__read_cons__():
        log.info('Could not read 24h consumption history')
        log.info('Program aborted during initialization')
        log.info('end---------------------------------------------------------')
        os._exit(1)
            
      #initialize the solcast url (read from file)
      self.__read_url__()

      log.debug(f'self._consumption_update_called: {self._consumption_update_called} - self._forecast_update_called: {self._forecast_update_called}')   

    except:
      log.error('Exception occured during init', exc_info=True)
      log.info('end---------------------------------------------------------')
      os._exit(1)

  #to update the forecast
  def update(self):
    #if a file named kill exists in the folder of this file, exit the program
    if os.path.isfile(os.getcwd()+'/kill'):
      os.remove(os.getcwd()+'/kill')
      self.__exit_program__()
    #run updates when required
    #we use self._consumption_update_called and self._forecast_update_called
    # to avoid calling again and again the same update if an exception has occured
    try:
      if not(datetime.now().minute % 30) and not(self._consumption_update_called):
        self._consumption_update_called=True
        self.__update_cons__(datetime.strftime(datetime.now(),"%H:%M"))
        log.debug(f'consumption updated for period ending {datetime.strftime(datetime.now(),"%H:%M")}: \
          {self._cons[datetime.strftime(datetime.now(),"%H:%M")]}')
        self.__save_cons__(self._cons)
        log.debug('24 h consumption history saved to file')
        log.debug(f'self._consumption_update_called: {self._consumption_update_called} - self._forecast_update_called: {self._forecast_update_called}')   
      if datetime.now().minute % 30 and self._consumption_update_called:
        self._consumption_update_called=False
        log.debug(f'self._consumption_update_called: {self._consumption_update_called} - self._forecast_update_called: {self._forecast_update_called}')   
      if not(datetime.now().hour % 3) and not(self._forecast_update_called):
        self._forecast_update_called=True
        success=self.__curl_prod__()
        #self._forecast_update_called=self.__read_prod__()
        if success:
          self.__calculate_out_max__()
          self.__update_dbus__()
          log.debug(f'New value set for {self._dbus_import_params["out_max"]["path"]}: {self._out_max}')
        log.debug(f'self._consumption_update_called: {self._consumption_update_called} - self._forecast_update_called: {self._forecast_update_called}')   
      if datetime.now().hour % 3 and self._forecast_update_called:
        self._forecast_update_called=False
        log.debug(f'self._consumption_update_called: {self._consumption_update_called} - self._forecast_update_called: {self._forecast_update_called}')   
    except:
      log.error('Exception occured during update', exc_info=True)
    
    return True

def main():
  parser = ArgumentParser(add_help=True)
  parser.add_argument('-d', '--debug', help='enable debug logging',
                        action='store_true')

  args = parser.parse_args()

  logging.basicConfig(
    filename=os.getcwd()+'/solcastforecast.log', 
      format='%(asctime)s: %(levelname)-8s %(message)s', 
      datefmt="%Y-%m-%d %H:%M:%S", 
      level=(logging.DEBUG if args.debug else logging.INFO))

  log.info('start-------------------------------------------------------')
  log.info(__file__+' started')

  signal.signal(signal.SIGINT, lambda s, f: os._exit(1))
  faulthandler.register(signal.SIGUSR1)
  dbus.mainloop.glib.threads_init()
  dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
  mainloop = GLib.MainLoop()

  forecast=SolcastForecast()

  forecast.init()
  log.info('Initialisation completed, now running permanent loop')
  GLib.timeout_add(UPDATE_INTERVAL, forecast.update)
  mainloop.run()

if __name__ == '__main__':
    main()
