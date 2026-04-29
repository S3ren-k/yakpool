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
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import Medicine, SessionLocal


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
        term = term.strip()
        if len(term) < 2 or term in STOPWORDS:
            continue
        cleaned.append(term)
        stripped = strip_korean_particle(term)
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
        "사용자 질문에서 의약품 이름이라고 판단되는 부분을 가장 먼저 판별하세요.\n"
        "아래 DB 약 목록을 참고해서 오타, 발음상 비슷한 표현, 조사 붙은 표현을 보정하세요.\n"
        "예: '부루펜이 뭐야?' -> '부루펜'\n"
        "예: '이브프로펜을 설명해줘' -> '이부프로펜'\n"
        "예: '겔포스는?' -> '겔포스'\n"
        "약 이름이 없으면 빈 문자열을 반환하세요.\n"
        "반드시 JSON만 반환하세요.\n\n"
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
            if score_value >= 72:
                scored.append((score_value, medicine))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

    def score(medicine):
        name = medicine.item_name or ""
        score_value = 0
        for term in terms:
            if term == name:
                score_value += 100
            elif term in name:
                score_value += 20 + len(term)
            elif name in message:
                score_value += 15
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
            "당신은 '약풀' 앱의 약사 AI 챗봇입니다. "
            "반드시 사용자가 제공한 DB 약 정보만 근거로 답하세요. "
            "DB에 없는 효능, 복용법, 주의사항, 성분, 상호작용 정보는 추측하지 말고 "
            "'DB에 등록된 정보만으로는 확인하기 어려워요'라고 말하세요. "
            "이 앱은 노인 대상 약품관리 앱입니다. "
            "진단이나 처방을 하지 말고, 위험하거나 불명확한 내용은 의사나 약사 상담을 권하세요. "
            "DB 원문을 그대로 복사하지 말고 노인분들이 읽기 쉬운 말로 짧게 요약하세요. "
            "전문용어는 가능한 쉬운 표현으로 바꾸고, 한 문장은 길지 않게 작성하세요. "
            "후속 질문 버튼, ACTIONS 표기, JSON, 코드블록은 절대 출력하지 마세요."
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
            "위 DB 정보만 사용해서 답변하세요. DB 원문을 그대로 길게 붙여넣지 마세요.\n"
            "각 항목은 반드시 1문장, 길어도 최대 2문장으로 요약하세요.\n"
            "각 항목 사이에는 빈 줄을 한 줄 넣으세요.\n"
            "사용자가 특정 항목만 물어봐도 아래 전체 형식은 유지하되, 질문과 관련된 항목을 가장 쉽게 설명하세요.\n\n"
            "반드시 아래 형식만 사용하세요:\n\n"
            "[효능] : 이 약이 어디에 쓰이는지 쉬운 말로 1~2문장.\n\n"
            "[복용법] : 언제, 얼마나 먹는지 쉬운 말로 1~2문장. DB에 정보가 복잡하면 가장 중요한 기준만 요약.\n\n"
            "[주의사항] : 꼭 조심해야 할 내용을 쉬운 말로 1~2문장. 위험한 내용은 의사나 약사에게 확인하라고 안내.\n\n"
            "[보관법] : 보관 방법을 쉬운 말로 1문장.\n\n"
            "약 이름을 첫 줄에 따로 길게 쓰지 말고, 위 4개 항목만 출력하세요. ACTIONS 줄은 출력하지 마세요."
        ),
    }
    return clean_chat_response(call_openai([system_msg, user_msg], max_tokens=700))


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
