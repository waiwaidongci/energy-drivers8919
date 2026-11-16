from sqlalchemy import select
from fastapi import HTTPException
from src.models import BuildingDataset,BuildingDay,BuildingDayForecast
from src.constants.enums import FEATURES
from src.services.splitting import split_buildings
def raw_days(db,dataset_id):
    days=db.scalars(select(BuildingDay).where(BuildingDay.dataset_id==dataset_id).order_by(BuildingDay.building_code,BuildingDay.day)).all()
    return [{'id':d.id,'building_code':d.building_code,'day':d.day,'energy_kwh':d.energy_kwh,**{f:getattr(d,f) for f in FEATURES}} for d in days]
def list_days(db,dataset_id,building,offset,limit):
    dataset=db.get(BuildingDataset,dataset_id)
    if not dataset:raise HTTPException(404,'数据集不存在')
    rows=raw_days(db,dataset_id);split=split_buildings(rows);selected=[r for r in rows if not building or r['building_code']==building]
    page=selected[offset:offset+limit]
    # The forecast shown here is the library selection, never the observed value.
    forecasts={}
    if page:
        keys=[(r['building_code'],r['day']) for r in page]
        for row_forecast in db.scalars(select(BuildingDayForecast).where(
                BuildingDayForecast.building_code.in_({k[0] for k in keys}),
                BuildingDayForecast.day.in_({k[1] for k in keys}))).all():
            forecasts[(row_forecast.building_code,row_forecast.day)]=row_forecast
    output=[]
    for row in page:
        sealed=row['building_code'] in split['test_buildings'];forecast=forecasts.get((row['building_code'],row['day']))
        output.append({**row,'sealed':sealed,'energy_kwh':row['energy_kwh'] if not sealed or dataset.evaluation_released else None,
            'forecast_temperature_c':forecast.temperature_c if forecast else None,
            'forecast_status':forecast.status if forecast else 'missing',
            'forecast_station_code':forecast.station_code if forecast else None,
            'forecast_published_at':forecast.published_at.isoformat() if forecast and forecast.published_at else None,
            'forecast_revision':forecast.revision if forecast else None})
    return {'total':len(selected),'rows':output}
