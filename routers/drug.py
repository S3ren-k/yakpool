from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import SessionLocal, Medicine

router = APIRouter()

# DB 세션 함수
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 약 검색 API
@router.get("/pills/{pill_name}")
def get_pill_info(pill_name: str, db: Session = Depends(get_db)):

    db_result = db.query(Medicine).filter(
        Medicine.item_name.contains(pill_name)
    ).first()

    if db_result:
        return {
            "status": "success",
            "name": db_result.item_name,
            "effect": db_result.efcy_info,
            "usage": db_result.use_method,
            "warning": db_result.atpn_warn,
            "storage": db_result.deposit_method
        }
    else:
        return {
            "status": "fail",
            "message": f"'{pill_name}' 정보 없음"
        }