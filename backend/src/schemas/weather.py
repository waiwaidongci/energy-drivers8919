from pydantic import BaseModel, Field, field_validator
from datetime import date


class StationInput(BaseModel):
    code: str = Field(min_length=2, max_length=60)
    name: str = Field(min_length=2, max_length=160)
    note: str = Field(default='', max_length=600)

    @field_validator('code')
    @classmethod
    def code_shape(cls, value):
        if any(ch.isspace() for ch in value):
            raise ValueError('站点代码不能含空白')
        return value


class ForecastImportInput(BaseModel):
    source_file: str = Field(default='forecast-import.csv', min_length=2, max_length=160)
    csv_text: str = Field(min_length=20, max_length=64000000)


class WithdrawalInput(BaseModel):
    occurred_at: str | None = None
    reason: str = Field(default='', max_length=600)


class AssignmentInput(BaseModel):
    building_code: str = Field(min_length=2, max_length=60)
    station_code: str = Field(min_length=2, max_length=60)
    valid_from: str
    valid_to: str | None = None
    note: str = Field(default='', max_length=600)

    @field_validator('valid_from', 'valid_to')
    @classmethod
    def valid_day(cls, value):
        if value is None:
            return value
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError('日期须为YYYY-MM-DD')
        return value


class AssignmentUpdate(AssignmentInput):
    pass
