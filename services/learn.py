"""학습 가이드 로더 — docs/learn/*/*.md 를 읽어 메모리 캐시."""

import re
from dataclasses import dataclass, field
from pathlib import Path

import markdown as md

from config import get_logger

logger = get_logger("learn")

LEARN_DIR = Path(__file__).parent.parent / "docs" / "learn"

CATEGORY_LABELS = {
    "stock": "주식",
    "crypto": "코인",
    "macro": "매크로",
    "account": "세금/계좌",
    "tradelab": "TradeLab 용어",
}

# 사이드바/인덱스에서 노출되는 순서
CATEGORY_ORDER = ["stock", "crypto", "macro", "account", "tradelab"]


@dataclass
class Article:
    slug: str
    title: str
    category: str
    order: int
    summary: str
    body_md: str = ""
    body_html: str = ""


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """YAML 풍 frontmatter 파싱 (간단 key: value 만 지원)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw, body = m.group(1), m.group(2)
    meta: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, body


def _render(body_md: str) -> str:
    return md.markdown(
        body_md,
        extensions=["tables", "fenced_code"],
    )


_cache: dict[str, Article] = {}
_loaded = False


def _load_all() -> None:
    global _cache, _loaded
    _cache = {}
    if not LEARN_DIR.exists():
        logger.warning(f"learn dir not found: {LEARN_DIR}")
        _loaded = True
        return

    for md_path in LEARN_DIR.rglob("*.md"):
        try:
            text = md_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"failed to read {md_path}: {e}")
            continue

        meta, body = _parse_frontmatter(text)
        slug = meta.get("slug") or md_path.stem
        title = meta.get("title", slug)
        category = meta.get("category") or md_path.parent.name
        try:
            order = int(meta.get("order", "999"))
        except ValueError:
            order = 999
        summary = meta.get("summary", "")

        if slug in _cache:
            logger.warning(f"duplicate slug {slug} ({md_path})")
        _cache[slug] = Article(
            slug=slug, title=title, category=category, order=order,
            summary=summary, body_md=body, body_html=_render(body),
        )
    _loaded = True
    logger.info(f"learn: loaded {len(_cache)} articles")


def get_article(slug: str) -> Article | None:
    if not _loaded:
        _load_all()
    return _cache.get(slug)


def list_by_category() -> dict[str, list[Article]]:
    """{ category: [Article, ...] } — 카테고리 순서 + 글 order 정렬."""
    if not _loaded:
        _load_all()
    grouped: dict[str, list[Article]] = {c: [] for c in CATEGORY_ORDER}
    for art in _cache.values():
        grouped.setdefault(art.category, []).append(art)
    for arts in grouped.values():
        arts.sort(key=lambda a: (a.order, a.title))
    # 빈 카테고리 제거
    return {c: arts for c, arts in grouped.items() if arts}


def reload_cache() -> None:
    """개발 중 수동 리로드."""
    global _loaded
    _loaded = False
    _load_all()
