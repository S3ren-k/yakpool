from fastapi import APIRouter, Depends, Query
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

# 기존: 약 검색 API
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


# 새로 추가: 약 이름 검색 (자동완성/리스트용)
@router.get("/medicine-search")
def medicine_search(
    q: str = Query("", description="검색할 약 이름"),
    limit: int = Query(10, description="최대 결과 수"),
    db: Session = Depends(get_db)
):
    if not q.strip():
        return []
    
    results = db.query(Medicine).filter(
        Medicine.item_name.contains(q)
    ).limit(limit).all()
    
    return [
        {
            "item_name": med.item_name,
            "image_url": med.image_url
        }
        for med in results
    ]


# 새로 추가: 약 이미지 조회
@router.get("/medicine-image")
def medicine_image(
    name: str = Query("", description="약 이름"),
    db: Session = Depends(get_db)
):
    if not name.strip():
        return {"error": "약 이름을 입력해주세요"}
    
    result = db.query(Medicine).filter(
        Medicine.item_name.contains(name)
    ).first()
    
    if result:
        return {
            "item_name": result.item_name,
            "image_url": result.image_url
        }
    else:
        return {
            "error": f"'{name}' 이미지를 찾을 수 없습니다",
            "image_url": None
        }
