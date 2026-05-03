import requests
from database import SessionLocal, Medicine

service_key = "edb4d836dbd54a5b98d50220055548e9debfb5ce45dbfd5e1d8f4b47d620eb38"
url = "http://apis.data.go.kr/1471000/DrbEasyDrugInfoService/getDrbEasyDrugList"

page = 1
total_saved = 0
seen_seqs = set() #중복 방지용 기억장치
DEFAULT_IMAGE = "https://via.placeholder.com/150"

while True:
    params = {
        "serviceKey": service_key,
        "pageNo": str(page),
        "numOfRows": "100",
        "type": "json"
    }

    print(f"[{page}페이지] 약 데이터를 가져오는 중...")
    
    try:
        #서버에 데이터 요청
        response = requests.get(url, params=params)
        
        if response.status_code != 200:
            print("API 호출 실패")
            break

        #데이터 꺼내기
        data = response.json()
        items = data.get('body', {}).get('items', [])
        
        if not items:
            print("더 이상 가져올 데이터가 없습니다. 수집을 종료합니다.")
            break

        #DB 연결 - DB 세션 생성    
        db = SessionLocal()
        current_batch_saved = 0
        
        for item in items:
            item_seq = item.get('itemSeq')

            print(item.get("itemName"), item.get("itemImage"))

            image_url = item.get("itemImage")
            print(item.get("itemName"), image_url)


            if not image_url:
                continue
            
            #1차 중복 제거
            if item_seq in seen_seqs:
                continue
                
            #2차 중복 제거
            existing_med = db.query(Medicine).filter(Medicine.item_seq == item_seq).first()
            
            #DB에 없는 경우 - 저장 진행
            if not existing_med:
                new_med = Medicine(
                    item_seq=item_seq,
                    item_name=item.get('itemName'),
                    efcy_info=item.get('efcyQesitm'),
                    use_method=item.get('useMethodQesitm'),
                    atpn_warn=item.get('atpnWarnQesitm'),
                    deposit_method=item.get('depositMethodQesitm'),
                    image_url=item.get("itemImage") or DEFAULT_IMAGE
                )
                db.add(new_med)
                seen_seqs.add(item_seq) 
                current_batch_saved += 1
        
        try:
            #100개 한 번에 저장
            db.commit() 
            total_saved += current_batch_saved
            print(f"-> {page}페이지 완료: {current_batch_saved}개 추가 (누적: {total_saved}개)")

        except Exception as db_err:
            db.rollback() 
            print(f"-> ⚠️ {page}페이지 저장 실패: {db_err}")
            
        db.close()
        page += 1

    except Exception as e:
        print(f"에러 발생: {e}")
        break

print(f"총 {total_saved}개의 데이터 저장 완료")
