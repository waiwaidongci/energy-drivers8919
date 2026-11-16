from fastapi import APIRouter, Depends, Query
from src.config.database import get_db
from src.utils.auth import current_user, editor
from src.schemas.weather import AssignmentInput, AssignmentUpdate, ForecastImportInput, StationInput, WithdrawalInput
from src.controllers import weather_controller as controller

router = APIRouter(prefix='/weather')


@router.get('/overview')
def overview(db=Depends(get_db), user=Depends(current_user)): return controller.overview(db)


@router.get('/stations')
def stations(db=Depends(get_db), user=Depends(current_user)): return controller.stations(db)


@router.post('/stations', status_code=201)
def add_station(data: StationInput, db=Depends(get_db), user=Depends(editor)): return controller.add_station(db, data)


@router.post('/imports', status_code=201)
def import_forecasts(data: ForecastImportInput, db=Depends(get_db), user=Depends(editor)): return controller.import_csv(db, data, user.email)


@router.get('/issues')
def issues(station: str = '', day: str = '', status: str = '', offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=200), db=Depends(get_db), user=Depends(current_user)): return controller.issues(db, station, day, status, offset, limit)


@router.post('/issues/{issue_id}/withdraw')
def withdraw(issue_id: int, data: WithdrawalInput, db=Depends(get_db), user=Depends(editor)): return controller.withdraw(db, issue_id, data, user.email)


@router.get('/assignments')
def assignments(building: str = '', db=Depends(get_db), user=Depends(current_user)): return controller.assignments(db, building)


@router.post('/assignments', status_code=201)
def add_assignment(data: AssignmentInput, db=Depends(get_db), user=Depends(editor)): return controller.add_assignment(db, data)


@router.put('/assignments/{assignment_id}')
def edit_assignment(assignment_id: int, data: AssignmentUpdate, db=Depends(get_db), user=Depends(editor)): return controller.edit_assignment(db, assignment_id, data)


@router.delete('/assignments/{assignment_id}')
def remove_assignment(assignment_id: int, db=Depends(get_db), user=Depends(editor)): return controller.remove_assignment(db, assignment_id)


@router.get('/days')
def days(building: str = '', start: str = '', end: str = '', status: str = '', offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=200), db=Depends(get_db), user=Depends(current_user)): return controller.days(db, building, start, end, status, offset, limit)


@router.get('/missing')
def missing(building: str = '', offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=200), db=Depends(get_db), user=Depends(current_user)): return controller.missing(db, building, offset, limit)


@router.get('/days/{building}/{day}/trace')
def trace(building: str, day: str, db=Depends(get_db), user=Depends(current_user)): return controller.trace(db, building, day)
