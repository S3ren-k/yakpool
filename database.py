from sqlalchemy import create_engine, Column, Integer, String, Text, Float, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from datetime import datetime
from pathlib import Path
import os

# 1. DB 연결 설정 (일단 연습하기 가장 편한 SQLite로 설정)
# 나중에 MySQL로 바꿀 때는 이 주소만 'mysql+pymysql://아이디:비번@주소/db이름'으로 바꾸면 돼!
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capstone_pharmacy.db")
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# --- [테이블 정의 시작] ---

# 1. 사용자 테이블
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True)
    password_hash = Column(String(200)) # 비밀번호는 암호화해서 저장해야 해
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # 관계 설정 (한 사용자가 여러 처방전과 알림을 가질 수 있음)
    prescriptions = relationship("Prescription", back_populates="owner")
    alarms = relationship("Alarm", back_populates="owner")

# 2. 의약품 정보 테이블 (공공데이터포털에서 가져온 데이터 저장용)
class Medicine(Base):
    __tablename__ = "medicines"
    
    item_seq = Column(String(50), primary_key=True, index=True) # 약품 코드 (기본키)
    item_name = Column(String(100), index=True)                 # 약 이름
    efcy_info = Column(Text)                                    # 효능/효과
    use_method = Column(Text)                                   # 사용법/용량
    atpn_warn = Column(Text)                                    # 주의사항/부작용
    deposit_method = Column(Text)                               # 보관법/폐기법
    image_url = Column(String(255), nullable=True)              # 약 이미지 링크

# 3. 처방전 기록 테이블
class Prescription(Base):
    __tablename__ = "prescriptions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))           # 어떤 사용자의 처방전인지 연결
    image_url = Column(String(255))                             # 업로드된 처방전 이미지 경로
    extracted_text = Column(Text)                               # OCR로 추출한 텍스트 원본
    created_at = Column(DateTime, default=datetime.utcnow)
    
    owner = relationship("User", back_populates="prescriptions")

# 4. 폐의약품 수거 지도 데이터 테이블
class DisposalBin(Base):
    __tablename__ = "disposal_bins"

    id = Column(Integer, primary_key=True, index=True)
    district = Column(String(50))
    name = Column(String(100))
    address = Column(String(200))
    detail_location = Column(String(200))
    phone = Column(String(50))
    latitude = Column(Float)
    longitude = Column(Float)

# 5. 복약 알림 테이블
class Alarm(Base):
    __tablename__ = "alarms"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    medicine_name = Column(String(100))                         # 먹어야 할 약 이름
    alarm_time = Column(String(50))                             # 알림 시간 (예: "09:00, 19:00")
    is_active = Column(Boolean, default=True)                   # 알림 켜짐/꺼짐 상태
    
    owner = relationship("User", back_populates="alarms")

# database.py 에 추가
# 5. [수정됨] 약물 충돌(DUR) 데이터 테이블
# database.py 수정 예시
# database.py의 Interaction 클래스 부분
class Interaction(Base):
    __tablename__ = "interactions"
    id = Column(Integer, primary_key=True, index=True)
    item_a_name = Column(String) 
    item_b_name = Column(String) 
    
    # [추가] 용량을 뺀 깨끗한 이름을 담을 칸입니다.
    simplified_a = Column(String, index=True)
    simplified_b = Column(String, index=True)
    
    prohibit_content = Column(String) 
    category = Column(String)
    target_group = Column(String)



# 이 코드를 실행하면 파이썬이 알아서 DB 파일을 만들고 테이블을 생성해 줘!
if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    print("DB 및 테이블 생성 완료! (capstone_pharmacy.db 파일이 생겼는지 확인해봐)")
