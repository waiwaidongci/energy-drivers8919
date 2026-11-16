from fastapi import APIRouter,Depends,Query
from src.config.database import get_db
from src.utils.auth import current_user,editor
from src.schemas.run import RunInput
from src.controllers.run_controller import list_runs,get_run,create_run,run_forecast
router=APIRouter(prefix='/runs')
@router.get('')
def index(db=Depends(get_db),user=Depends(current_user)):return list_runs(db)
@router.post('',status_code=201)
def create(data:RunInput,db=Depends(get_db),user=Depends(editor)):return create_run(db,data)
@router.get('/{id}')
def detail(id:int,db=Depends(get_db),user=Depends(current_user)):return get_run(db,id)

@router.get('/{id}/forecast')
def forecast(id:int,offset:int=Query(default=0,ge=0),limit:int=Query(default=100,ge=1,le=500),db=Depends(get_db),user=Depends(current_user)):return run_forecast(db,id,offset,limit)
