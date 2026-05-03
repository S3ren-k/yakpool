from pathlib import Path
import base64
import difflib
import json
import os
import re

import requests
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from database import Interaction, Medicine, SessionLocal


router = APIRouter()

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip():
    os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
GOOGLE_VISION_API_KEY = os.getenv("GOOGLE_VISION_API_KEY", "").strip()
GOOGLE_CLOUD_API_KEY = (
    os.getenv("GOOGLE_CLOUD_API_KEY", "").strip() or GOOGLE_VISION_API_KEY
)
VISION_API_URL = (
    f"https://vision.googleapis.com/v1/images:annotate?key={GOOGLE_VISION_API_KEY}"
    if GOOGLE_VISION_API_KEY
    else ""
)
GOOGLE_STT_LANGUAGE = os.getenv("GOOGLE_STT_LANGUAGE", "ko-KR").strip()
GOOGLE_TTS_LANGUAGE = os.getenv("GOOGLE_TTS_LANGUAGE", "ko-KR").strip()
GOOGLE_TTS_VOICE = os.getenv("GOOGLE_TTS_VOICE", "ko-KR-Standard-A").strip()

STOPWORDS = {
    "약",
    "약품",
    "정보",
    "효능",
    "효과",
    "복용",
    "복용법",
    "주의",
    "주의사항",
    "부작용",
    "보관",
    "방법",
    "알려줘",
    "궁금해",
    "먹어도",
    "되나요",
    "어떻게",
    "언제",
    "얼마나",
}

REQUEST_SUFFIXES = (
    "설명해줘",
    "설명해주세요",
    "설명",
    "알려줘",
    "알려주세요",
    "궁금해",
    "궁금합니다",
    "뭐야",
    "무엇",
    "효능",
    "효과",
    "복용법",
    "주의사항",
    "부작용",
    "보관방법",
    "정보",
)

KOREAN_PARTICLES = (
    "이에요",
    "예요",
    "인가요",
    "이랑",
    "랑",
    "하고",
    "으로",
    "로",
    "에서",
    "에게",
    "한테",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "도",
    "만",
)


def clean_search_term(term):
    term = (term or "").strip()
    for suffix in REQUEST_SUFFIXES:
        if term.endswith(suffix) and len(term) > len(suffix) + 1:
            term = term[: -len(suffix)]
            break
    term = strip_korean_particle(term)
    return term.strip()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def extract_search_terms(message):
    terms = re.findall(r"[가-힣A-Za-z0-9]+", message)
    cleaned = []
    for term in terms:
        term = clean_search_term(term)
        if len(term) < 2 or term in STOPWORDS:
            continue
        if any(term == suffix for suffix in REQUEST_SUFFIXES):
            continue
        cleaned.append(term)
        stripped = clean_search_term(term)
        if stripped != term and len(stripped) >= 2 and stripped not in STOPWORDS:
            cleaned.append(stripped)
    return cleaned[:8]


def strip_korean_particle(term):
    for particle in KOREAN_PARTICLES:
        if term.endswith(particle) and len(term) > len(particle) + 1:
            return term[: -len(particle)]
    return term


def normalize_medicine_text(text):
    return re.sub(r"[^가-힣a-zA-Z0-9]", "", (text or "").lower())


def medicine_name_variants(name):
    variants = {name or ""}
    variants.update(re.findall(r"[가-힣A-Za-z0-9]+", name or ""))
    variants.update(re.findall(r"\(([^)]+)\)", name or ""))
    return [normalize_medicine_text(v) for v in variants if normalize_medicine_text(v)]


def fuzzy_score(term, medicine_name):
    normalized_term = normalize_medicine_text(strip_korean_particle(term))
    if len(normalized_term) < 3:
        return 0

    best = 0
    for variant in medicine_name_variants(medicine_name):
        if not variant:
            continue
        if normalized_term == variant:
            best = max(best, 100)
        elif normalized_term in variant or variant in normalized_term:
            best = max(best, 88)
        else:
            best = max(best, int(difflib.SequenceMatcher(None, normalized_term, variant).ratio() * 100))
            term_len = len(normalized_term)
            if len(variant) >= term_len:
                for size in {term_len, min(term_len + 1, len(variant))}:
                    for start in range(0, len(variant) - size + 1):
                        chunk = variant[start : start + size]
                        best = max(best, int(difflib.SequenceMatcher(None, normalized_term, chunk).ratio() * 100))
    return best


def get_medicine_name_hints(db):
    names = [
        row[0]
        for row in db.query(Medicine.item_name).limit(300).all()
        if row[0]
    ]
    variants = []
    for name in names:
        variants.append(name)
        variants.extend(re.findall(r"\(([^)]+)\)", name))
    return variants[:500]


def extract_medicine_name_with_ai(user_message, db):
    medicine_hints = "\n".join(f"- {name}" for name in get_medicine_name_hints(db))
    prompt = (
        "사용자 질문에서 의약품 이름을 정확하게 추출하세요.\n"
        "아래 DB 약 목록을 참고해서 가장 정확히 일치하는 이름을 찾으세요.\n\n"
        "중요 규칙:\n"
        "1. 사용자가 입력한 이름과 가장 비슷한 약 이름을 DB에서 찾으세요.\n"
        "2. 예: '아스피린 프로텍트' → DB에서 '아스피린 프로텍트'와 가장 유사한 것 찾기 (단순 '아스피린' 아님)\n"
        "3. 예: '부루펜이 뭐야?' → '부루펜'\n"
        "4. 예: '이브프로펜을 설명해줘' → '이부프로펜'\n"
        "5. 약 이름이 없으면 빈 문자열 반환\n"
        "6. 반드시 JSON만 반환하세요.\n\n"
        "DB 약 목록:\n"
        f"{medicine_hints}\n\n"
        f"사용자 질문: {user_message}\n\n"
        '{"medicine_name": ""}'
    )
    raw = call_openai([{"role": "user", "content": prompt}], max_tokens=120)
    raw = raw.replace("```json", "").replace("```", "").strip()
    data = json.loads(raw)
    return (data.get("medicine_name") or "").strip()


def find_medicine_from_db(db, message):
    terms = extract_search_terms(message)
    if not terms:
        return None

    filters = [Medicine.item_name.ilike(f"%{term}%") for term in terms]
    candidates = db.query(Medicine).filter(or_(*filters)).limit(20).all()
    if not candidates:
        all_medicines = db.query(Medicine).limit(1000).all()
        scored = []
        for medicine in all_medicines:
            score_value = max(fuzzy_score(term, medicine.item_name or "") for term in terms)
            if score_value >= 65:
                scored.append((score_value, medicine))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

    def score(medicine):
        name = medicine.item_name or ""
        normalized_name = normalize_medicine_text(name)
        score_value = 0
        for term in terms:
            normalized_term = normalize_medicine_text(term)
            # 완전 일치 (가장 높은 점수)
            if normalized_term == normalized_name:
                score_value += 200
            # 약 이름이 검색어를 포함 (검색어가 긴 경우 더 높은 점수)
            elif normalized_term in normalized_name:
                score_value += 20 + len(normalized_term) * 2
            # 검색어가 약 이름을 포함 (덜 정확한 경우)
            elif normalized_name in normalized_term:
                score_value += 15
            elif name in message:
                score_value += 10
        
        # 검색어와 약 이름 길이 차이가 작을수록 더 정확한 매칭 → 보너스
        # 예: "아스피린프로텍트" vs "아스피린" 중 전자가 검색어에 더 가까움
        search_normalized = normalize_medicine_text(" ".join(terms))
        if search_normalized and normalized_name:
            len_diff = abs(len(search_normalized) - len(normalized_name))
            score_value += max(0, 30 - len_diff * 2)  # 길이 차이 작을수록 보너스
        
        return score_value

    return max(candidates, key=score)


def find_medicine_for_question(db, user_message):
    extracted_name = ""
    try:
        extracted_name = extract_medicine_name_with_ai(user_message, db)
    except Exception:
        extracted_name = ""

    search_text = extracted_name or user_message
    medicine = find_medicine_from_db(db, search_text)
    if medicine:
        return medicine, extracted_name

    if extracted_name and extracted_name != user_message:
        medicine = find_medicine_from_db(db, user_message)
        if medicine:
            return medicine, extracted_name

    return None, extracted_name


def medicine_to_context(medicine):
    return {
        "name": medicine.item_name,
        "effect": medicine.efcy_info or "DB에 등록된 효능 정보가 없습니다.",
        "usage": medicine.use_method or "DB에 등록된 복용법 정보가 없습니다.",
        "warning": medicine.atpn_warn or "DB에 등록된 주의사항 정보가 없습니다.",
        "storage": medicine.deposit_method or "DB에 등록된 보관법 정보가 없습니다.",
        "image_url": medicine.image_url,
    }


INTERACTION_KEYWORDS = (
    "같이",
    "함께",
    "동시에",
    "병용",
    "금기",
    "상호작용",
    "안되는",
    "안 되는",
)


def is_interaction_question(message):
    if any(keyword in message for keyword in INTERACTION_KEYWORDS):
        return True
    return "먹어도" in message and any(marker in message for marker in ("이랑", "랑", "와", "과", "하고"))


def normalize_interaction_name(text):
    return re.sub(r"[^가-힣A-Za-z0-9]", "", (text or "")).lower()


def clean_interaction_candidate(text):
    candidate = re.sub(r"[^가-힣A-Za-z0-9]", "", (text or "")).strip()
    for suffix in ("이랑은", "이랑", "랑은", "하고는", "하고", "와는", "과는", "으로", "로", "은", "는", "이", "가", "을", "를", "랑", "와", "과"):
        if candidate.endswith(suffix) and len(candidate) > len(suffix) + 1:
            candidate = candidate[: -len(suffix)]
            break
    return candidate


def extract_interaction_names_fallback(message):
    cleaned = re.sub(r"(같이|함께|동시에|먹어도|먹으면|먹는|먹을|안되는|안 되는|약|있을까|되나|돼|되나요|될까|금기|병용|상호작용)", " ", message)
    candidates = re.findall(r"[가-힣A-Za-z0-9]+", cleaned)
    cleaned_candidates = []
    for candidate in candidates:
        candidate = clean_interaction_candidate(candidate)
        if len(candidate) >= 2 and candidate not in cleaned_candidates:
            cleaned_candidates.append(candidate)
    return cleaned_candidates[:3]


def extract_interaction_medicine_names_with_ai(user_message):
    prompt = (
        "사용자 질문에서 의약품 이름만 추출하세요.\n"
        "질문이 'A랑 B 같이 먹어도 돼?'이면 A와 B를 모두 추출하세요.\n"
        "질문이 'A랑 같이 먹으면 안 되는 약이 있을까?'이면 A만 추출하세요.\n"
        "반드시 JSON만 반환하세요.\n\n"
        f"사용자 질문: {user_message}\n\n"
        '{"medicines": []}'
    )
    raw = call_openai([{"role": "user", "content": prompt}], max_tokens=160)
    raw = raw.replace("```json", "").replace("```", "").strip()
    data = json.loads(raw)
    names = data.get("medicines") or []
    return [str(name).strip() for name in names if str(name).strip()]


def extract_interaction_medicine_names(user_message):
    try:
        names = extract_interaction_medicine_names_with_ai(user_message)
    except Exception:
        names = []
    if not names:
        names = extract_interaction_names_fallback(user_message)
    return [clean_interaction_candidate(name) for name in names if clean_interaction_candidate(name)][:3]


def interaction_to_context(row):
    return {
        "drug_a": row.item_a_name,
        "drug_b": row.item_b_name,
        "reason": row.prohibit_content or "DB에 구체적인 금기 사유가 없습니다.",
        "category": row.category or "병용금기",
        "target_group": row.target_group or "전체",
    }


def find_interaction_rows(db, names, limit=12):
    normalized = [normalize_interaction_name(name) for name in names if normalize_interaction_name(name)]
    if not normalized:
        return []

    if len(normalized) >= 2:
        first, second = normalized[0], normalized[1]
        filters = [
            and_(Interaction.simplified_a.ilike(f"%{first}%"), Interaction.simplified_b.ilike(f"%{second}%")),
            and_(Interaction.simplified_a.ilike(f"%{second}%"), Interaction.simplified_b.ilike(f"%{first}%")),
            and_(Interaction.item_a_name.ilike(f"%{names[0]}%"), Interaction.item_b_name.ilike(f"%{names[1]}%")),
            and_(Interaction.item_a_name.ilike(f"%{names[1]}%"), Interaction.item_b_name.ilike(f"%{names[0]}%")),
        ]
        return db.query(Interaction).filter(or_(*filters)).limit(limit).all()

    name = normalized[0]
    original = names[0]
    return (
        db.query(Interaction)
        .filter(
            or_(
                Interaction.simplified_a.ilike(f"%{name}%"),
                Interaction.simplified_b.ilike(f"%{name}%"),
                Interaction.item_a_name.ilike(f"%{original}%"),
                Interaction.item_b_name.ilike(f"%{original}%"),
            )
        )
        .limit(limit)
        .all()
    )


def make_interaction_ai_answer(user_message, names, interaction_rows):
    facts = [interaction_to_context(row) for row in interaction_rows]
    system_msg = {
        "role": "system",
        "content": (
            "당신은 고령자도 이해하기 쉽게 설명하는 약국 AI 챗봇입니다. "
            "반드시 제공된 병용금기 DB 검색 결과만 근거로 답하세요. "
            "DB에 없는 조합이나 사유를 추측하지 마세요. "
            "의학적 최종 판단이나 처방 변경 지시는 하지 말고, 위험 가능성이 있으면 의사 또는 약사 상담을 권하세요. "
            "ACTIONS, JSON, 코드블록은 출력하지 마세요."
        ),
    }
    if facts:
        user_content = (
            f"사용자 질문: {user_message}\n"
            f"추출된 약 이름: {', '.join(names) if names else '없음'}\n\n"
            "병용금기 DB 검색 결과:\n"
            f"{json.dumps(facts, ensure_ascii=False, indent=2)}\n\n"
            "답변 형식:\n"
            "[확인 결과] : 같이 먹어도 되는지 한 문장으로 말하세요.\n\n"
            "[주의 이유] : DB의 금기 사유를 쉬운 말로 설명하세요.\n\n"
            "[안내] : 이미 같이 복용했거나 복용 예정이면 의사 또는 약사에게 확인하라고 안내하세요."
        )
    else:
        user_content = (
            f"사용자 질문: {user_message}\n"
            f"추출된 약 이름: {', '.join(names) if names else '없음'}\n\n"
            "병용금기 DB 검색 결과: 없음\n\n"
            "답변 형식:\n"
            "[확인 결과] : 현재 DB에서 해당 병용금기 정보를 찾지 못했다고 말하세요.\n\n"
            "[주의] : DB에 없다고 안전이 확정되는 것은 아니라고 짧게 말하세요.\n\n"
            "[안내] : 정확한 복용 가능 여부는 의사 또는 약사에게 확인하라고 안내하세요."
        )
    return clean_chat_response(call_openai([system_msg, {"role": "user", "content": user_content}], max_tokens=650))


def get_stt_phrase_hints(db=None):
    common_phrases = [
        "효능",
        "복용법",
        "주의사항",
        "부작용",
        "보관법",
        "같이 먹어도 되나요",
        "식전",
        "식후",
        "약 알려줘",
        "어떻게 먹나요",
    ]
    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    try:
        medicine_names = [
            row[0]
            for row in db.query(Medicine.item_name).limit(300).all()
            if row[0]
        ]
    finally:
        if owns_db:
            db.close()
    return (medicine_names + common_phrases)[:500]


def call_openai(messages, max_tokens=500):
    if not OPENAI_API_KEY:
        raise RuntimeError(".env에 OPENAI_API_KEY가 설정되어 있지 않습니다.")

    res = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": OPENAI_MODEL,
            "max_tokens": max_tokens,
            "messages": messages,
        },
        timeout=30,
    )
    if not res.ok:
        try:
            message = res.json().get("error", {}).get("message", res.text)
        except Exception:
            message = res.text
        raise RuntimeError(f"OpenAI 오류: {message}")

    return res.json()["choices"][0]["message"]["content"].strip()


def make_db_based_ai_answer(user_message, medicine):
    info = medicine_to_context(medicine)
    system_msg = {
        "role": "system",
        "content": (
            "당신은 노인 대상 약품관리 앱 '약풀'의 AI 챗봇입니다. "
            "반드시 아래 규칙을 지키세요:\n"
            "1. 제공된 DB 정보만 사용하세요. DB에 없는 내용은 절대 추측하지 마세요.\n"
            "2. 각 항목은 반드시 1문장으로만 작성하세요. 절대로 2문장 이상 쓰지 마세요.\n"
            "3. 초등학생도 이해할 수 있는 쉬운 단어를 사용하세요.\n"
            "4. 전문 의학 용어는 반드시 쉬운 말로 바꾸세요. 예: '혈전' → '피가 굳는 것', '심혈관' → '심장과 혈관'\n"
            "5. 진단, 처방, 추천은 하지 마세요. 위험한 내용은 의사나 약사 상담을 권하세요.\n"
            "6. ACTIONS, JSON, 코드블록, 후속 질문 버튼은 절대 출력하지 마세요."
        ),
    }
    user_msg = {
        "role": "user",
        "content": (
            f"사용자 질문: {user_message}\n\n"
            "DB 약 정보:\n"
            f"- 이름: {info['name']}\n"
            f"- 효능: {info['effect']}\n"
            f"- 복용법: {info['usage']}\n"
            f"- 주의사항: {info['warning']}\n"
            f"- 보관법: {info['storage']}\n\n"
            "아래 형식으로만 답변하세요. 각 항목은 반드시 1문장, 최대 20자 이내로 작성하세요.\n"
            "DB 원문을 절대 그대로 복사하지 마세요. 핵심만 뽑아서 쉽게 풀어쓰세요.\n\n"
            "[효능] : (이 약이 무엇에 쓰이는지 딱 1문장)\n\n"
            "[복용법] : (언제, 얼마나 먹는지 딱 1문장)\n\n"
            "[주의사항] : (가장 중요한 주의사항 딱 1문장)\n\n"
            "[보관법] : (보관 방법 딱 1문장)\n\n"
            "위 4개 항목만 출력하세요. 그 외 내용은 출력하지 마세요."
        ),
    }
    return clean_chat_response(call_openai([system_msg, user_msg], max_tokens=300))


def clean_chat_response(text):
    lines = []
    for line in text.splitlines():
        normalized = line.strip()
        if normalized.startswith("[ACTIONS:") or normalized.startswith("ACTIONS:"):
            continue
        lines.append(line)
    cleaned = "\n".join(lines).strip()
    if not cleaned:
        raise RuntimeError("OpenAI가 후속 질문만 반환했습니다.")
    return cleaned


_MEAL_KO = {
    "after meal": "식후",
    "after meals": "식후",
    "after eating": "식후",
    "before meal": "식전",
    "before meals": "식전",
    "before eating": "식전",
    "with meal": "식중",
    "with meals": "식중",
    "with food": "식중",
    "regardless of meal": "식사 무관",
    "any time": "식사 무관",
}
_GAP_KO = {
    "30 minutes": "30분 후",
    "30 mins": "30분 후",
    "30 min": "30분 후",
    "15 minutes": "15분 후",
    "15 mins": "15분 후",
    "1 hour": "1시간 후",
    "immediately": "즉시",
    "right away": "즉시",
}
_DOSE_KO = {
    "1 tablet": "1정",
    "2 tablets": "2정",
    "3 tablets": "3정",
    "1 capsule": "1캡슐",
    "2 capsules": "2캡슐",
    "1 pack": "1포",
    "2 packs": "2포",
    "half tablet": "0.5정",
    "half a tablet": "0.5정",
}


def normalize_drug_fields(drug):
    for field, mapping in [("meal", _MEAL_KO), ("gap", _GAP_KO), ("dose", _DOSE_KO)]:
        value = (drug.get(field) or "").strip()
        if value.lower() in mapping:
            drug[field] = mapping[value.lower()]
    return drug


def parse_drugs_from_ocr(ocr_text):
    prompt = f"""
다음은 한국어 약봉지 또는 처방전 OCR 텍스트입니다.
텍스트에 있는 약 이름과 복약 정보를 찾아 JSON만 반환하세요.

반환 형식:
{{
  "drugs": [
    {{
      "name": "OCR에 실제로 나온 약 이름",
      "times": ["08:00"],
      "dose": "1정",
      "meal": "식후",
      "gap": "30분 후",
      "days": [1, 2, 3, 4, 5],
      "memo": ""
    }}
  ]
}}

규칙:
- name에는 OCR text에 실제로 등장하는 약 이름만 넣으세요. 반환 형식의 예시 문구를 약 이름으로 복사하지 마세요.
- 병원명, 약국명, 환자명, 날짜, 주소, 전화번호는 제외하세요.
- 시간은 24시간 HH:MM 형식으로 반환하세요.
- 복용 정보가 없으면 times는 ["08:00"], dose는 "1정", meal은 "식후", gap은 "30분 후"로 설정하세요.
- 약을 찾지 못한 경우에만 drugs를 빈 배열로 반환하세요.
- 모든 필드 값은 한국어로 반환하세요. 영어를 쓰지 마세요.
  예: dose "1 tablet" 금지, 반드시 "1정"
  예: meal "after meal" 금지, 반드시 "식후"
  예: gap "30 minutes" 금지, 반드시 "30분 후"

OCR text:
{ocr_text}
"""
    raw = call_openai([{"role": "user", "content": prompt}], max_tokens=800)
    raw = raw.replace("```json", "").replace("```", "").strip()
    parsed = json.loads(raw)
    return [normalize_drug_fields(drug) for drug in parsed.get("drugs", [])]


def get_google_speech_encoding(content_type):
    from google.cloud import speech

    content_type = (content_type or "").lower()
    if "ogg" in content_type:
        return speech.RecognitionConfig.AudioEncoding.OGG_OPUS
    if "wav" in content_type or "wave" in content_type:
        return speech.RecognitionConfig.AudioEncoding.LINEAR16
    return speech.RecognitionConfig.AudioEncoding.WEBM_OPUS


def get_google_speech_rest_encoding(content_type):
    content_type = (content_type or "").lower()
    if "ogg" in content_type:
        return "OGG_OPUS"
    if "wav" in content_type or "wave" in content_type:
        return "LINEAR16"
    return "WEBM_OPUS"


def stt_with_api_key(audio_content, content_type):
    if not GOOGLE_CLOUD_API_KEY:
        raise RuntimeError(".env에 GOOGLE_CLOUD_API_KEY 또는 GOOGLE_VISION_API_KEY가 없습니다.")

    encoding = get_google_speech_rest_encoding(content_type)
    config = {
        "encoding": encoding,
        "languageCode": GOOGLE_STT_LANGUAGE,
        "enableAutomaticPunctuation": True,
        "speechContexts": [
            {
                "phrases": get_stt_phrase_hints(),
                "boost": 15.0,
            }
        ],
    }
    if encoding in {"WEBM_OPUS", "OGG_OPUS"}:
        config["sampleRateHertz"] = 48000

    res = requests.post(
        f"https://speech.googleapis.com/v1/speech:recognize?key={GOOGLE_CLOUD_API_KEY}",
        json={
            "config": config,
            "audio": {"content": base64.b64encode(audio_content).decode("utf-8")},
        },
        timeout=30,
    )
    if not res.ok:
        raise RuntimeError(res.text)

    data = res.json()
    return " ".join(
        alt.get("transcript", "").strip()
        for result in data.get("results", [])
        for alt in result.get("alternatives", [])[:1]
    ).strip()


def tts_with_api_key(text):
    if not GOOGLE_CLOUD_API_KEY:
        raise RuntimeError(".env에 GOOGLE_CLOUD_API_KEY 또는 GOOGLE_VISION_API_KEY가 없습니다.")

    res = requests.post(
        f"https://texttospeech.googleapis.com/v1/text:synthesize?key={GOOGLE_CLOUD_API_KEY}",
        json={
            "input": {"text": text[:4000]},
            "voice": {
                "languageCode": GOOGLE_TTS_LANGUAGE,
                "name": GOOGLE_TTS_VOICE,
            },
            "audioConfig": {
                "audioEncoding": "MP3",
                "speakingRate": 0.9,
            },
        },
        timeout=30,
    )
    if not res.ok:
        raise RuntimeError(res.text)

    audio_content = res.json().get("audioContent")
    if not audio_content:
        raise RuntimeError("Google TTS 응답에 audioContent가 없습니다.")
    return base64.b64decode(audio_content)


def sanitize_google_error(error):
    message = str(error)
    if GOOGLE_CLOUD_API_KEY:
        message = message.replace(GOOGLE_CLOUD_API_KEY, "[GOOGLE_API_KEY]")
    if GOOGLE_VISION_API_KEY:
        message = message.replace(GOOGLE_VISION_API_KEY, "[GOOGLE_API_KEY]")
    return message


@router.post("/chat")
async def chat(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    user_message = (data.get("message") or "").strip()

    if not user_message:
        return {"response": "메시지를 입력해주세요."}

    if is_interaction_question(user_message):
        names = extract_interaction_medicine_names(user_message)
        interaction_rows = find_interaction_rows(db, names)
        try:
            response = make_interaction_ai_answer(user_message, names, interaction_rows)
        except Exception as e:
            return {
                "response": (
                    "병용금기 DB 검색은 완료했지만 AI 답변 생성에 실패했어요.\n\n"
                    "OpenAI API 키와 네트워크 연결을 확인해 주세요."
                ),
                "source": "interaction_openai_error",
                "error": str(e),
                "extracted_medicine_names": names,
                "interactions": [interaction_to_context(row) for row in interaction_rows],
            }

        return {
            "response": response,
            "source": "interaction_with_db",
            "extracted_medicine_names": names,
            "interactions": [interaction_to_context(row) for row in interaction_rows],
        }

    medicine, extracted_name = find_medicine_for_question(db, user_message)
    if not medicine:
        return {
            "response": (
                f"'{extracted_name or user_message}'에 해당하는 약 정보를 DB에서 찾지 못했어요.\n\n"
                "약 이름을 조금 더 정확히 입력해 주세요."
            ),
            "extracted_medicine_name": extracted_name,
        }

    try:
        response = make_db_based_ai_answer(user_message, medicine)
    except Exception as e:
        return {
            "response": (
                "DB에서 약 정보는 찾았지만 OpenAI 답변 생성에 실패했어요.\n\n"
                ".env의 OPENAI_API_KEY 값과 네트워크 연결을 확인해 주세요."
            ),
            "source": "openai_error",
            "error": str(e),
            "medicine": medicine_to_context(medicine),
        }

    return {
        "response": response,
        "source": "openai_with_db",
        "extracted_medicine_name": extracted_name,
        "medicine": medicine_to_context(medicine),
    }


@router.post("/analyze-image")
async def analyze_image(image: UploadFile = File(...)):
    if not VISION_API_URL:
        return JSONResponse({"error": ".env에 GOOGLE_VISION_API_KEY가 설정되어 있지 않습니다."}, status_code=500)

    image_data = await image.read()
    if not image_data:
        return JSONResponse({"error": "이미지 파일이 비어 있습니다."}, status_code=400)

    vision_res = requests.post(
        VISION_API_URL,
        json={
            "requests": [
                {
                    "image": {"content": base64.b64encode(image_data).decode("utf-8")},
                    "features": [{"type": "TEXT_DETECTION"}],
                    "imageContext": {"languageHints": ["ko", "en"]},
                }
            ]
        },
        timeout=30,
    )
    if not vision_res.ok:
        return JSONResponse(
            {"error": f"Google Vision error: {sanitize_google_error(vision_res.text)}"},
            status_code=502,
        )

    try:
        annotations = vision_res.json()["responses"][0].get("textAnnotations", [])
    except (KeyError, IndexError):
        return JSONResponse({"error": "OCR 응답을 읽지 못했습니다."}, status_code=502)

    if not annotations:
        return {"drugs": [], "message": "사진에서 텍스트를 인식하지 못했습니다."}

    ocr_text = annotations[0].get("description", "")
    try:
        return {"drugs": parse_drugs_from_ocr(ocr_text), "ocr_text": ocr_text}
    except json.JSONDecodeError:
        return JSONResponse(
            {"error": "AI 응답을 JSON으로 해석하지 못했습니다.", "ocr_text": ocr_text},
            status_code=502,
        )
    except Exception as e:
        return JSONResponse({"error": str(e), "ocr_text": ocr_text}, status_code=500)


@router.post("/stt")
async def speech_to_text(audio: UploadFile = File(...)):
    audio_content = await audio.read()
    if not audio_content:
        return JSONResponse({"error": "음성 파일이 비어 있습니다."}, status_code=400)

    try:
        if GOOGLE_CLOUD_API_KEY:
            transcript = stt_with_api_key(audio_content, audio.content_type)
            if not transcript:
                return JSONResponse({"error": "음성을 텍스트로 인식하지 못했습니다."}, status_code=400)
            return {"text": transcript}

        from google.cloud import speech

        client = speech.SpeechClient()
        encoding = get_google_speech_encoding(audio.content_type)
        config_kwargs = {
            "encoding": encoding,
            "language_code": GOOGLE_STT_LANGUAGE,
            "enable_automatic_punctuation": True,
            "speech_contexts": [
                speech.SpeechContext(phrases=get_stt_phrase_hints(), boost=15.0)
            ],
        }
        if encoding in {
            speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
            speech.RecognitionConfig.AudioEncoding.OGG_OPUS,
        }:
            config_kwargs["sample_rate_hertz"] = 48000

        response = client.recognize(
            config=speech.RecognitionConfig(**config_kwargs),
            audio=speech.RecognitionAudio(content=audio_content),
        )
        transcript = " ".join(
            result.alternatives[0].transcript.strip()
            for result in response.results
            if result.alternatives
        ).strip()
        if not transcript:
            return JSONResponse({"error": "음성을 텍스트로 인식하지 못했습니다."}, status_code=400)
        return {"text": transcript}
    except Exception as e:
        return JSONResponse({"error": f"Google STT 오류: {sanitize_google_error(e)}"}, status_code=502)


@router.post("/tts")
async def text_to_speech(request: Request):
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "읽을 텍스트가 없습니다."}, status_code=400)

    try:
        if GOOGLE_CLOUD_API_KEY:
            return Response(content=tts_with_api_key(text), media_type="audio/mpeg")

        from google.cloud import texttospeech

        client = texttospeech.TextToSpeechClient()
        voice_kwargs = {"language_code": GOOGLE_TTS_LANGUAGE}
        if GOOGLE_TTS_VOICE:
            voice_kwargs["name"] = GOOGLE_TTS_VOICE

        response = client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=text[:4000]),
            voice=texttospeech.VoiceSelectionParams(**voice_kwargs),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=0.9,
            ),
        )
        return Response(content=response.audio_content, media_type="audio/mpeg")
    except Exception as e:
        return JSONResponse({"error": f"Google TTS 오류: {sanitize_google_error(e)}"}, status_code=502)
