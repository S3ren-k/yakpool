from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from passlib.context import CryptContext
from datetime import datetime

# 1. DB 설정
USER_DB_URL = "sqlite:///./users_info.db"
engine = create_engine(USER_DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 비밀번호 암호화 도구
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# 2. 사용자 테이블 모델
class User(Base):
    __tablename__ = "users"

    id           = Column(Integer, primary_key=True, index=True)
    username     = Column(String(50), unique=True, index=True, nullable=False)  # 아이디 (중복 불가)
    hashed_password = Column(String(200), nullable=False)                        # 암호화된 비번
    full_name    = Column(String(50), nullable=False)                            # 이름
    age          = Column(Integer, nullable=True)                                # 나이
    birth_date   = Column(String(20), nullable=True)                             # 생년월일 (예: 1990-01-01)
    gender       = Column(String(20), nullable=True)                             # 성별
    phone        = Column(String(30), nullable=True)                             # 전화번호
    created_at   = Column(DateTime, default=datetime.utcnow)

# 3. DB 생성 함수
def init_user_db():
    Base.metadata.create_all(bind=engine)
    print("users_info.db가 준비되었습니다.")

if __name__ == "__main__":
    init_user_db()
