#!/root/miniconda3/envs/karol/bin/python
import os
import matplotlib as mpl
mpl.use('Agg')

import sqlite3
import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

import numpy as np
import pytz
import calendar

import pandas as pd
import requests, json, hashlib, urllib, datetime
import time
import config as cfg
import datetime
#from apscheduler.schedulers.blocking import BlockingScheduler

#Gsched = BlockingScheduler()

def get_data_from_diabetes_m(USERNAME = cfg.DM_USERNAME,PASSWORD = cfg.DM_PASSWORD,start_date="2018092400",DB="karol_DB.db"):
    startTS = datetime.datetime.strptime(start_date,"%Y%m%d%H").timestamp()*1000.


    endTS=-1

    login = requests.post('https://analytics.diabetes-m.com/api/v1/user/authentication/login', json={
    'username': USERNAME,
    'password': PASSWORD,
    'device': ''
    }, headers={
    'origin': 'https://analytics.diabetes-m.com'
    })
    if login.status_code == 200:
        auth_code = login.json()['token']
        print("Getting sensor data from Diabetes_M")
        print("-------------------------------------")
        print("Loading entries...")
        entries = requests.post('https://analytics.diabetes-m.com/api/v1/diary/entries/list', 
        cookies=login.cookies, 
        headers={
            'origin': 'https://analytics.diabetes-m.com',
            'authorization': 'Bearer '+auth_code
        }, json={
            'fromDate': startTS,
            'toDate': endTS,
            'includeSensor': True,
            'page_count': 90000,
            'page_start_entry_time': 0
        })
        print("Loaded", len(entries.json()["logEntryList"]), "entries")

        dm_df = pd.DataFrame(entries.json()['logEntryList'])[['entry_time','carb_bolus','basal', 
                                                          'basal_insulin_type','bolus_insulin_type',
                                                          'carbs','glucose','notes','is_sensor']]
        selection=(dm_df.carb_bolus != 0) | (dm_df.is_sensor == False) | (dm_df.basal != 0) | ((dm_df.glucose!=0) & (dm_df.is_sensor == True))
        dm_df = dm_df.loc[selection]

        dm_df.entry_time = (dm_df.entry_time / 1000).astype('int64')
        dm_df.is_sensor = dm_df.is_sensor.astype('int')
        dm_df['app']='DM'
        sqlite3.register_adapter(np.int64, lambda val: int(val))
        sqlite3.register_adapter(np.int32, lambda val: int(val))
        try:
            print("Connecting to database: {:s}".format(DB))
            con = sqlite3.connect(DB)
            cur = con.cursor()
            try:
                cur.execute("CREATE TABLE TREATMENTS (timestamp INTEGER PRIMARY KEY, bolus REAL, basal REAL, basal_insulin_type TEXT, bolus_insulin_type TEXT, carbs REAL, glucose REAL, notes TEXT, is_sensor INTEGER, app TEXT)")
            except: 
                pass
            #deletes record after given date
            try:                
                print("Cleaning DB...")
                con.execute('''delete from TREATMENTS where timestamp > ?;''',(startTS//1000,))
            except Exception as e:
                print("Error while cleaning DB.",e)
            print("Saving to database: {:s}".format(DB))
            sql='''insert or replace into TREATMENTS (timestamp,bolus,basal, basal_insulin_type,bolus_insulin_type,carbs,glucose,notes,is_sensor,app) values (?,?,?,?,?,?,?,?,?,?) '''
            con.executemany(sql,dm_df[['entry_time', 'carb_bolus', 'basal', 'basal_insulin_type',
           'bolus_insulin_type', 'carbs', 'glucose','notes', 'is_sensor','app']].to_records(index=False))
            con.commit()
            con.close()
        except Exception as e:
            print("Error: saving to DB",e)
    else:
        print("Error logging in to Diabetes-M: ",login.status_code, login.text)
        exit(0)
    

def data_from_NSxdrip(start_date,DB="karol_DB.db",nightscout_url=cfg.nightscout_url):
    startTS = datetime.datetime.strptime(start_date,"%Y%m%d%H").timestamp()
    print("Getting sensor data from nightscout")
    print("-------------------------------------")

    try:
        ns_df=pd.read_json("{:s}/api/v1/entries/sgv.json?find[dateString][$gte]={:s}-{:s}-{:s}&count=None".format(nightscout_url,start_date[0:4],start_date[4:6],start_date[6:8]))
        ns_df['timestamp'] = ns_df.date.apply(lambda x: calendar.timegm((x.timetuple())))
       # ns_df['sgv'] = ns_df.filtered/18000.
        ns_df['sgv'] = ns_df.sgv/18.
        ns_df['sgv']=ns_df['sgv'].map('{:.1f}'.format).astype('float')
        print("Data successfully retrieved.")
    except Exception as e:
        print("Data retrival from Nightscout failed:",e)
        
    sqlite3.register_adapter(np.int64, lambda val: int(val))
    sqlite3.register_adapter(np.int32, lambda val: int(val))
    try:
        print("Connecting to database: {:s}".format(DB))
        con = sqlite3.connect(DB)
        cur = con.cursor()
        try:
            cur.execute("CREATE TABLE SGV (timestamp INTEGER PRIMARY KEY, sgv REAL)")
        except: 
            pass
            #deletes record after given date
        try:                
            print("Cleaning DB...")
            con.execute('''delete from SGV where timestamp > ?;''',(startTS,))
        except Exception as e:
                print("Error while cleaning DB.",e)
        print("Saving to database: {:s}".format(DB))
        sql='''insert or replace into SGV (timestamp,sgv) values (?,?) '''
        con.executemany(sql,ns_df[['timestamp', 'sgv']].to_records(index=False))
        con.commit()
        con.close()
    except Exception as e:
        print("Error: saving to DB",e)



def load_data_from_db(DB="karol_DB.db",table="TREATMENTS"):
    import sqlite3
    con = sqlite3.connect(DB)
    cur = con.cursor()
    query = "SELECT * FROM {:s};".format(table)
    df = pd.read_sql_query(query,con)
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='s',utc=True)
    df['datetime'] = df.apply(lambda x: x['datetime'].tz_convert('Europe/Zagreb'), axis=1)
    df.set_index('datetime',inplace=True)
    con.close()
    return df

def update_db(sdate):
    data_from_NSxdrip(start_date=sdate)
    get_data_from_diabetes_m(start_date=sdate)

def plot_sugar(plot_date,ax=None,lowOK=3.4,highOK=10,basal_name='HumulinN',
              bolus_name = "HumulinR",opacity = 0.4,bar_width = 0.01):

    title="{:%Y%m%d}".format(plot_date)
    #Dummy data - just to have 24h span
    dummy_idx = pd.date_range(start="{:%Y%m%d} 01".format(plot_date),end="{:%Y%m%d} 00".format(plot_date+datetime.timedelta(days=1)),freq='1H',tz=pytz.timezone('Europe/Zagreb'))
    dummy_df=pd.DataFrame(np.repeat(2,len(dummy_idx)),dummy_idx,columns=['dummy'])
    ax.plot(dummy_df.index,dummy_df.dummy,'.',alpha=0,label='') 

    #Insulin
    ukupno_insulin=0
    barax = ax.twinx()
    barax.set_ylabel('Insulin [U]')
    treat = load_data_from_db()
    sgv = load_data_from_db(table="SGV")
    if "{:%Y-%m-%d}".format(plot_date) in treat.index:
        tmp = treat["{:%Y-%m-%d}".format(plot_date)]
    
        #basal
        basal = tmp.loc[tmp.basal != 0,'basal']
        if len(basal)>0:
            ukupno_insulin=ukupno_insulin+basal.values.sum()
            name=tmp.loc[tmp.bolus != 0,'basal_insulin_type']
            if (name.all() == '8' or name.all() == '0'):
                basal_name = "HumulinR"
            elif name.all() == '21':
                basal_name = "Tresiba"
            barN=barax.bar(basal.index, basal, width=bar_width,facecolor='indianred',label=basal_name,alpha=opacity)
            for rect in barN:
                height = rect.get_height()
                barax.text(rect.get_x(), height+0.1, "{:.1f}".format(height), ha='right', va='center',
                           color='indianred',weight='bold',bbox=dict(boxstyle="round",
                           ec='indianred',
                           fc=(1., 1,1),
                           alpha=opacity                                                             
                          ))
                 
        #bolus
        bolus = tmp.loc[tmp.bolus != 0,'bolus'] 
        #bolus = tmp.loc[tmp.bolus != 0,['bolus','notes']]
        #print(bolus)
        #bolus = bolus.loc[~bolus.notes.str.contains('Nightscout'),'bolus']
        if len(bolus)>0:        
            ukupno_insulin=ukupno_insulin+bolus.values.sum()
            name=tmp.loc[tmp.bolus != 0,'bolus_insulin_type']
            if (name.all() == '5' or name.all() == '0'):
                bolus_name = "HumulinR"
            elif name.all() == '13':
                bolus_name = "NovoRapid"
            barR = barax.bar(bolus.index, bolus, width=bar_width,facecolor='blue',label=bolus_name,alpha=opacity)
            for rect in barR:
                height = rect.get_height()
                barax.text(rect.get_x(), height+0.1, "{:.1f}".format(height), ha='left', va='bottom',
                           color='blue',weight='bold',bbox=dict(boxstyle="round",
                           ec= 'blue',
                           fc=(1., 1, 1),
                           alpha=opacity                                         
                           ))
    

        carbs = tmp.loc[(tmp.carbs!=0),'carbs']
        if len(carbs)>0:
            barC = barax.bar(carbs.index, carbs/2.8, width=bar_width/5,facecolor='saddlebrown',label="ugljikohidrati",alpha=1)
            for rect in barC:
                height = rect.get_height()
                barax.text(rect.get_x(), height+0.1, "{:.1f}".format(height*2.8), ha='left', va='bottom',
                       color='saddlebrown',weight='bold',bbox=dict(boxstyle="round",
                       ec= 'saddlebrown',
                       fc=(1., 1, 1),
                       alpha=opacity                                         
                       ))

        if len(basal) > 0 or len(bolus) > 0 or len(carbs) >0:
            barax.set_ylim([0,25])
            barax.legend(loc=2)

        #finger
#        finger = tmp.loc[(tmp.is_sensor==0) & (tmp.glucose!=0),['glucose']]
        finger = tmp.loc[(tmp.is_sensor==0),['glucose']]
        finger = finger[finger.glucose != 0]

        if len(finger)>0:
            bg=ax.plot(finger.index, finger,'.--', color='red', markersize=15,label='Krv')
            for i, txt in enumerate(finger.glucose):
                ax.text(finger.index[i],txt+0.3,"{:.1f}".format(txt),ha='center', va='bottom',color='black',weight='bold')


    #SENSOR
    hipo_posto=0
    hiper_posto=0
    inrange_posto=0
    minute=0
    maxMinute=30
    maxInsulinOk = 35
    if "{:%Y-%m-%d}".format(plot_date) in sgv.index:
        tmp_sgv = sgv["{:%Y-%m-%d}".format(plot_date)]

        if len(tmp_sgv.sgv)>0:
           sensor = ax.plot(tmp_sgv.index, tmp_sgv.sgv,'-',label='Libre sensor')
           hipo_posto=len(tmp_sgv[tmp_sgv.sgv<lowOK])/len(tmp_sgv)
           hiper_posto=len(tmp_sgv[tmp_sgv.sgv>highOK])/len(tmp_sgv)
           inrange_posto=1-hipo_posto-hiper_posto
           a="{:.1f} | {:.1f} | {:.1f}".format(hipo_posto*100,inrange_posto*100,hiper_posto*100) 
           print(a)
    #    if len(tmp.glucose)>0:
    #        sensor = ax.plot(tmp.index, tmp.glucose,'.',label='DM sensor')


    
    ax.set_ylabel('BG')


    #Decoration
    ax.set_ylim([0,25])
    ax.set_yticks([a for a in range(0,10)]+[a for a in np.arange(10,21,2.5)]+[25])
    ax.axhspan(lowOK, highOK, alpha=0.2, color='green')
    ax.legend()
    ax.grid()
    hours = mdates.HourLocator(interval = 1)
    h_fmt = mdates.DateFormatter('%H',tz=pytz.timezone('Europe/Zagreb'))
    ax.xaxis.set_major_locator(hours)
    ax.xaxis.set_major_formatter(h_fmt)
    ax.set_title(title,fontsize=12,fontweight='bold')
    ax.xaxis_date('Europe/Zagreb')
    ukupno_col='lightblue'
    if ukupno_insulin > maxInsulinOk:
        ukupno_col='red'
    ax.text(0.15,0.9,"Ukupno insulin:{:d}".format(int(ukupno_insulin)),fontsize="14",bbox=dict(boxstyle="round",
    ec= 'white',fc=ukupno_col),transform=ax.transAxes,color='black')
    ax.text(0.05,1.04,"{:.1f}%".format(hipo_posto*100),fontsize="14",bbox=dict(boxstyle="round",
    ec= 'white',fc='red'),transform=ax.transAxes,color='black')
    ax.text(0.12,1.04,"{:.1f}%".format(inrange_posto*100),fontsize="14",bbox=dict(boxstyle="round",
    ec= 'white',fc='lightgreen'),transform=ax.transAxes,color='black')
    ax.text(0.19,1.04,"{:.1f}%".format(hiper_posto*100),fontsize="14",bbox=dict(boxstyle="round",
    ec= 'white',fc='orange'),transform=ax.transAxes,color='black')
    minute=maxMinute*inrange_posto
    ax.text(0.27,1.04,"Igrica: {:.0f}min".format(minute),fontsize="14",bbox=dict(boxstyle="round",
    ec= 'white',fc='gold'),transform=ax.transAxes,color='black')

def plot_main():
    start_date = (datetime.datetime.now()-datetime.timedelta(days=7)).strftime("%Y%m%d%H")  
    update_db(start_date)
    
    dates=[dt for dt in [datetime.datetime.now()-datetime.timedelta(days=n) for n in range(0,5)]]
    plt.close('all')
    fig, ax = plt.subplots(5,1,figsize=(12,20))
 
    #Get last bg value
    try:
        last=pd.read_json("http://karol1.herokuapp.com/api/v1/entries/sgv.json?count=1")
        last_sgv="{:.1f}".format((last.sgv/18.).values[0])
        last_dir=last.direction
        print(last_sgv,last_dir.values[0])
        boje={'FortyFiveDown':'pink','SingleDown':'salmon','DoubleDown':'red','FortyFiveUp':'khaki','SingleUp':'yellow','DoubleUp':'orange','Flat':'white'}
        strelice={'FortyFiveDown':'\u2198','SingleDown':'\u2193','DoubleDown':'\u2193 \u2193','FortyFiveUp':'\u2197','SingleUp':'\u2191','DoubleUp':'\u2191 \u2191','Flat':'\u2192'}
        col=boje[last_dir.values[0]]
        last_time = pd.to_datetime(last.date.values[0],utc=True).tz_convert('Europe/Zagreb')
        fig.text(0.7,0.988,"{:%H:%M} | {:s} {:s}".format(last_time,last_sgv,strelice[last_dir.values[0]]),fontsize="14",bbox=dict(boxstyle="round",ec= 'black',fc=col))
        #fig.text(0.57,0.988,"Ukupno: {:.0f}min".format(ukupno_minute),fontsize="14",bbox=dict(boxstyle="round",ec= 'gold',fc='gold'))
    except:
        pass

    now=pd.to_datetime(datetime.datetime.now(),utc=True).tz_convert('Europe/Zagreb')
    deltaT = now - last_time
    deltaTh = deltaT.components.hours
    
    #Za Pushover 
    plt.close('all')
    emoji = True
    push = False
    disconnect_warn = False
   
    with open("upozorenje.dat","r") as f:
        broj=int(f.read())
    if deltaTh > 1:
        disconnect_warn = True
        if broj<5:
            poruka = "Karol, malo si predugo odspojen, daj vidi kaj ne stima. Zadnji podaci su od: {:%H:%H} ".format(last_time)
        elif broj == 5:
            poruka = "Karol, malo si predugo odspojen, daj vidi kaj ne stima. Zadnji podaci su od: {:%H:%H}. Upozorio sam te vec 5 puta, vise necu. ".format(last_time)
        else:
            disconnect_warn = False
    else:
        with open("upozorenje.dat","w") as f:
            f.write("0")
   
    if disconnect_warn:
        ts = time.time()
        r = requests.post("https://api.pushover.net/1/messages.json", data={"token":"a9brnzcafrx482iw9vtrgzr6t4xsyk","user":"ugy7k8aq6qkip3gsa5vn9vfyadenst","message":poruka,"title":"Oprez -  nema vrijednosti secera!","timestamp":ts,"sound":"tugboat"})
        broj=broj+1
        with open("upozorenje.dat","w") as f:
            f.write("{:d}".format(broj))

#Upozorenja na razne vrijednosti secera
    if float(last_sgv)>11 and (last_dir.values[0] == 'SingleUp' or last_dir.values[0] == 'DoubleUp'):
        poruka = "Kavoc, malo ti je secer visok, jesi si ti dal inzulina!?  \U0001F489  \U0001F489"
        push = True
    if float(last_sgv)>12 and last_dir.values[0] == 'FortyFiveUp':
        poruka = "Kavoc, staje rasti al je visok... Jesi si ti dal inzulina!?  \U0001F489  \U0001F489"
        push = True

    if float(last_sgv)<9 and last_dir.values[0] == 'DoubleDown':
        print("Upozorenje 1")
        push = True
#https://github.com/carpedm20/emoji/blob/master/emoji/unicode_codes.py
#https://www.webfx.com/tools/emoji-cheat-sheet/
        if emoji:
            poruka = "\U0001F635 \U0001F615 Karol, ide ti secer u bunar: {:s} \u2193 \u2193 !!! Pripazi se i reagiraj  ako vec nisi!".format(last_sgv)
        else:
            poruka = "Karol, ide ti secer u bunar: {:s} !!! Pripazi se i reagiraj  ako vec nisi!".format(last_sgv)
    if float(last_sgv)<6 and last_dir.values[0] == 'SingleDown':
        push = True
        if emoji:
            poruka = "Karol, baci \U0001F440 na secer, moglo bi biti \U0001F4A9: {:s} \u2193 !!! Pripazi se  reagiraj ako vec nisi!".format(last_sgv)
        else:
            poruka = "Karol, baci oko na secer, moglo bi biti problema: {:s} !!! Pripazi se  reagiraj ako vec nisi!".format(last_sgv)
    if push:
        print("For Pushover")
        fig, ax = plt.subplots(1,1,figsize=(12,5))
        plot_sugar(dates[0],ax=ax)
    #fig.text(0.7,0.96,"{:%H:%M} | {:s} [{:s}]".format(last_time,last_sgv,last_dir.values[0]),fontsize="14",bbox=dict(boxstyle="round",ec= 'black',fc=col))
        fig.text(0.7,0.96,"{:%H:%M} | {:s} {:s}".format(last_time,last_sgv,strelice[last_dir.values[0]]),fontsize="14",bbox=dict(boxstyle="round",ec= 'black',fc=col))
        plt.savefig("pushover_karol.png")
#    poruka = "Ides u bunar {:s} i 2 crte dolje!!! Pripazi se!".format(last_svg)
        ts = time.time()
        r = requests.post("https://api.pushover.net/1/messages.json", data={"token":"a9brnzcafrx482iw9vtrgzr6t4xsyk","user":"ugy7k8aq6qkip3gsa5vn9vfyadenst","message":poruka,"title":"Oprez!","timestamp":ts,"sound":"tugboat"},files={"attachment": ("image.jpg", open("pushover_karol.png", "rb"), "image/jpeg")})
        print(r.text)

    print("Plotting") 
    for i,date in enumerate(dates):
        print(date)
        plot_sugar(date,ax=ax[i])
    #last_sgv="{:.1f}".format((pd.read_json("http://karol1.herokuapp.com/api/v1/entries/sgv.json?count=1").sgv/18.).values[0])
    fig.tight_layout()
    print("prije crtanja")
    plt.savefig("glimp_karol.svg")
    print("nakon crtanja svgg")
    plt.savefig("glimp_karol.png")
    print("nakon crtanja png")

    
plot_main()
#sched.start()

