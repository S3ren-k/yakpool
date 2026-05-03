# 이 스크립트의 사용법을 출력
print("""
=== DB 경량화 스크립트 ===

사용법:
  cd ~/Desktop/기초캡스톤/BE_1st
  python slim_db.py

이 스크립트는:
1. capstone_pharmacy.db에서 medicines 테이블의 약 이름 99개를 가져옴
2. interactions 테이블에서 그 99개 약에 해당하는 병용금기만 추출
3. disposal_bins는 전부 복사
4. 결과를 capstone_lite.db로 저장
""")

import sqlite3
import os

SOURCE = "capstone_pharmacy.db"
OUTPUT = "capstone_lite.db"

# 기존 lite DB 있으면 삭제
if os.path.exists(OUTPUT):
    os.remove(OUTPUT)

# 원본 연결
src = sqlite3.connect(SOURCE)
src_cur = src.cursor()

# 새 DB 연결
dst = sqlite3.connect(OUTPUT)
dst_cur = dst.cursor()

# 1. disposal_bins 테이블 통째로 복사
print("[1/4] disposal_bins 복사 중...")
src_cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='disposal_bins'")
create_sql = src_cur.fetchone()
if create_sql:
    dst_cur.execute(create_sql[0])
    src_cur.execute("SELECT * FROM disposal_bins")
    rows = src_cur.fetchall()
    cols = len(rows[0]) if rows else 0
    placeholders = ','.join(['?' for _ in range(cols)])
    dst_cur.executemany(f"INSERT INTO disposal_bins VALUES ({placeholders})", rows)
    print(f"  -> {len(rows)}개 복사 완료")

# 2. medicines 테이블 통째로 복사
print("[2/4] medicines 복사 중...")
src_cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='medicines'")
create_sql = src_cur.fetchone()
if create_sql:
    dst_cur.execute(create_sql[0])
    src_cur.execute("SELECT * FROM medicines")
    rows = src_cur.fetchall()
    cols = len(rows[0]) if rows else 0
    placeholders = ','.join(['?' for _ in range(cols)])
    dst_cur.executemany(f"INSERT INTO medicines VALUES ({placeholders})", rows)
    print(f"  -> {len(rows)}개 복사 완료")

# 3. medicines에 있는 약 이름 목록 가져오기
print("[3/4] medicines에 있는 약 이름으로 interactions 필터링 중...")
src_cur.execute("SELECT item_name FROM medicines")
medicine_names = [row[0] for row in src_cur.fetchall()]
print(f"  -> medicines에 {len(medicine_names)}개 약 있음")

# 4. interactions 테이블 구조 복사 + 필터링된 데이터만 넣기
src_cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='interactions'")
create_sql = src_cur.fetchone()
if create_sql:
    dst_cur.execute(create_sql[0])
    
    # interactions 컬럼 이름 확인
    src_cur.execute("PRAGMA table_info(interactions)")
    col_info = src_cur.fetchall()
    col_names = [c[1] for c in col_info]
    print(f"  -> interactions 컬럼: {col_names}")
    
    # item_a_name 또는 item_b_name이 medicines의 약 이름을 포함하는 것만 추출
    total_filtered = 0
    for med_name in medicine_names:
        src_cur.execute("""
            SELECT * FROM interactions 
            WHERE item_a_name LIKE ? OR item_b_name LIKE ?
        """, (f'%{med_name}%', f'%{med_name}%'))
        
        rows = src_cur.fetchall()
        if rows:
            cols = len(rows[0])
            placeholders = ','.join(['?' for _ in range(cols)])
            # 중복 방지: INSERT OR IGNORE
            dst_cur.executemany(f"INSERT OR IGNORE INTO interactions VALUES ({placeholders})", rows)
            total_filtered += len(rows)
    
    print(f"  -> 필터링된 interactions: 약 {total_filtered}개 (중복 제거 전)")

# 커밋
dst.commit()

# 결과 확인
print("\n[4/4] 결과 확인:")
dst_cur.execute("SELECT COUNT(*) FROM disposal_bins")
print(f"  disposal_bins: {dst_cur.fetchone()[0]}개")
dst_cur.execute("SELECT COUNT(*) FROM medicines")
print(f"  medicines: {dst_cur.fetchone()[0]}개")
dst_cur.execute("SELECT COUNT(*) FROM interactions")
print(f"  interactions: {dst_cur.fetchone()[0]}개 (원본: 193,025개)")

src.close()
dst.close()

# 파일 크기 비교
original_size = os.path.getsize(SOURCE) / (1024 * 1024)
lite_size = os.path.getsize(OUTPUT) / (1024 * 1024)
print(f"\n  원본 DB: {original_size:.1f} MB")
print(f"  경량 DB: {lite_size:.1f} MB")
print(f"  절감율: {(1 - lite_size/original_size) * 100:.1f}%")

if lite_size < 100:
    print(f"\n✅ 100MB 이하! GitHub에 올릴 수 있어요!")
else:
    print(f"\n⚠️ 아직 100MB 초과. 추가 최적화 필요.")
