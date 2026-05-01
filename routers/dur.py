import re

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from database import Interaction, SessionLocal


router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def normalize_drug_name(value):
    value = (value or "").strip()
    value = re.sub(r"_\([^)]*\)\s*$", "", value)
    return re.sub(r"[^가-힣A-Za-z0-9]", "", value).lower()


def matches_query(value, query, normalized_query):
    value = value or ""
    if query.lower() in value.lower():
        return True
    return bool(normalized_query and normalized_query in normalize_drug_name(value))


def interaction_to_result(row):
    return {
        "id": row.id,
        "item_a_name": row.item_a_name,
        "item_b_name": row.item_b_name,
        "prohibit_content": row.prohibit_content,
        "category": row.category or "병용금기",
        "target_group": row.target_group or "전체",
    }


@router.get("/check-dur/search")
def search_dur_candidates(
    name: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = name.strip()
    normalized_query = normalize_drug_name(query)
    filters = [
        Interaction.item_a_name.ilike(f"%{query}%"),
        Interaction.item_b_name.ilike(f"%{query}%"),
    ]
    if normalized_query:
        filters.extend(
            [
                Interaction.simplified_a.ilike(f"%{normalized_query}%"),
                Interaction.simplified_b.ilike(f"%{normalized_query}%"),
            ]
        )

    rows = (
        db.query(Interaction)
        .filter(or_(*filters))
        .limit(limit * 4)
        .all()
    )

    results = []
    seen = set()
    for row in rows:
        candidates = (
            ("A", row.item_a_name, row.simplified_a),
            ("B", row.item_b_name, row.simplified_b),
        )
        for side, item_name, simplified in candidates:
            if not item_name:
                continue
            if not (
                matches_query(item_name, query, normalized_query)
                or matches_query(simplified, query, normalized_query)
            ):
                continue
            key = normalize_drug_name(item_name)
            if not key or key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "name": item_name,
                    "matched_side": side,
                    "interaction_id": row.id,
                }
            )
            if len(results) >= limit:
                return {"query": query, "count": len(results), "results": results}

    return {"query": query, "count": len(results), "results": results}


@router.get("/check-dur")
def check_dur_interaction(
    pill_a: str = Query(..., min_length=1),
    pill_b: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    pill_a = pill_a.strip()
    pill_b = pill_b.strip()
    normalized_a = normalize_drug_name(pill_a)
    normalized_b = normalize_drug_name(pill_b)
    filters = [
        and_(
            Interaction.item_a_name.ilike(f"%{pill_a}%"),
            Interaction.item_b_name.ilike(f"%{pill_b}%"),
        ),
        and_(
            Interaction.item_a_name.ilike(f"%{pill_b}%"),
            Interaction.item_b_name.ilike(f"%{pill_a}%"),
        ),
    ]
    if normalized_a and normalized_b:
        filters.extend(
            [
                and_(
                    Interaction.simplified_a.ilike(f"%{normalized_a}%"),
                    Interaction.simplified_b.ilike(f"%{normalized_b}%"),
                ),
                and_(
                    Interaction.simplified_a.ilike(f"%{normalized_b}%"),
                    Interaction.simplified_b.ilike(f"%{normalized_a}%"),
                ),
            ]
        )

    rows = (
        db.query(Interaction)
        .filter(or_(*filters))
        .limit(limit)
        .all()
    )

    return {
        "pill_a": pill_a,
        "pill_b": pill_b,
        "is_prohibited": bool(rows),
        "count": len(rows),
        "message": (
            "DB에서 병용금기 조합을 찾았습니다."
            if rows
            else "DB에서 해당 병용금기 조합을 찾지 못했습니다. 없다는 결과가 안전함을 보장하지는 않습니다."
        ),
        "matches": [interaction_to_result(row) for row in rows],
    }
