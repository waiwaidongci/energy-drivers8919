from src.services.run_service import list_runs, get_run, create_run
from src.services.weather_service import run_forecast_evidence as forecast_evidence


def run_forecast(db, run_id, offset, limit):
    return forecast_evidence(db, run_id, offset, limit)
