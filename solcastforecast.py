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

FOLDER = os.path.dirname(os.path.abspath(__file__))
DEF_PATH = "/run/media/sda1"
LOGFILE = '/projects.log'

UPDATE_INTERVAL = 250

# Adjusting time zone as system is not aligned with the time zone set in the UI 
os.environ['TZ'] = 'Europe/Paris'
tzset()

class EnergyCalculator(object):
    # all values in kWh
    def __init__(self):
        self.bus=dbus.SessionBus() if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else dbus.SystemBus()
        self.meters={
        'released' : {
                        'service' : 'com.victronenergy.battery.socketcan_can0', 
                        'path' : '/History/DischargedEnergy', 
                        'value' : None, 
                        'gap' : 0
                        },
        'retained' : {
                        'service' : 'com.victronenergy.battery.socketcan_can0', 
                        'path' : '/History/ChargedEnergy', 
                        'value' : None, 
                        'gap' : 0
                        },
        'imported' : {
                        'service' : 'com.victronenergy.grid.se_203', 
                        'path' : '/Ac/Energy/Forward', 
                        'value' : None, 
                        'gap' : 0
                        },
        'exported' : {
                        'service' : 'com.victronenergy.grid.se_203', 
                        'path' : '/Ac/Energy/Reverse', 
                        'value' : None, 
                        'gap' : 0
                        },
        'produced' : {
                        'service' : 'com.victronenergy.pvinverter.se_101', 
                        'path' : '/Ac/Energy/Forward', 
                        'value' : None, 
                        'gap' : 0
                        },
        }
        self.dbus_new_values={}
        for name, meter in self.meters.items():
            self.dbus_new_values[name]=None
        self.consumption=0.0

    #to read values on dbus      
    def __read_dbus__(self):
        dbus_objects={}
        for name, meter in self.meters.items():
            try:
                #initialize dbus objects
                dbus_objects[name] = VeDbusItemImport(self.bus, meter['service'], meter['path'])
                self.dbus_new_values[name]=dbus_objects[name].get_value()
            except:
                log.error(f'error reading {_bus, meter["service"]} {meter["path"]}')
                self.dbus_new_values[name]=None

    #to update the index values and gap of meters registered during the period and calculate consuptiom
    def update(self):
        #try to update the meters values on dbus
        self.__read_dbus__()
        updated_meters_count=0 # to count if all meters have been updated
        for name, meter in self.meters.items():
            log.debug(f'name: {name} - meter: {meter}')
            if  self.dbus_new_values[name] is not None: 
                if meter['value'] is not None:
                    meter['gap'] = self.dbus_new_values[name] - meter['value']
                    updated_meters_count+=1
                meter['value'] = self.dbus_new_values[name]
            else:
                meter['gap'] = 0
            log.debug(f'name: {name} - meter: {meter}')
        #
        log.debug(f'updated_meters_count: {updated_meters_count}')
        #
        return self.meters

class SolcastForecast(object):

    def __init__(self, auth_write):
        #to skip the update of MaxDischargePower on dbus
        self.auth_write=auth_write
        #values to publish on dbus
        self.values = {
            'batt_soc':[0]*96,
            'produced':[0]*96,
            'consumed':[0]*96,
            'released':[0]*96,
            'retained':[0]*96,
            'imported':[0]*96,
            'exported':[0]*96,
            'autocons':[0]*96
            }
        #name of the dbus service where to publish calculated values
        self.dbus_service_name = 'com.victronenergy.forecast'
        #initialize forecast variable
        self.dbus_service_mains={
            'total_prod' : {'path' : '/TotalProduced', 'value' : 0},
            'total_cons' : {'path' : '/TotalConsumed', 'value' : 0},
            'timestamp' : {'path' : '/Timestamp', 'value' : datetime.now().strftime("%Y-%m-%d %H:%M:00")}
            }
        self.dbus_service_lists={
            'batt_soc' : {'path' : '/Lists/BatterySoc', 'value' : None},
            'consumed' : {'path' : '/Lists/Consumed', 'value' : None},
            'produced' : {'path' : '/Lists/Produced', 'value' : None},
            'imported' : {'path' : '/Lists/Imported', 'value' : None},
            'exported' : {'path' : '/Lists/Exported', 'value' : None},
            'retained' : {'path' : '/Lists/Retained', 'value' : None},
            'released' : {'path' : '/Lists/Released', 'value' : None},
            'autocons' : {'path' : '/Lists/Autocons', 'value' : None},
        }
        self.dbus_import_params={
            'grid_sp' : {
                'service' : 'com.victronenergy.settings', 
                'path' : '/Settings/CGwacs/AcPowerSetPoint', 
                'value' : 0
                },
            'out_max' : {
                'service' : 'com.victronenergy.settings', 
                'path' : '/Settings/CGwacs/MaxDischargePower', 
                'value' : 0
                },
            'soc_min' : {
                'service' : 'com.victronenergy.settings', 
                'path' : '/Settings/CGwacs/BatteryLife/MinimumSocLimit', 
                'value' : 0
                },
            'bat_soc' : {
                'service' : 'com.victronenergy.battery.socketcan_can0', 
                'path' : '/Soc', 
                'value' : 0
                },
            'bat_soh' : {
                'service' : 'com.victronenergy.battery.socketcan_can0', 
                'path' : '/Soh', 
                'value' : 0
                },
            'bat_cap' : {
                'service' : 'com.victronenergy.battery.socketcan_can0', 
                'path' : '/InstalledCapacity', 
                'value' : 0
                },
        }
        self.dbus_imports={}
        # Path for file exchange
        self.file_path=(DEF_PATH if os.path.exists(DEF_PATH) else FOLDER)
        #other attributes
        self.url = None
        self.prod={}
        self.cons={}
        self.out_max=0
        self.solcast_forecast_called = False
        self.solcast_forecast_available = False
        #if program starts at time when update is supposed to be called
        self.values_update_called=False #set to False to prevent launching the consumption update 
        self.values_update_ready=False
        self.values_reset = True
        self.out_max_calculated = False

    #to read the solcast url in a configuration file stored in the working folder as it is site specific
    def __read_url__(self):
        url = None
        filename=FOLDER+'/solcast_url.cfg'
        if os.path.isfile(filename):
            f = open(filename, "r")
            self.url=f.read()
            f.close()
            return True
        else:
            return False

    #to load the consumption history saved in a file (24h consumption on 30mn interval, json format)
    def __read_cons__(self):
        filename=self.file_path+'/cons_history.json'
        if os.path.isfile(filename):
            with open(filename, mode="r", encoding="utf-8") as file:
                self.cons = json.load(file)
            return True
        else:
            return False

    #to save everything that we want to save
    def __save_cons__(self):
        filename=self.file_path+'/cons_history.json'
        with open(filename, mode="w", encoding="utf-8") as file:
            json.dump(self.cons, file)

    #to load the production forecast from a json saved in a file (for testing purpose only)
    def __read_prod__(self):
        filename=self.file_path+'/prod_forecast.json'
        if os.path.isfile(filename):
            with open(filename, mode="r", encoding="utf-8") as file:
                self.prod = json.load(file)
            #check if forecast is younger than 3 hours
            td = datetime.utcnow() - datetime.strptime(self.prod['forecasts'][0]["period_end"], "%Y-%m-%dT%H:%M:%S.0000000Z")
            if td.days==0 and td.seconds<=10800:
                 return True
            else:
                return False
        else:
            return False

    #to save production forecast as a json into a file (not used)
    def __save_prod__(self):
        filename=self.file_path+'/prod_forecast.json'
        with open(filename, mode="w", encoding="utf-8") as file:
            json.dump(self.prod, file)

    #to retrieve the production forecast from the solcast url
    def __curl_prod__(self):
        log.debug('Calling Solcast API url')
        self.prod=json.load(os.popen("curl -s "+self.url))
        if "forecasts" in self.prod:
            return True
        elif "response_status" in self.prod and "error_code" in self.prod["response_status"]:
            log.error(f'error received from Solcast API: {self.prod["response_status"]["error_code"]}')
        else:
            log.error('unidentified error when contacting Solcast API')
        return False

    #to validate value change on the dbus service
    def __callback_authwrite_change__(self, path, newvalue):
        if not newvalue:
            log.debug('!!!!!!!!!Change of MaxDischargedPower is NOT authorized')
        else:
            log.debug('Change of MaxDischargedPower is authorized')
        return True
  
    #to initialize the interface with dbus
    def __init_dbus__(self):
        #initialize the bus to connect to
        self.dbus_bus=dbus.SessionBus() if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else dbus.SystemBus()

        #create the VeDbusService
        self.dbus_service = VeDbusService(self.dbus_service_name, register=False)

        #add the required paths
        self.dbus_service.add_path(
            '/AuthorizeWriteMaxDischargePower', 
            value=1 if self.auth_write else 0,
            description='set to 1 to authorize to write the MaxDischargePower after calculation', 
            writeable=True,
            onchangecallback=self.__callback_authwrite_change__, 
            gettextcallback=None, 
            valuetype=dbus.Boolean
            )
        for name, item in self.dbus_service_mains.items():
            self.dbus_service.add_path(f'{item["path"]}',item["value"])
        for name, item in self.dbus_service_lists.items():
            self.dbus_service.add_path(f'{item["path"]}/0',json.dumps(self.values[name][:48]))
            self.dbus_service.add_path(f'{item["path"]}/1',json.dumps(self.values[name][48:]))
        #claim the service name on dbus only if not already existing
        self.dbus_service.register()
    
        #import the dbus objects
        for name, item in self.dbus_import_params.items():
            self.dbus_imports[name] = VeDbusItemImport(self.dbus_bus, item['service'], item['path'])

    #to refresh the imported objects
    def __read_dbus__(self):
        for name, item in self.dbus_import_params.items():
            item['value'] = self.dbus_imports[name].get_value()
        self.auth_write=self.dbus_service['/AuthorizeWriteMaxDischargePower']
        return True

    #to update the values in a 30 mn period 
    def __update_values__(self):
        ts=datetime.now()
        meters = self.energy_calculator.update()
        index = int((ts - datetime(ts.year, ts.month, ts.day, 0, 0, 0)).seconds/1800)
        for name, item in meters.items():
            self.values[name][index]=int(round(item['gap']*200,0))
        self.values['batt_soc'][index]=int(round(self.dbus_imports['bat_soc'].get_value(),0))
        autocons = meters['produced']['gap']
        autocons-= meters['exported']['gap']
        autocons-= meters['retained']['gap']
        consumed = autocons
        consumed+= meters['released']['gap'] 
        consumed+= meters['imported']['gap'] 
        self.values['autocons'][index]=int(round(autocons*200,0))
        self.values['consumed'][index]=int(round(consumed*200,0))
        self.cons[datetime.strftime(ts,"%H:%M")] = consumed
        return True

    #to calculate the max power pulled from the battery
    def __calculate_out_max__(self):
        #refresh the imported objects
        self.__read_dbus__()
        #set other variables
        run_loop=1                          #to continue loop searching optimal value (0 to exit loop)
        out_top=2000                        #absolute max for out_max
        sp_min=0                            #lower cap of the interval for the regression
        sp_max=out_top                      #upper cap of the interval for the regression
        soc_max=0                           #max forecasted battery soc
        soc_min=100                         #min forecasted battery soc
        self.out_max = (sp_min+sp_max)/2    #calculated max power output
        total_produced=0
        total_consumed=0
        #start the loops trying to find the optimal power output 
        #to maintain forecasted battery soc between soc_min+5% and 95%
        #all energies are calcuated in kWh, multiplied by 100 and rounded to int
        #to allow to publish as text with length lower than 256 characters
        #for further reading by HomeAssistant MQTT text 
        # only total_produced and total_consumed are in kWh
        #stop after 10 iterations in any case
        for iteration in range(10):
            #reset variables and lists
            total_produced=0
            total_consumed=0
            ts=datetime.now()
            index = int((ts - datetime(ts.year, ts.month, ts.day, 0, 0, 0)).seconds/1800)+1
            #loop all the records of the solar production forecast (96 x 30 minutes periods)
            for item in self.prod['forecasts']:
                #we are only interested in the forecast for the next 24 hours
                #we stop after 48 records in any case
                if index>95:
                    break
                #adjust period_end value to local time
                period_loc=datetime.strptime(item["period_end"], "%Y-%m-%dT%H:%M:%S.0000000Z")\
                            .replace(tzinfo=timezone(timedelta(seconds=0), 'UTC'))\
                            .astimezone()
                #if period end is in the past skip the calculation
                if period_loc<=ts.astimezone():
                    continue
                #store the battery soc at the beginning of the period
                soc_prev=(
                    self.dbus_import_params['bat_soc']['value'] if index == 0 
                        else self.values['batt_soc'][index-1]
                    )
                #all values are calculated average power
                #in 10W unit rounded as int to limit size of the dbus publish message to 256 characters
                #
                #retrieve the forecasted production for the period (already average power in kW, so x100)
                self.values['produced'][index]=(int(round(item['pv_estimate']*100,0)))
                #retrieve the forecasted consumption for the period (in kWh so x100 and x2 because was calculated on 30 mn)
                self.values['consumed'][index]=(int(round(self.cons[datetime.strftime(period_loc,"%H:%M")]*100*2,0)))
                #calculate average power discharged from the battery
                #out_max and grid_sp are in W so /10,
                #available battery capacity calculated in Wh so /10 and x2
                self.values['released'][index]=(int(round(min(
                    self.dbus_import_params['bat_soh']['value']/100
                        *self.dbus_import_params['bat_cap']['value']*48
                        *(soc_prev-self.dbus_import_params['soc_min']['value'])/100
                        /10*2,
                    min(
                        self.out_max/10, 
                        max(
                            0, 
                            self.values['consumed'][index]-self.values['produced'][index]
                                -self.dbus_import_params['grid_sp']['value']/10 
                            )
                        )
                    ))))
                #calculate average power charged into the battery
                #available battery capacity calculated in Wh so /10 and x2 
                self.values['retained'][index]=(int(round(min(
                    self.dbus_import_params['bat_soh']['value']/100
                        *self.dbus_import_params['bat_cap']['value']*52
                        *(100-soc_prev)/100
                        /10*2,
                    max(0, self.values['produced'][index]-self.values['consumed'][index])
                    ))))
                #calculate exchanges with grid
                self.values['imported'][index]=(int(max(0, 
                    self.values['consumed'][index]
                    -self.values['produced'][index]
                    -self.values['released'][index]
                    )))
                self.values['exported'][index]=(int(max(0, 
                    self.values['produced'][index]
                    -self.values['consumed'][index]
                    -self.values['retained'][index]
                    )))
                #calculate self consumption
                self.values['autocons'][index]=(int(max(0, 
                    self.values['consumed'][index]
                    -self.values['imported'][index]
                    -self.values['released'][index]
                    )))
                #calculate the battery soc at the period end
                #soc is calculated using Ah battery capacity
                #with 52V charge voltage and 48V discharge voltage
                #retained and released are calculated back into Wh so *10 and /2
                self.values['batt_soc'][index]=(int(round(
                    soc_prev
                    +(self.values['retained'][index]/52-self.values['released'][index]/48)*10/2
                        /(self.dbus_import_params['bat_soh']['value']
                            *self.dbus_import_params['bat_cap']['value']) 
                        *10000
                    ,0)))
                #update soc_max and soc_min
                soc_max=max(soc_max, self.values['batt_soc'][index])
                soc_min=min(soc_min, self.values['batt_soc'][index])
                #update the total_cons and total_prod (in kWh)
                #produced and consumed are calculated back into kWh so /100 and /2
                total_produced+=self.values['produced'][index]/100/2
                total_consumed+=self.values['consumed'][index]/100/2
                #
                index += 1

            #update regression interval and continue loop if soc is going below lower limit
            #or not recharging battery to the expected level
            if (soc_min < self.dbus_import_params['soc_min']['value']+5) or (soc_max < 85):
                run_loop=1
                sp_max=self.out_max
                self.out_max=(self.out_max+sp_min)/2
            #update regression interval and continue loop if soc is going above upper limit
            elif soc_max > 95:
                run_loop=1
                sp_min=self.out_max
                self.out_max=(sp_max+self.out_max)/2
            else:
                break
        #round the value
        self.out_max = round(self.out_max, 0)
        #if regression did not find optimum and reached lower value, set out_max to 0 
        if self.out_max < 2:
            self.out_max = 0
        #if regression did not find optimum and reached upper value, set out_max to out_top 
        elif self.out_max > out_top - 2:
            self.out_max = out_top
        #publish calculated values on dbus
        self.dbus_service['/Timestamp']=datetime.now().strftime('%Y-%m-%d %H:%M:00')
        self.dbus_service['/TotalProduced']=round(total_produced,3)
        self.dbus_service['/TotalConsumed']=round(total_consumed,3)
        for name, item in self.dbus_service_lists.items():
            self.dbus_service[f'{item["path"]}/0']=json.dumps(self.values[name][:48])
            self.dbus_service[f'{item["path"]}/1']=json.dumps(self.values[name][48:])
        return True

    #to end glib loop nicely
    def __soft_exit__(self):
        log.info('terminated on request')
        self.__save_cons__()
        log.info('24 h consumption history saved to file')
        os._exit(1)

    #to initialize        
    def init(self):
        try:
            #initialize the interface with dbus
            self.__init_dbus__()
            if not self.dbus_service['/AuthorizeWriteMaxDischargePower']:
                log.info('!!!!!!!!!change of MaxDischargedPower is NOT authorized')
            else:
                log.info('change of MaxDischargedPower is authorized')
      
            #initialize the consumption calculator
            self.energy_calculator = EnergyCalculator()
      
            #initialize the consumption history (read from file)
            if not self.__read_cons__():
                log.info('could not read 24h consumption history')
                log.info('aborted during initialization')
                os._exit(1)
            
            #initialize the solcast url (read from file)
            self.__read_url__()

            #read the production forecast in the file
            self.solcast_forecast_available = self.__read_prod__()
            if not self.solcast_forecast_available:
                log.info('could not read recent production forecast')
                log.info('waiting for current 30 mn period to end to curl solcast api')
        except:
            log.error('exception occured during init', exc_info=True)
            os._exit(1)

    #to update the forecast
    def update(self):
        #if a file named kill exists in the folder of this file, exit the program
        if os.path.isfile(FOLDER+'/kill'):
            os.remove(FOLDER+'/kill')
            self.__soft_exit__()

        #run updates when required
        #we use self.values_update_called and self.forecast_update_called
        # to avoid calling again and again the same update if an exception has occured
        #we use self.values_update_ready
        # to update the consumption only if a full 30 mn period has been completed after init
        #we use self.solcast_forecast_available
        # to calculate a new forecast as soon as a new hour starts after init
        try:
            #everyd day reset the values
            if datetime.now().hour == 0 and not self.values_reset:
                self.values_reset = True
                for name, item in self.values.items():
                    item=[0]*96
            #reset
            if (datetime.now().hour != 0) and self.values_reset:
                self.values_reset = False
            #every 3 hours
            if ((not(datetime.now().hour % 3) 
                or (not datetime.now().minute % 30 and not self.solcast_forecast_available))
                and not self.solcast_forecast_called):
                #curl solcast api
                if self.__curl_prod__():
                    self.solcast_forecast_available = True
                    self.__save_prod__()
                    log.debug('production_forecast saved to file')
                self.solcast_forecast_called = True
            #reset
            if datetime.now().hour % 3 and self.solcast_forecast_called:
                self.solcast_forecast_called = False

            #every 30 mn period
            if not(datetime.now().minute % 30):
                #calculate the consumption of the last 30 mn
                #skip the first period end after init 
                #to make sure the calculation is made with a full 30 mn period
                if not(self.values_update_ready):
                    self.values_update_ready=True
                #do the calculation only once
                elif not(self.values_update_called):
                    self.values_update_called=True
                    self.__update_values__()
                    log.debug(
                        f'values updated for period ending '
                        +f'{datetime.strftime(datetime.now(),"%H:%M")}: '
                        +f'{self.cons[datetime.strftime(datetime.now(),"%H:%M")]}'
                        )
                    self.__save_cons__()
                    log.debug('24 h consumption history saved to file')

                #if a recent forecast is available do the out_max calculation
                if self.solcast_forecast_available and not self.out_max_calculated and self.values_update_called:
                    self.out_max_calculated = self.__calculate_out_max__()
                    log.debug(
                        f'New value calculated for {self.dbus_import_params["out_max"]["path"]}: '
                        +f'{self.out_max}'
                        )
                    if (
                        self.dbus_service['/AuthorizeWriteMaxDischargePower'] 
                        and (abs(self.out_max - self.dbus_import_params['out_max']['value']) > 2)
                        ):
                        self.dbus_imports['out_max'].set_value(self.out_max)
                        log.debug(
                            f'New value set for {self.dbus_import_params["out_max"]["path"]}: '
                            +f'{self.out_max}'
                            )
            #reset
            if datetime.now().minute % 30 and self.values_update_called:
                self.out_max_calculated = False
                self.values_update_called = False

        except:
            log.error('exception occured during update', exc_info=True)
    
        return True

def main():
    parser = ArgumentParser(add_help=True)
    parser.add_argument('-d', '--debug', help='enable debug logging',
                        action='store_true')
    parser.add_argument('-s', '--skip', help='to skip writing of MaxDischargePower on dbus',
                        action='store_false')

    args = parser.parse_args()

    logging.basicConfig(
        filename=(DEF_PATH+LOGFILE if os.path.exists(DEF_PATH) else os.path.abspath(__file__)+'.log'),
        format='%(asctime)s - %(levelname)s - %(filename)-8s %(message)s', 
        datefmt="%Y-%m-%d %H:%M:%S", 
        level=(logging.DEBUG if args.debug else logging.INFO)
        )

    log.info(
        f'started, logging to '
        f'{DEF_PATH+LOGFILE if os.path.exists(DEF_PATH) else os.path.abspath(__file__)+".log"}'
        )

    signal.signal(signal.SIGINT, lambda s, f: os._exit(1))
    faulthandler.register(signal.SIGUSR1)
    dbus.mainloop.glib.threads_init()
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    mainloop = GLib.MainLoop()

    forecast=SolcastForecast(args.skip)

    forecast.init()
    log.info(f'initialization completed, now running permanent loop')
    GLib.timeout_add(UPDATE_INTERVAL, forecast.update)
    mainloop.run()

if __name__ == '__main__':
    main()
