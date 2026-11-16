from datetime import datetime
from sqlalchemy import (
    DateTime, Double, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class WeatherStation(Base):
    __tablename__ = 'weather_stations'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    note: Mapped[str] = mapped_column(String(600), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WeatherForecastIssue(Base):
    """One publication of one forecast for one station and target day.

    ``published_at`` is the original publication time and is never rewritten by
    import, revision or withdrawal.  A revised forecast is a new row with a new
    revision number; a late delivery keeps the publisher's original time.
    """
    __tablename__ = 'weather_forecast_issues'
    __table_args__ = (
        UniqueConstraint('station_code', 'target_day', 'published_at', 'revision',
                         name='uq_weather_issue_natural'),
        Index('ix_weather_issue_lookup', 'station_code', 'target_day', 'published_at', 'revision'),
        Index('ix_weather_issue_target', 'target_day'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    station_code: Mapped[str] = mapped_column(String(60))
    target_day: Mapped[str] = mapped_column(String(10))
    published_at: Mapped[datetime] = mapped_column(DateTime)
    received_at: Mapped[datetime] = mapped_column(DateTime)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    temperature_c: Mapped[float] = mapped_column(Double)
    status: Mapped[str] = mapped_column(String(20), default='issued')
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    source_file: Mapped[str] = mapped_column(String(160), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WeatherForecastWithdrawal(Base):
    __tablename__ = 'weather_forecast_withdrawals'
    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey('weather_forecast_issues.id'))
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    received_at: Mapped[datetime] = mapped_column(DateTime)
    reason: Mapped[str] = mapped_column(String(600), default='')
    actor: Mapped[str] = mapped_column(String(160), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BuildingStationAssignment(Base):
    """A building's station assignment for an inclusive period.

    ``valid_to`` is inclusive; NULL means open ended.
    """
    __tablename__ = 'building_station_assignments'
    __table_args__ = (
        Index('ix_assignment_building', 'building_code', 'valid_from', 'valid_to'),
        Index('ix_assignment_station', 'station_code', 'valid_from', 'valid_to'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    building_code: Mapped[str] = mapped_column(String(60))
    station_code: Mapped[str] = mapped_column(String(60))
    valid_from: Mapped[str] = mapped_column(String(10))
    valid_to: Mapped[str | None] = mapped_column(String(10), nullable=True, default=None)
    note: Mapped[str] = mapped_column(String(600), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BuildingDayForecast(Base):
    """Materialised, auditable forecast selection for one building-day.

    Missing rows are first-class rows with ``status='missing'``.  They never
    store an observed temperature as a substitute.
    """
    __tablename__ = 'building_day_forecasts'
    __table_args__ = (
        UniqueConstraint('building_code', 'day', name='uq_building_day_forecast'),
        Index('ix_bdf_station_day', 'station_code', 'day'),
        Index('ix_bdf_day', 'day'),
        Index('ix_bdf_status', 'status', 'day'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    building_code: Mapped[str] = mapped_column(String(60))
    day: Mapped[str] = mapped_column(String(10))
    station_code: Mapped[str | None] = mapped_column(String(60), nullable=True, default=None)
    assignment_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    issue_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    temperature_c: Mapped[float | None] = mapped_column(Double, nullable=True, default=None)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    revision: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default='missing')
    missing_reason: Mapped[str | None] = mapped_column(String(60), nullable=True, default=None)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    forecast_version: Mapped[int] = mapped_column(Integer, default=0)
    assignment_version: Mapped[int] = mapped_column(Integer, default=0)
    resolved_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WeatherLibraryState(Base):
    __tablename__ = 'weather_library_state'
    id: Mapped[int] = mapped_column(primary_key=True)
    forecast_version: Mapped[int] = mapped_column(Integer, default=0)
    assignment_version: Mapped[int] = mapped_column(Integer, default=0)
    forecast_issue_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ForecastRunSnapshot(Base):
    __tablename__ = 'forecast_run_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('model_runs.id'), unique=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey('building_datasets.id'))
    forecast_version: Mapped[int] = mapped_column(Integer)
    assignment_version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict] = mapped_column(JSON)
    split: Mapped[dict] = mapped_column(JSON)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ForecastSnapshotDay(Base):
    __tablename__ = 'forecast_snapshot_days'
    __table_args__ = (
        UniqueConstraint('snapshot_id', 'building_code', 'day', name='uq_forecast_snapshot_day'),
        Index('ix_snapshot_day', 'snapshot_id', 'day'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey('forecast_run_snapshots.id'))
    source_day_id: Mapped[int] = mapped_column(Integer)
    building_code: Mapped[str] = mapped_column(String(60))
    day: Mapped[str] = mapped_column(String(10))
    energy_kwh: Mapped[float] = mapped_column(Double)
    features: Mapped[dict] = mapped_column(JSON)
    station_code: Mapped[str | None] = mapped_column(String(60), nullable=True, default=None)
    assignment_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    issue_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    temperature_c: Mapped[float | None] = mapped_column(Double, nullable=True, default=None)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    revision: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20))
    missing_reason: Mapped[str | None] = mapped_column(String(60), nullable=True, default=None)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
