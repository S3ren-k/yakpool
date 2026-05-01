from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

from sqlalchemy import text


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
DESKTOP_DIR = PROJECT_DIR.parent

sys.path.insert(0, str(BACKEND_DIR))

from database import Base, Interaction, SessionLocal, engine  # noqa: E402


CATEGORY = "병용금기"
TARGET_GROUP = "전체"
CSV_FILENAMES = ("dur_data.csv", "new_dur_data.csv")
ENCODINGS = ("utf-8-sig", "cp949", "euc-kr")


def normalize_drug_name(value):
    value = (value or "").strip()
    value = re.sub(r"_\([^)]*\)\s*$", "", value)
    return re.sub(r"[^가-힣A-Za-z0-9]", "", value).lower()


def pair_key(item_a_name, item_b_name):
    simplified_a = normalize_drug_name(item_a_name)
    simplified_b = normalize_drug_name(item_b_name)
    if not simplified_a or not simplified_b:
        return None
    return tuple(sorted((simplified_a, simplified_b)))


def find_default_data_dir():
    candidates = [
        PROJECT_DIR / "scripts" / "data",
        PROJECT_DIR / "기캡 DB파일",
        DESKTOP_DIR / "기캡 DB파일",
        Path.home() / "Desktop" / "기캡 DB파일",
    ]
    for candidate in candidates:
        if all((candidate / filename).exists() for filename in CSV_FILENAMES):
            return candidate
    return DESKTOP_DIR / "기캡 DB파일"


def detect_csv_encoding(path):
    for encoding in ENCODINGS:
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                reader = csv.reader(handle)
                headers = next(reader)
            if get_csv_layout(headers):
                return encoding
        except UnicodeDecodeError:
            continue
    raise ValueError(f"CSV 컬럼을 인식하지 못했습니다: {path}")


def get_csv_layout(headers):
    header_set = set(headers)
    if {"제품명1", "제품명2", "금기사유"}.issubset(header_set):
        return {
            "item_a": "제품명1",
            "item_b": "제품명2",
            "reason": "금기사유",
        }
    if {"수가코드명칭1", "수가코드명칭2"}.issubset(header_set):
        return {
            "item_a": "수가코드명칭1",
            "item_b": "수가코드명칭2",
            "reason": "적용구분",
        }
    return None


def iter_csv_records(path):
    encoding = detect_csv_encoding(path)
    with path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle)
        layout = get_csv_layout(reader.fieldnames or [])
        if not layout:
            raise ValueError(f"CSV 컬럼을 인식하지 못했습니다: {path}")

        for row in reader:
            item_a_name = (row.get(layout["item_a"]) or "").strip()
            item_b_name = (row.get(layout["item_b"]) or "").strip()
            prohibit_content = (row.get(layout["reason"]) or "").strip()
            if not item_a_name or not item_b_name:
                continue
            if prohibit_content == "적용":
                prohibit_content = "DUR 병용금기 적용"
            yield {
                "item_a_name": item_a_name,
                "item_b_name": item_b_name,
                "simplified_a": normalize_drug_name(item_a_name),
                "simplified_b": normalize_drug_name(item_b_name),
                "prohibit_content": prohibit_content or "DUR 병용금기 데이터",
                "category": CATEGORY,
                "target_group": TARGET_GROUP,
            }


def load_existing_pairs(db):
    existing = set()
    rows = db.query(
        Interaction.item_a_name,
        Interaction.item_b_name,
        Interaction.simplified_a,
        Interaction.simplified_b,
    ).all()
    for item_a_name, item_b_name, simplified_a, simplified_b in rows:
        for key in (
            pair_key(item_a_name, item_b_name),
            pair_key(simplified_a, simplified_b),
        ):
            if key:
                existing.add(key)
    return existing


def create_indexes():
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_interactions_simplified_pair "
                "ON interactions (simplified_a, simplified_b)"
            )
        )


def flush_batch(db, batch, dry_run):
    if not batch:
        return
    if dry_run:
        return
    db.bulk_save_objects(batch)
    db.commit()


def import_dur_csv(data_dir, batch_size, dry_run):
    Base.metadata.create_all(bind=engine)
    create_indexes()

    db = SessionLocal()
    try:
        existing_pairs = load_existing_pairs(db)
        print(f"기존 병용금기 조합: {len(existing_pairs):,}건")

        inserted = 0
        skipped = 0
        scanned = 0
        batch = []

        for filename in CSV_FILENAMES:
            path = data_dir / filename
            if not path.exists():
                raise FileNotFoundError(f"CSV 파일을 찾을 수 없습니다: {path}")

            print(f"읽는 중: {path}")
            for record in iter_csv_records(path):
                scanned += 1
                key = pair_key(record["item_a_name"], record["item_b_name"])
                if not key or key in existing_pairs:
                    skipped += 1
                    continue

                existing_pairs.add(key)
                inserted += 1
                batch.append(Interaction(**record))

                if len(batch) >= batch_size:
                    flush_batch(db, batch, dry_run)
                    print(f"진행: 스캔 {scanned:,} / 추가 {inserted:,} / 중복 {skipped:,}")
                    batch.clear()

            print(f"완료: {filename}")

        flush_batch(db, batch, dry_run)
        print("--- DUR CSV import 완료 ---")
        print(f"스캔: {scanned:,}건")
        print(f"추가: {inserted:,}건")
        print(f"중복/건너뜀: {skipped:,}건")
        if dry_run:
            print("dry-run 모드라 DB에는 저장하지 않았습니다.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def parse_args():
    parser = argparse.ArgumentParser(description="Import DUR CSV files into interactions.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=find_default_data_dir(),
        help="dur_data.csv와 new_dur_data.csv가 있는 폴더",
    )
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    import_dur_csv(args.data_dir, args.batch_size, args.dry_run)
