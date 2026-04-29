from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import inspect
from database import SessionLocal, DisposalBin

router = APIRouter()

# DB 세션 함수
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 폐의약품 수거함 목록 API
@router.get("/bins")
def get_bins(db: Session = Depends(get_db)):
    bins = db.query(DisposalBin).all()
    
    # DisposalBin 모델의 모든 컬럼을 자동으로 추출
    columns = [c.key for c in inspect(DisposalBin).mapper.column_attrs]
    
    result = []
    for bin in bins:
        bin_dict = {}
        for col in columns:
            value = getattr(bin, col, None)
            bin_dict[col] = value
        result.append(bin_dict)
    
    return result
