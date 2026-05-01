from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import Alarm, get_db


router = APIRouter()


@router.get("/alarms")
async def get_dynamic_alarms(user_id: int, db: Session = Depends(get_db)):
    alarms = (
        db.query(Alarm)
        .filter(Alarm.user_id == user_id)
        .order_by(Alarm.id.asc())
        .all()
    )
    return [
        {
            "id": alarm.id,
            "user_id": alarm.user_id,
            "name": alarm.medicine_name,
            "medicine_name": alarm.medicine_name,
            "time": alarm.alarm_time,
            "times": [t.strip() for t in (alarm.alarm_time or "").split(",") if t.strip()],
            "enabled": bool(alarm.is_active),
            "status": "active" if alarm.is_active else "inactive",
        }
        for alarm in alarms
    ]


@router.post("/event/complete")
async def record_meal(user_id: int, event_type: str, db: Session = Depends(get_db)):
    return {
        "message": "Success",
        "user_id": user_id,
        "event_type": event_type,
    }
