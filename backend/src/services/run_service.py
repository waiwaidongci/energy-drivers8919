import json,hashlib
from sqlalchemy import select
from fastapi import HTTPException
from redis import Redis
from src.config.settings import REDIS_URL
from src.constants.enums import RunStatus
from src.models import BuildingDataset,ModelRun,DriverFinding,ForecastRunSnapshot,ForecastSnapshotDay
from src.services.splitting import training_arrays
from src.services.learning import fit_grouped
from src.services.inspection import inspect_holdout
from src.services.weather_service import build_snapshot,snapshot_info,snapshot_rows
queue=Redis.from_url(REDIS_URL)
def list_runs(db):return db.scalars(select(ModelRun).order_by(ModelRun.id.desc())).all()
def get_run(db,id):
    run=db.get(ModelRun,id)
    if not run:raise HTTPException(404,'模型研究不存在')
    return run
def create_run(db,data):
    dataset=db.scalar(select(BuildingDataset).where(BuildingDataset.id==data.dataset_id).with_for_update())
    if not dataset:raise HTTPException(404,'数据集不存在')
    config=data.config()
    if dataset.locked_config is not None and dataset.locked_config!=config:raise HTTPException(409,'数据集选模配置已锁定；公开封存评估后不得改特征或超参，只能同配置复算')
    if db.scalar(select(ModelRun).where(ModelRun.name==data.name)):raise HTTPException(409,'研究名称已存在')
    run=ModelRun(name=data.name,dataset_id=dataset.id,config=config,status=RunStatus.QUEUED)
    db.add(run);db.flush()
    try:
        # Freeze forecast + assignment versions and the exact rows in the same
        # transaction that accepts the run, so later imports/withdrawals cannot
        # change what this study sees.
        snapshot=build_snapshot(db,run,dataset)
        rows=snapshot_rows(db,snapshot,config['features'])
    except HTTPException:
        db.rollback();raise
    except ValueError as exc:
        db.rollback();raise HTTPException(422,str(exc))
    if not rows:
        db.rollback()
        message='该数据集没有可用的楼宇日记录'
        if config['features'] and 'temperature_c' in config['features']:
            missing=snapshot.counts.get('missing_rows',0)
            if missing:
                message=f'该数据集在所选预报版本下有{missing}个楼宇日缺报，系统不会用实测气温替代；请先在预报资料库补齐预报或调整站归属'
        raise HTTPException(422,message)
    try:training_arrays(rows,config,snapshot.split)
    except ValueError as exc:db.rollback();raise HTTPException(422,str(exc))
    dataset.locked_config=config
    db.commit()
    queue.lpush('energy:runs',str(run.id))
    return run
def execute_run(db,id):
    run=db.get(ModelRun,id)
    if not run or run.status not in (RunStatus.QUEUED,RunStatus.RUNNING):return
    run.status=RunStatus.RUNNING;db.commit()
    try:
        dataset=db.get(BuildingDataset,run.dataset_id)
        frozen=db.scalar(select(ForecastRunSnapshot).where(ForecastRunSnapshot.run_id==run.id))
        if frozen is None:
            # Legacy run accepted before the forecast library existed: freeze now.
            frozen=build_snapshot(db,run,dataset);db.commit()
        rows=snapshot_rows(db,frozen,run.config['features'])
        if not rows:raise ValueError('冻结快照中没有可用楼宇日：预报缺报不能用实测值替代，请补齐后重新提交')
        model,report=fit_grouped(rows,run.config,frozen.split)
        # The winning configuration and training-only report exist before test targets are inspected.
        metrics,findings,points=inspect_holdout(model,report,rows,run.config)
        weather=snapshot_info(frozen)
        # Recovery re-enters an interrupted job.  Replace any findings written by
        # the interrupted attempt with this attempt's *same frozen snapshot*
        # results, so a run never mixes versions of the forecast data.
        db.query(DriverFinding).filter(DriverFinding.run_id==run.id).delete()
        frozen_rows=frozen_rows_for(db,frozen)
        for values in findings:db.add(DriverFinding(run_id=run.id,**values))
        fingerprint=hashlib.sha256(json.dumps({'algorithm':'elasticnet-building-v2','dataset_id':dataset.id,'rows':frozen_rows,'config':run.config,'split':report['split'],'forecast':weather},sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        run.result={**report,'metrics':metrics,'points':points,'forecast':weather,'input_fingerprint':fingerprint,'config':run.config};run.status=RunStatus.COMPLETED;run.error='';dataset.evaluation_released=True;db.commit()
    except Exception as exc:
        db.rollback();run=db.get(ModelRun,id);run.status=RunStatus.FAILED;run.error=str(exc)[:600];db.commit()


def frozen_rows_for(db,snapshot):
    return [{
        'building_code':r.building_code,'day':r.day,'energy_kwh':r.energy_kwh,'features':r.features,
        'forecast_issue_id':r.issue_id,'forecast_station_code':r.station_code,
        'forecast_published_at':r.published_at.isoformat() if r.published_at else None,
        'forecast_received_at':r.received_at.isoformat() if r.received_at else None,
        'forecast_revision':r.revision,'forecast_cutoff_at':r.cutoff_at.isoformat(),
        'forecast_status':r.status,'forecast_missing_reason':r.missing_reason,
    } for r in db.scalars(select(ForecastSnapshotDay).where(ForecastSnapshotDay.snapshot_id==snapshot.id)
                          .order_by(ForecastSnapshotDay.building_code,ForecastSnapshotDay.day)).all()]
