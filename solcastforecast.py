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

class ConsumptionCalculator(object):

    def __init__(self):
        self.bus=dbus.SessionBus() if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else dbus.SystemBus()
        self.meters={
        'released' : {
                        'service' : 'com.victronenergy.battery.socketcan_can0', 
                        'path' : '/History/DischargedEnergy', 
                        'value' : None, 
                        'gap' : None
                        },
        'retained' : {
                        'service' : 'com.victronenergy.battery.socketcan_can0', 
                        'path' : '/History/ChargedEnergy', 
                        'value' : None, 
                        'gap' : None
                        },
        'imported' : {
                        'service' : 'com.victronenergy.grid.se_203', 
                        'path' : '/Ac/Energy/Forward', 
                        'value' : None, 
                        'gap' : None
                        },
        'exported' : {
                        'service' : 'com.victronenergy.grid.se_203', 
                        'path' : '/Ac/Energy/Reverse', 
                        'value' : None, 
                        'gap' : None
                        },
        'produced' : {
                        'service' : 'com.victronenergy.pvinverter.se_101', 
                        'path' : '/Ac/Energy/Forward', 
                        'value' : None, 
                        'gap' : None
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
    def update(self, old_value):
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
            log.debug(f'name: {name} - meter: {meter}')
        #
        log.debug(f'updated_meters_count: {updated_meters_count}')
        if updated_meters_count==5:
            self.consumption = self.meters['released']['gap'] 
            self.consumption -= self.meters['retained']['gap']
            self.consumption += self.meters['imported']['gap'] 
            self.consumption -= self.meters['exported']['gap']
            self.consumption += self.meters['produced']['gap']
        else:
            self.consumption = old_value
        #
        log.debug(f'consumption: {self.consumption}')
        return self.consumption

class SolcastForecast(object):

    def __init__(self, auth_write):
        #to skip the update of MaxDischargePower on dbus
        self.auth_write=auth_write
        #name of the dbus service where to publish calculated values
        self.dbus_service_name = 'com.victronenergy.forecast'
        #initialize forecast variable
        self.dbus_service_params={
            'period_end' : {'path' : '/PeriodEnd', 'value' : "0000-00-00 00:00:00"},
            'battery_soc' : {'path' : '/BatterySoc', 'value' : 0},
            'consumed' : {'path' : '/Consumed', 'value' : 0},
            'produced' : {'path' : '/Produced', 'value' : 0},
            'imported' : {'path' : '/Imported', 'value' : 0},
            'exported' : {'path' : '/Exported', 'value' : 0},
            'retained' : {'path' : '/Retained', 'value' : 0},
            'released' : {'path' : '/Released', 'value' : 0},
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
                'value' : 0},
            'bat_soh' : {
                'service' : 'com.victronenergy.battery.socketcan_can0', 
                'path' : '/Soh', 
                'value' : 0},
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
        self.forecast_update_called=True #set to True to launch the forecast update 
        #if program starts at time when update is supposed to be called
        self.consumption_update_called=False #set to False to prevent launching the consumption update 
        #if program starts at time when update is supposed to be called
        self.forecast_update_ready=False
        self.consumption_update_ready=False

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
    def __save__(self):
        filename=self.file_path+'/cons_history.json'
        with open(filename, mode="w", encoding="utf-8") as file:
            json.dump(self.cons, file)

    #to load the production forecast from a json saved in a file (for testing purpose only)
    def __read_prod__(self):
        filename=self.file_path+'/prod_forecast.json'
        if os.path.isfile(filename):
            with open(filename, mode="r", encoding="utf-8") as file:
                self.prod = json.load(file)
            return True
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
            log.info('!!!!!!!!!Change of MaxDischargedPower is NOT authorized')
        else:
            log.info('Change of MaxDischargedPower is authorized')
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
        self.dbus_service.add_path('/TotalForecastedProduction', value=0)
        self.dbus_service.add_path('/TotalForecastedConsumption', value=0)
        for i in range(96):
            for name, item in self.dbus_service_params.items():
                self.dbus_service.add_path(f'/Forecast/{i:02d}{item["path"]}',item["value"])
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

    #to update the energy consumption in a period 
    #period_end must be local time provided in format "%H:%M"
    def __update_cons__(self, period_end):
        log.debug(f'Consumption of the period before update: {self.cons[period_end]}')
        self.cons[period_end] = round(self.consumption_calculator.update(self.cons[period_end]), 2)
        log.debug(f'Consumption of the period after update: {self.cons[period_end]}')
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
        period_end=[]
        battery_soc=[]
        produced=[]
        consumed=[]
        released=[]
        retained=[]
        imported=[]
        exported=[]
        #start the loops trying to find the optimal power output 
        #to maintain forecasted battery soc between soc_min+5% and 95%
        #stop after 10 iterations in any case
        for iteration in range(10):
            #reset variables and lists
            total_produced=0
            total_consumed=0
            period_end*=0
            battery_soc*=0
            produced*=0
            consumed*=0
            released*=0
            retained*=0
            imported*=0
            exported*=0
            #loop all the records of the solar production forecast (96 x 30 minutes periods)
            #use enumerate to keep track of the item index
            for index, item in enumerate(self.prod['forecasts']):
                #depending time when the solcas query is made, 97 records can be returned
                #we stop after 96 records in any case
                if index>95:
                    break
                #adjust period_end value to local time
                period_end.append(
                    datetime.strftime(
                        datetime.strptime(item["period_end"], "%Y-%m-%dT%H:%M:%S.0000000Z")
                            .replace(tzinfo=timezone(timedelta(seconds=0), 'UTC')).astimezone()
                        ),
                        "%Y-%m-%d %H:%M:%S"
                    )
                #store the battery soc at the beginning of the period
                soc_prev=(
                    self.dbus_import_params['bat_soc']['value'] if index == 0 
                        else battery_soc[index-1]
                    )
                #retrieve the forecasted production for the period
                produced.append(item['pv_estimate'])
                #retrieve the forecasted consumption for the period
                consumed.append(self.cons[datetime.strftime(period_end[index],"%H:%M")])
                #calculate energy discharged from the battery
                released.append(min(
                    self.dbus_import_params['bat_soh']['value']/100
                        *self.dbus_import_params['bat_cap']['value']*0.048
                        *(soc_prev-self.dbus_import_params['soc_min']['value'])/100,
                    min(
                        self.out_max/2000, 
                        max(
                            0, 
                            consumed[index]-produced[index]
                                -self.dbus_import_params['grid_sp']['value']/2000 
                            )
                        )
                    ))
                #calculate energy charged into the battery
                retained.append(min(
                    self.dbus_import_params['bat_soh']['value']/100
                        *self.dbus_import_params['bat_cap']['value']*0.052
                        *(100-soc_prev)/100,
                    max(0, produced[index]-consumed[index])
                    ))
                #calculate exchanges with grid
                imported.append(max(0, consumed[index]-produced[index]-released[index]))
                exported.append(max(0, produced[index]-consumed[index]-retained[index]))
                #calculate the battery soc at the period end
                #soc is calculated using Ah battery capacity
                #with 52V charge voltage and 48V discharge voltage
                battery_soc.append(
                    soc_prev
                    +(retained[index]/0.052-released[index]/0.048)
                        /(self.dbus_import_params['bat_soh']['value']
                            *self.dbus_import_params['bat_cap']['value']) 
                        *10000
                    )
                #update soc_max and soc_min
                soc_max=max(soc_max, battery_soc[index])
                soc_min=min(soc_min, battery_soc[index])
                #update the total_cons and total_prod
                total_produced+=produced[index]
                total_consumed+=consumed[index]

            #update regression interval and continue loop if soc is going below lower limit
            if soc_min < self.dbus_import_params['soc_min']['value']+5:
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
        #if regression did not find optimum and reached lower value, set out_max to 0 
        if self.out_max < 2:
            self.out_max = 0
        #if regression did not find optimum and reached upper value, set out_max to out_top 
        elif self.out_max > out_top - 2:
            self.out_max = out_top
        #publish calculated values on dbus
        self.dbus_service['/TotalForecastedProduction']=total_produced
        self.dbus_service['/TotalForecastedConsumption']=total_consumed
        for index, item in enumerate(period_end):
            head=f'/Forecast/{index:02d}'
            self.dbus_service[f'{head}/PeriodEnd']=item
            self.dbus_service[f'{head}/BatterySoc']=battery_soc[index]
            self.dbus_service[f'{head}/Produced']=produced[index]
            self.dbus_service[f'{head}/Consumed']=consumed[index]
            self.dbus_service[f'{head}/Retained']=retained[index]
            self.dbus_service[f'{head}/Released']=released[index]
            self.dbus_service[f'{head}/Imported']=imported[index]
            self.dbus_service[f'{head}/Exported']=exported[index]
 
        return True

    #pour terminer la boucle permanente de façon propre
    def __soft_exit__(self):
        log.info('terminated on request')
        self.__save__()
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
            self.consumption_calculator = ConsumptionCalculator()
      
            #initialize the consumption history (read from file)
            if not self.__read_cons__():
                log.info('could not read 24h consumption history')
                log.info('aborted during initialization')
                os._exit(1)
            
            #initialize the solcast url (read from file)
            self.__read_url__()

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
        #we use self.consumption_update_called and self.forecast_update_called
        # to avoid calling again and again the same update if an exception has occured
        #we use self.consumption_update_ready
        # to update the consumption only if a full 30 mn period has been completed after init
        #we use self.forecast_update_ready
        # to calculate a new forecast as soon as a new hour starts after init
        try:
            #check if a 30mn period end is reached
            if not(datetime.now().minute % 30):
                #skip the first period end after init 
                #to make sure the calculation is made with a full 30 mn period
                if not(self.consumption_update_ready):
                    self.consumption_update_ready=True
                #do the calculation only once
                elif not(self.consumption_update_called):
                    self.consumption_update_called=True
                    self.__update_cons__(datetime.strftime(datetime.now(),"%H:%M"))
                    log.debug(
                        f'consumption updated for period ending '
                        +f'{datetime.strftime(datetime.now(),"%H:%M")}: '
                        +f'{self.cons[datetime.strftime(datetime.now(),"%H:%M")]}'
                        )
                    self.__save__()
                    log.debug('24 h consumption history saved to file')

            #reset
            if datetime.now().minute % 30 and self.consumption_update_called:
                self.consumption_update_called=False

            #do the forecast every 3 hours or once when a new hour starts if it is first calculation
            if (
                (not(datetime.now().hour % 3) and not(self.forecast_update_called)) 
                or (datetime.now().minute==0 and not(self.forecast_update_ready))
                ):
                self.forecast_update_ready=True
                self.forecast_update_called=True
                success=self.__curl_prod__()
                if success:
                    self.__calculate_out_max__()
                    log.info(
                        f'New value calculated for {self.dbus_import_params["out_max"]["path"]}: '
                        +f'{self.out_max}'
                        )
                    if (
                        self.dbus_service['/AuthorizeWriteMaxDischargePower'] 
                        and (abs(self.out_max - self.dbus_import_params['out_max']['value']) > 2)
                        ):
                        self.dbus_imports['out_max'].set_value(self.out_max)
                        log.info(
                            f'New value set for {self.dbus_import_params["out_max"]["path"]}: '
                            +f'{self.out_max}'
                            )

            #reset
            if datetime.now().hour % 3 and self.forecast_update_called:
                self.forecast_update_called=False

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

    log.info('')
    log.info('------------------------------------------------------------')
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
