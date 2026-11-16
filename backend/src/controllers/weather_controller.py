from src.services import weather_service as svc


def overview(db):
    return svc.summary(db)


def stations(db):
    return svc.list_stations(db)


def add_station(db, data):
    return svc.create_station(db, data)


def import_csv(db, data, actor):
    return svc.import_forecasts(db, data, actor)


def issues(db, station, day, status, offset, limit):
    return svc.list_issues(db, station, day, status, offset, limit)


def withdraw(db, issue_id, data, actor):
    return svc.withdraw_issue(db, issue_id, data, actor)


def assignments(db, building):
    return svc.list_assignments(db, building)


def add_assignment(db, data):
    return svc.create_assignment(db, data)


def edit_assignment(db, assignment_id, data):
    return svc.update_assignment(db, assignment_id, data)


def remove_assignment(db, assignment_id):
    return svc.delete_assignment(db, assignment_id)


def days(db, building, start, end, status, offset, limit):
    return svc.list_days(db, building, start, end, status, offset, limit)


def missing(db, building, offset, limit):
    return svc.list_missing(db, building, offset, limit)


def trace(db, building, day):
    return svc.trace(db, building, day)
