import random,math,json
from datetime import date,timedelta,datetime
from sqlalchemy import select,func
from src.config.database import Base,engine,Session
from src.models import (
    User,BuildingDataset,BuildingDay,ModelRun,DriverFinding,RetrofitHypothesis,
    WeatherStation,WeatherForecastIssue,BuildingStationAssignment,
)
from src.utils.security import hash_password

STATIONS=[('WS-N01','北岭气象站','覆盖 LS-01 至 LS-04'),('WS-C02','中庭自动站','覆盖 LS-05 至 LS-08'),('WS-S03','南堤气象站','覆盖 LS-09 至 LS-12')]

def seed():
    Base.metadata.create_all(engine);rng=random.Random(97)
    with Session() as db:
        for role,name in [('admin','季砾 · 工程负责人'),('analyst','周山 · 能耗分析员'),('viewer','林岑 · 楼宇联络员')]:
            if not db.scalar(select(User).where(User.email==f'{role}@lishan.demo')):db.add(User(email=f'{role}@lishan.demo',name=name,role=role,password_hash=hash_password('Lishan2026!')))
        name='砾山12栋公共建筑 · 90日能耗特征'
        dataset=db.scalar(select(BuildingDataset).where(BuildingDataset.name==name))
        if not dataset:
            dataset=BuildingDataset(name=name,source_file='ls-energy-days.csv',note='2026-08-01起90日，机构级楼宇日记录；目标为日能耗kWh，固定按楼宇封存。');db.add(dataset);db.flush()
            for b in range(12):
                area=2500+b*350+rng.uniform(-80,80);equipment=40+area*.014+rng.uniform(-3,3);intercept=rng.gauss(0,7)
                for d in range(90):
                    day=date(2026,8,1)+timedelta(days=d);holiday=float(day.weekday()>=5);hours=9-2*holiday+rng.uniform(-1,1);temp=20+9*math.sin(d/18)+b*.12+rng.gauss(0,1.2);visitors=max(0,160+20*(b%4)+25*math.sin(d/7)+rng.gauss(0,30));humidity=max(5,min(95,55+12*math.sin(d/13)+rng.gauss(0,4)))
                    energy=.075*area+24*hours+7*temp+.24*visitors+1.7*equipment-10*holiday+intercept+rng.gauss(0,16)
                    db.add(BuildingDay(dataset_id=dataset.id,building_code=f'LS-{b+1:02d}',day=day.isoformat(),temperature_c=round(temp,3),operating_hours=round(hours,3),area_m2=round(area,2),visitors=round(visitors,0),humidity_pct=round(humidity,3),equipment_kw=round(equipment,3),holiday_flag=holiday,energy_kwh=round(energy,3)))
        for code,station_name,note in STATIONS:
            if not db.scalar(select(WeatherStation).where(WeatherStation.code==code)):
                db.add(WeatherStation(code=code,name=station_name,note=note))
        db.flush()
        # Two issues per station-day: D-2 06:00 and D-2 18:00, both published
        # before the D-1 00:00 cutoff.  The later issue is the revision that the
        # selection rule picks; a D-1 06:00 issue (not seeded) would be late.
        forecast_rng=random.Random(131)
        stations={s.code:s for s in db.scalars(select(WeatherStation)).all()}
        for code,_,_ in STATIONS:
            for offset in range(90):
                target=date(2026,8,1)+timedelta(days=offset);cutoff=datetime.combine(target-timedelta(days=1),datetime.min.time())
                for revision,published in [(0,cutoff-timedelta(hours=18)),(1,cutoff-timedelta(hours=6))]:
                    if db.scalar(select(WeatherForecastIssue.id).where(WeatherForecastIssue.station_code==code,WeatherForecastIssue.target_day==target.isoformat(),WeatherForecastIssue.published_at==published,WeatherForecastIssue.revision==revision)):continue
                    temperature=round(20+9*math.sin(offset/18)+forecast_rng.gauss(0,.9)+(1 if revision else 1.4),3)
                    db.add(WeatherForecastIssue(station_code=code,target_day=target.isoformat(),published_at=published,received_at=published+timedelta(minutes=7),revision=revision,temperature_c=temperature,status='issued',source_file='ls-weather-forecast.csv'))
        for b in range(12):
            building=f'LS-{b+1:02d}';code=STATIONS[b//4][0]
            if not db.scalar(select(BuildingStationAssignment.id).where(BuildingStationAssignment.building_code==building)):
                db.add(BuildingStationAssignment(building_code=building,station_code=code,valid_from='2026-08-01',valid_to=None,note='seed初始逐期归属'))
        db.commit()
        from src.services.weather_service import ensure_state,materialize_days
        state=ensure_state(db)
        days=db.scalars(select(BuildingDay).where(BuildingDay.dataset_id==dataset.id)).all()
        materialize_days(db,days,state)
        db.commit()
        print(json.dumps({m.__tablename__:db.scalar(select(func.count()).select_from(m)) for m in (User,BuildingDataset,BuildingDay,ModelRun,DriverFinding,RetrofitHypothesis,WeatherStation,WeatherForecastIssue,BuildingStationAssignment)},ensure_ascii=False))
if __name__=='__main__':seed()
