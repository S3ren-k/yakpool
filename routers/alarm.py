from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, time
from database import get_db # 기존 프로젝트의 DB 세션 획득 함수

router = APIRouter()

@router.get("/alarms")
async def get_dynamic_alarms(user_id: int, db: Session = Depends(get_db)):
    today = datetime.now().date()
    
    # 1. 사용자의 복약 설정 로드
    schedules = db.query(UserSchedule).filter(UserSchedule.user_id == user_id).all()
    
    # 2. 오늘 기록된 실제 식사 이벤트 로드
    events = db.query(ActualEvent).filter(
        ActualEvent.user_id == user_id,
        db.func.date(ActualEvent.actual_time) == today
    ).all()
    event_map = {e.event_type: e.actual_time for e in events}

    results = []
    for s in schedules:
        actual_time = event_map.get(s.base_event)
        
        if actual_time:
            # 실제 식사 기록 기반 동적 시간 계산
            offset = s.offset_minutes if s.timing_type == TimingType.AFTER else -s.offset_minutes
            if s.timing_type == TimingType.WITH: offset = 0
            final_time = actual_time + timedelta(minutes=offset)
            is_confirmed = True
        else:
            # 식사 전: 기본 예상 시간(예: 점심 12:30) 제공
            final_time = datetime.combine(today, time(12, 30))
            is_confirmed = False

        results.append({
            "id": s.id,
            "name": s.medication.name,
            "pill_image": s.medication.pill_image_url,
            "time": final_time.strftime("%H:%M"),
            "status": "confirmed" if is_confirmed else "pending"
        })
    
    return results

@router.post("/event/complete")
async def record_meal(user_id: int, event_type: str, db: Session = Depends(get_db)):
    # 사용자가 '식사 완료' 버튼을 누르면 호출
    new_event = ActualEvent(user_id=user_id, event_type=event_type)
    db.add(new_event)
    db.commit()
    return {"message": "Success"}
