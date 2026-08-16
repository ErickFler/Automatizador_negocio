from dataclasses import dataclass
from difflib import SequenceMatcher
import re

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.models import MasterProduct
from app.domain.normalization import normalize_for_comparison

MAX_CANDIDATES = 5
SUGGESTION_THRESHOLD = 0.56
_NON_WORD = re.compile(r"[^\w]+", flags=re.UNICODE)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_GENERIC_TOKENS = {
    "de",
    "el",
    "la",
    "con",
    "refresco",
    "pack",
    "paq",
    "paquete",
    "pieza",
    "piezas",
    "l",
    "lt",
    "lts",
    "ml",
    "kg",
    "g",
}


@dataclass(frozen=True, slots=True)
class ProductCandidate:
    master_product: MasterProduct
    score: float


def find_product_candidates(
    session: Session,
    *,
    original_name: str,
    original_category_name: str | None,
    limit: int = MAX_CANDIDATES,
    threshold: float = SUGGESTION_THRESHOLD,
) -> list[ProductCandidate]:
    """Consulta candidatos sin crear relaciones ni modificar la base."""
    source_name = _comparison_text(original_name)
    source_category = normalize_for_comparison(original_category_name or "")
    candidates: list[ProductCandidate] = []
    products = session.scalars(
        select(MasterProduct)
        .options(joinedload(MasterProduct.category))
        .where(MasterProduct.active.is_(True))
    ).all()

    for product in products:
        target_name = _comparison_text(product.display_name)
        score = _similarity_score(source_name, target_name)
        if (
            source_category
            and source_category == product.category.normalized_name
        ):
            score = min(1.0, score + 0.08)
        if score >= threshold:
            candidates.append(ProductCandidate(master_product=product, score=score))

    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.score,
            candidate.master_product.normalized_name,
        ),
    )[:limit]


def search_master_products(
    session: Session, *, query: str, limit: int = 20
) -> list[MasterProduct]:
    normalized_query = _comparison_text(query)
    if not normalized_query:
        return []
    products = session.scalars(
        select(MasterProduct)
        .options(joinedload(MasterProduct.category))
        .where(MasterProduct.active.is_(True))
        .order_by(MasterProduct.normalized_name)
    ).all()
    direct = [
        product
        for product in products
        if normalized_query in _comparison_text(product.display_name)
    ]
    if direct:
        return direct[:limit]
    return [
        candidate.master_product
        for candidate in find_product_candidates(
            session,
            original_name=query,
            original_category_name=None,
            limit=limit,
            threshold=0.48,
        )
    ]


def _comparison_text(value: str) -> str:
    normalized = normalize_for_comparison(value)
    return " ".join(part for part in _NON_WORD.split(normalized) if part)


def _similarity_score(source: str, target: str) -> float:
    if not source or not target:
        return 0.0
    if source == target:
        return 1.0

    source_tokens = set(source.split())
    target_tokens = set(target.split())
    union = source_tokens | target_tokens
    token_score = len(source_tokens & target_tokens) / len(union) if union else 0.0
    sequence_score = SequenceMatcher(None, source, target).ratio()
    score = (sequence_score * 0.65) + (token_score * 0.35)

    source_numbers = set(_NUMBER.findall(source))
    target_numbers = set(_NUMBER.findall(target))
    if source_numbers and target_numbers and source_numbers != target_numbers:
        score *= 0.55
    source_important = source_tokens - _GENERIC_TOKENS - source_numbers
    target_important = target_tokens - _GENERIC_TOKENS - target_numbers
    if source_important and target_important:
        important_union = source_important | target_important
        important_overlap = len(source_important & target_important) / len(
            important_union
        )
        if important_overlap < 0.5:
            score *= 0.55
    return score
