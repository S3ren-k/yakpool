import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from jose import jwt
from datetime import datetime, timedelta
import requests as http_requests

import user_db

router = APIRouter(tags=["회원관리"])

# ── 설정값 (환경변수에서 읽어옴) ─────────────────────────
SECRET_KEY               = os.getenv("SECRET_KEY", "yakpool_secret_key_change_later")
ALGORITHM                = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30

KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "")
KAKAO_REDIRECT_URI = "https://yakpool-fe-p.vercel.app/kakao/callback"


# ── DB 의존성 ────────────────────────────────────────────
def get_user_db():
    db = user_db.SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── 요청 스키마 ──────────────────────────────────────────
class SignupRequest(BaseModel):
    username:         str
    password:         str
    password_confirm: str
    name:             str
    birth_date:       str = ""
    gender:           str = ""
    phone:            str = ""

class LoginRequest(BaseModel):
    username:   str
    password:   str
    keep_login: bool = True

class KakaoLoginRequest(BaseModel):
    code: str


# ── 헬퍼 ─────────────────────────────────────────────────
def create_access_token(data: dict, days: int = 30) -> str:
    to_encode = data.copy()
    to_encode["exp"] = datetime.utcnow() + timedelta(days=days)
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# ── 엔드포인트 ────────────────────────────────────────────

@router.get("/check-username")
def check_username(username: str, db: Session = Depends(get_user_db)):
    """아이디 중복 확인"""
    exists = db.query(user_db.User).filter(
        user_db.User.username == username
    ).first()
    if exists:
        return {"available": False, "메시지": "이미 사용 중인 아이디입니다."}
    return {"available": True, "메시지": "사용 가능한 아이디입니다."}


@router.post("/signup")
def signup(req: SignupRequest, db: Session = Depends(get_user_db)):
    """회원가입"""
    if req.password != req.password_confirm:
        raise HTTPException(status_code=400, detail="비밀번호가 일치하지 않습니다.")

    if db.query(user_db.User).filter(user_db.User.username == req.username).first():
        raise HTTPException(status_code=400, detail="이미 존재하는 아이디입니다.")

    new_user = user_db.User(
        username        = req.username,
        hashed_password = user_db.pwd_context.hash(req.password),
        full_name       = req.name,
        birth_date      = req.birth_date or None,
        gender          = req.gender or None,
        phone           = req.phone or None,
    )
    try:
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        return {
            "상태":    "성공",
            "메시지":  f"{req.name}님, 가입을 환영합니다!",
            "user_id": new_user.id,
        }
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="회원가입 저장 중 오류가 발생했습니다.")


@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_user_db)):
    """로그인"""
    db_user = db.query(user_db.User).filter(
        user_db.User.username == req.username
    ).first()

    if not db_user:
        raise HTTPException(status_code=400, detail="아이디가 존재하지 않습니다.")

    if not user_db.pwd_context.verify(req.password, db_user.hashed_password):
        raise HTTPException(status_code=400, detail="비밀번호가 틀렸습니다.")

    expire_days  = 30 if req.keep_login else 1
    access_token = create_access_token(
        {"sub": db_user.username, "user_id": db_user.id},
        days=expire_days,
    )
    return {
        "상태":            "성공",
        "메시지":          "로그인 성공",
        "access_token":    access_token,
        "token_type":      "bearer",
        "expires_in_days": expire_days,
        "user": {
            "id":         db_user.id,
            "username":   db_user.username,
            "name":       db_user.full_name,
            "birth_date": db_user.birth_date,
            "gender":     db_user.gender,
            "phone":      db_user.phone,
        },
    }


@router.post("/kakao-login")
def kakao_login(req: KakaoLoginRequest, db: Session = Depends(get_user_db)):
    """카카오 소셜 로그인"""
    if not KAKAO_REST_API_KEY:
        raise HTTPException(status_code=500, detail="카카오 API 키가 설정되지 않았습니다.")

    # 1. 카카오 액세스 토큰 발급
    token_res = http_requests.post(
        "https://kauth.kakao.com/oauth/token",
        data={
            "grant_type":   "authorization_code",
            "client_id":    KAKAO_REST_API_KEY,
            "redirect_uri": KAKAO_REDIRECT_URI,
            "code":         req.code,
        },
    )
    token_json = token_res.json()
    if "access_token" not in token_json:
        raise HTTPException(status_code=400, detail=f"카카오 토큰 발급 실패: {token_json.get('error_description', '')}")

    # 2. 카카오 사용자 정보 조회
    user_res  = http_requests.get(
        "https://kapi.kakao.com/v2/user/me",
        headers={"Authorization": f"Bearer {token_json['access_token']}"},
    )
    user_info = user_res.json()
    kakao_id  = str(user_info["id"])
    nickname  = user_info.get("properties", {}).get("nickname", "카카오 회원")

    # 3. 신규면 자동 가입, 기존이면 로그인
    db_user = db.query(user_db.User).filter(
        user_db.User.username == f"kakao_{kakao_id}"
    ).first()

    if not db_user:
        db_user = user_db.User(
            username        = f"kakao_{kakao_id}",
            hashed_password = "KAKAO_LOGIN",
            full_name       = nickname,
        )
        db.add(db_user)
        db.commit()
        db.refresh(db_user)

    access_token = create_access_token(
        {"sub": db_user.username, "user_id": db_user.id},
        days=30,
    )
    return {
        "상태":         "성공",
        "메시지":       "카카오 로그인 성공",
        "access_token": access_token,
        "token_type":   "bearer",
        "user": {
            "id":       db_user.id,
            "username": db_user.username,
            "name":     db_user.full_name,
        },
    }
