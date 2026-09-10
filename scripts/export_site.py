"""books_metrics / age_ranks / book_tags → 사이트 데이터

  site/src/data/meta.json, ages/{a}.json(기본 목록 + 필터 facet), books.json(정적 상세 페이지용)
  site/public/data/search_index.json, books/NNN.json(전체 상세 조각), pool/{a}.json(나이별 확장 풀: 필터용)

실행: python scripts/export_site.py            # data/processed 사용
      python scripts/export_site.py --sample   # data/sample (합성) 사용
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_metrics import CENT_MIN, GAMMA, MIN_LOANS_AT_AGE  # noqa: E402
from tags import KDC1_NAME, KDC2_NAME  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
SITE_DATA = ROOT / "site" / "src" / "data"
PUB = ROOT / "site" / "public" / "data"
SHOW_AGES = list(range(1, 8))     # 사이트 노출 1~7세 (0세 코호트는 기관 대출 오염으로 제외, 1세를 "0~1세"로 표기)
AGE_LABELS = {1: "0~1세"}
ALL_AGES = list(range(1, 14))     # 프로필 막대 (실제로는 books 컬럼에서 재계산)
TOP_N = 300                       # 나이별 fit/pop 각 300권 → 상세 페이지 생성 대상
SEARCH_MAX = 30000                # 검색 색인에 넣을 도서 수 (총대출 상위)
SERIES_CAP = 3                    # 같은 시리즈(전집)는 목록당 최대 3권
POOL_PER_FACET = 30               # 나이 × (세부 라벨 / 형태 / 출판사 / 성격) 마다 상위 30권
POOL_PUBLISHERS = 40              # 나이별 출판사 facet 수 (그 나이 후보 도서 수 기준 상위)
FLAG_TAGS = ["요즘 인기", "베스트셀러", "여러 나이 스테디", "10년 스테디셀러", "먼저 보기 좋은", "커서도 보는"]
FLAG_CODE = {t: i for i, t in enumerate(FLAG_TAGS)}


def series_key(b: pd.Series) -> str:
    """시리즈·전집 묶음 키.
    1) 제목이 '(시리즈명) …' 이면 괄호 안, 2) '시리즈명 :부제' 이면 콜론 앞,
    3) 아니면 ISBN 출판사 접두(9자리) + 출판년 + 부가기호 (출판사명이 비어도 동작)."""
    t = str(b.bookname or "").strip()
    m = re.match(r"^\(([^)]{2,})\)", t)
    if m:
        return "T:" + re.sub(r"\s+", "", m.group(1))
    if ":" in t:
        head = re.sub(r"\s+", "", t.split(":", 1)[0])
        if len(head) >= 2:
            return "T:" + head
    return f"{str(b.isbn13)[:9]}|{b.publication_year}|{b.addition_symbol}"


def diversify(df: pd.DataFrame, books: pd.DataFrame, n: int, cap: int = SERIES_CAP) -> pd.DataFrame:
    """순위 순서를 유지하면서 시리즈 키당 cap 권까지만 남기고 n 권을 채운다."""
    keep, seen = [], {}
    for i, isbn in enumerate(df.isbn13):
        k = series_key(books.loc[isbn])
        if seen.get(k, 0) >= cap:
            continue
        seen[k] = seen.get(k, 0) + 1
        keep.append(i)
        if len(keep) >= n:
            break
    return df.iloc[keep]


def flags_of(b: pd.Series) -> list[str]:
    return [t for t in FLAG_TAGS if bool(b.get(t, False))]


HOLDINGS: dict[str, list[str]] = {}   # isbn13 → libCode 목록 (핵심 도서만)


def book_card(b: pd.Series) -> dict:
    return {
        "libs": HOLDINGS.get(b.isbn13, []),
        "isbn13": b.isbn13, "title": b.bookname, "authors": b.authors,
        "publisher": b.publisher if (b.publisher or "").strip() else "(출판사 정보 없음)",
        "year": b.publication_year, "cover": b.bookImageURL or "", "detail": b.bookDtlUrl or "",
        "picture": bool(b.is_picture), "total": int(b.total_loans),
        "peak": int(b.peak_age), "band": b.band, "spread": float(b.spread),
        "median": float(b.get("median_age", b.peak_age)),
        "skew": float(b.get("skew", 0.0)), "shape": str(b.get("shape", "")),
        "kdc2": str(b.get("kdc2", "") or ""), "kdc2n": str(b.get("kdc2_name", "") or ""), "kdc1n": str(b.get("kdc1_name", "") or ""),
        "form": str(b.get("form", "") or ""), "pubn": str(b.get("publisher_norm", "") or ""),
        "flags": flags_of(b), "sk": series_key(b),
        "upper": [int(b.get(f"upper_{a}", b[f"loan_{a}"])) for a in ALL_AGES],
        "prof": [float(b[f"prof_{a}"]) for a in ALL_AGES],
        "loans": [int(b[f"loan_{a}"]) for a in ALL_AGES],
        "lifts": [float(b[f"lift_{a}"]) for a in ALL_AGES],
        "cents": [float(b.get(f"cent_{a}", 0.0)) for a in ALL_AGES],
        "specs": [float(b.get(f"spec_{a}", 0.0)) for a in ALL_AGES],
        "fits": [int(round(float(b.get(f"fit_{a}", 0.0)))) for a in ALL_AGES],
        "obs": [bool(b[f"obs_{a}"]) for a in ALL_AGES],
    }


def pool_card(b: pd.Series, x) -> dict:
    """나이별 확장 풀용 압축 카드 (x: 그 나이의 ranks 행, itertuples)."""
    return {
        "i": b.isbn13, "t": (b.bookname or "").strip(), "au": (b.authors or "")[:40],
        "pu": (b.publisher if (b.publisher or "").strip() else "(출판사 정보 없음)")[:20], "yr": str(b.publication_year or ""),
        "cv": b.bookImageURL or "", "band": b.band, "med": round(float(b.get("median_age", b.peak_age)), 1),
        "sh": str(b.get("shape", "")), "pic": 1 if bool(b.is_picture) else 0,
        "k2": str(b.get("kdc2", "") or ""), "fm": str(b.get("form", "") or ""), "pn": str(b.get("publisher_norm", "") or ""),
        "fl": [FLAG_CODE[t] for t in flags_of(b)], "sk": series_key(b),
        "ln": int(x.loan_count), "ce": round(float(x.cent), 2), "sp": round(float(x.spec), 1), "fit": int(round(float(x.fit_score))),
        "pr": int(x.pop_rank) if pd.notna(x.pop_rank) else 0,
        "prof": [int(round(100 * float(b[f"prof_{a}"]))) for a in ALL_AGES],
    }


def write_shards(books: pd.DataFrame, out_dir: Path, nshards: int = 1000) -> None:
    """전체 도서 상세 데이터를 ISBN 끝 세 자리로 나눈 조각 JSON (public/data/books/NNN.json, 약 90KB)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    shards: dict[str, dict] = {f"{i:03d}": {} for i in range(nshards)}
    shards["xxx"] = {}                                # ISBN 끝 세 자리가 숫자가 아닌 예외 (예: '…(2)')
    for b in books.itertuples():
        card = book_card(books.loc[b.isbn13])
        card.pop("upper", None)                       # 조각 용량 절감
        card["prof"] = [round(v, 3) for v in card["prof"]]
        card["cents"] = [round(v, 2) for v in card["cents"]]
        card["specs"] = [round(v, 1) for v in card["specs"]]
        card["lifts"] = [round(v, 2) for v in card["lifts"]]
        tail = str(b.isbn13)[-3:]
        shards[tail if tail.isdigit() else "xxx"][b.isbn13] = card
    total = 0
    for k, v in shards.items():
        p = out_dir / f"{k}.json"
        p.write_text(json.dumps(v, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        total += p.stat().st_size
    print(f"book shards: {len(books):,} books → {nshards} files, {total / 1024 / 1024:.1f} MB")


def build_pool(a: int, ranks_a: pd.DataFrame, books: pd.DataFrame) -> tuple[dict, dict]:
    """나이 a 의 확장 풀과 facet 요약. 후보(fit_rank 有)를 fit 순으로 두고 facet 마다 상위 POOL_PER_FACET 권을 합집합."""
    el = ranks_a[ranks_a.fit_rank.notna()].sort_values("fit_rank")
    el = el.merge(books[["kdc2", "kdc2_name", "kdc1_name", "form", "publisher_norm", *FLAG_TAGS]],
                  left_on="isbn13", right_index=True)
    chosen: set[str] = set(el.head(TOP_N * 3).isbn13)          # 전체 상위(시리즈 캡 전 3배 여유)
    facets: dict[str, list] = {"kdc2": [], "form": [], "pub": [], "flag": []}
    for k2, g in el[el.kdc2 != ""].groupby("kdc2"):
        chosen |= set(g.head(POOL_PER_FACET).isbn13)
        facets["kdc2"].append([k2, KDC2_NAME.get(k2, k2), KDC1_NAME.get(k2[:1], ""), int(len(g))])
    for fm, g in el.groupby("form"):
        chosen |= set(g.head(POOL_PER_FACET).isbn13)
        facets["form"].append([fm, int(len(g))])
    top_pubs = el[el.publisher_norm != ""].publisher_norm.value_counts().head(POOL_PUBLISHERS)
    for pn in top_pubs.index:
        g = el[el.publisher_norm == pn]
        chosen |= set(g.head(POOL_PER_FACET).isbn13)
        facets["pub"].append([pn, int(len(g))])
    for t in FLAG_TAGS:
        g = el[el[t].astype(bool)]
        chosen |= set(g.head(POOL_PER_FACET).isbn13)
        facets["flag"].append([FLAG_CODE[t], t, int(len(g))])
    facets["kdc2"].sort(key=lambda r: (r[0][:1], -r[3]))
    facets["form"].sort(key=lambda r: -r[1])
    pool_rows = el[el.isbn13.isin(chosen)]
    cards = [pool_card(books.loc[x.isbn13], x) for x in pool_rows.itertuples()]
    return {"age": a, "n_eligible": int(len(el)), "books": cards}, facets


def export_holdings(src: Path) -> dict:
    """도서관 디렉터리(libs.json)와 도서관별 소장 ISBN 파일(libs/{code}.json). 반환: meta 용 요약."""
    global HOLDINGS
    hp, lp = src / "holdings.parquet", src / "libs.parquet"
    if not hp.exists() or not lp.exists():
        return {"regions": [], "n_books": 0, "n_libs": 0}
    h = pd.read_parquet(hp)
    h = h[h.libCode.astype(str) != ""]
    libs = pd.read_parquet(lp)
    regions = sorted(pd.read_parquet(hp).region.astype(str).unique())
    HOLDINGS = h.groupby("isbn13")["libCode"].apply(lambda s: sorted(set(s.astype(str)))).to_dict()
    by_lib = h.groupby("libCode")["isbn13"].apply(lambda s: sorted(set(s))).to_dict()
    d = PUB / "libs"
    d.mkdir(parents=True, exist_ok=True)
    for code, isbns in by_lib.items():
        (d / f"{code}.json").write_text(json.dumps({"n": len(isbns), "isbns": isbns}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    def s_(v):
        return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)
    rows = []
    for x in libs.itertuples():
        rows.append([str(x.libCode), s_(x.libName), s_(x.address), None if pd.isna(x.latitude) else round(float(x.latitude), 5),
                     None if pd.isna(x.longitude) else round(float(x.longitude), 5), str(x.region), s_(x.homepage),
                     s_(x.operatingTime), s_(x.closed), int(len(by_lib.get(str(x.libCode), [])))])
    (PUB / "libs.json").write_text(json.dumps({"fields": ["code", "name", "addr", "lat", "lon", "region", "home", "hours", "closed", "n"],
                                                "regions": regions, "libs": rows}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"holdings: 책 {len(HOLDINGS):,}권, 도서관 {len(by_lib):,}곳(소장 파일), 디렉터리 {len(rows):,}곳, 지역 {regions}")
    return {"regions": regions, "n_books": len(HOLDINGS), "n_libs": len(by_lib)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--top", type=int, default=TOP_N)
    args = ap.parse_args()
    src = ROOT / "data" / ("sample" if args.sample else "processed")
    holdings_meta = export_holdings(src)
    books = pd.read_parquet(src / "books_metrics.parquet").set_index("isbn13", drop=False)
    ranks = pd.read_parquet(src / "age_ranks.parquet")
    tags_p = src / "book_tags.parquet"
    if tags_p.exists():
        tags = pd.read_parquet(tags_p).set_index("isbn13")
        books = books.join(tags.drop(columns=[c for c in tags.columns if c in books.columns]))
    for c in ("kdc2", "kdc2_name", "kdc1_name", "form", "publisher_norm"):
        if c not in books.columns:
            books[c] = ""
        books[c] = books[c].fillna("")
    for t in FLAG_TAGS:
        if t not in books.columns:
            books[t] = False
        books[t] = books[t].fillna(False).astype(bool)
    global ALL_AGES
    ALL_AGES = sorted(int(c[5:]) for c in books.columns if c.startswith("prof_"))
    show = [a for a in SHOW_AGES if a in ALL_AGES]

    (SITE_DATA / "ages").mkdir(parents=True, exist_ok=True)
    (PUB / "pool").mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    for a in show:
        r = ranks[ranks.age == a]
        pop = diversify(r[r.observed].nsmallest(args.top * 3, "pop_rank"), books, args.top)
        fit = diversify(r[r.fit_rank.notna()].nsmallest(args.top * 3, "fit_rank"), books, args.top)

        def rows(df):
            out = []
            for _, x in df.iterrows():
                c = book_card(books.loc[x.isbn13]); c["loan_count"] = int(x.loan_count); c["lift"] = float(x.lift)
                c["cent"] = float(x.get("cent", 0.0)); c["spec"] = float(x.get("spec", 0.0))
                c["fit"] = float(x.fit_score); c["observed"] = bool(x.observed); out.append(c); used.add(x.isbn13)
            return out
        pool, facets = build_pool(a, r, books)
        payload = {"age": a, "popular": rows(pop), "fit": rows(fit), "facets": facets, "n_eligible": pool["n_eligible"]}
        (SITE_DATA / "ages" / f"{a}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        pp = PUB / "pool" / f"{a}.json"
        pp.write_text(json.dumps(pool, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"  {a}세 풀 {len(pool['books']):,}권 ({pp.stat().st_size / 1024:.0f} KB), facet kdc2 {len(facets['kdc2'])} / 출판사 {len(facets['pub'])}")

    detail = {i: book_card(books.loc[i]) for i in sorted(used)}
    (SITE_DATA / "books.json").write_text(json.dumps(detail, ensure_ascii=False), encoding="utf-8")

    # 검색 색인: [isbn, 제목, 저자, 출판사, 출판년, 밴드, peak, 총대출, prof%, 상세페이지, 그림책, seq, kdc2, 형태, 출판사정규화, 성격코드]
    idx = []
    top_books = books.sort_values("total_loans", ascending=False).head(SEARCH_MAX)
    for b in top_books.itertuples():
        row = books.loc[b.isbn13]
        idx.append([b.isbn13, (b.bookname or "").strip(), (b.authors or "")[:40], (b.publisher or "")[:20],
                    str(b.publication_year or ""), b.band, int(b.peak_age), int(b.total_loans),
                    [int(round(100 * float(getattr(b, f"prof_{a}")))) for a in ALL_AGES],
                    1 if b.isbn13 in used else 0, 1 if bool(b.is_picture) else 0,
                    (b.bookDtlUrl or "").split("seq=")[-1] if "seq=" in (b.bookDtlUrl or "") else "",
                    str(row.get("kdc2", "") or ""), str(row.get("form", "") or ""), str(row.get("publisher_norm", "") or ""),
                    [FLAG_CODE[t] for t in flags_of(row)]])
    PUB.mkdir(parents=True, exist_ok=True)
    (PUB / "search_index.json").write_text(json.dumps(idx, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    write_shards(books, PUB / "books")
    (SITE_DATA / "search_meta.json").write_text(json.dumps({"n": len(idx)}), encoding="utf-8")
    print(f"search index: {len(idx):,} books, {(PUB / 'search_index.json').stat().st_size / 1024:.0f} KB → site/public/data/")

    meta = {"generated": dt.date.today().isoformat(), "build": dt.datetime.now().strftime("%Y%m%d%H%M%S"), "sample": args.sample, "ages": show,
            "labels": {str(a): AGE_LABELS.get(a, f"{a}세") for a in show},
            "profile_ages": ALL_AGES, "n_books_total": int(len(books)), "n_books_site": len(used),
            "cent_min": CENT_MIN, "min_loans": MIN_LOANS_AT_AGE, "gamma": GAMMA,
            "kdc2_names": KDC2_NAME, "kdc1_names": KDC1_NAME, "flag_tags": FLAG_TAGS,
            "forms": ["그림책", "전집", "단행본", "도감", "기타"],
            "holdings": holdings_meta,
            "region_names": {"11": "서울", "21": "부산", "22": "대구", "23": "인천", "24": "광주", "25": "대전", "26": "울산", "29": "세종",
                             "31": "경기", "32": "강원", "33": "충북", "34": "충남", "35": "전북", "36": "전남", "37": "경북", "38": "경남", "39": "제주"},
            "source": "도서관정보나루(data4library.kr) 공공도서관 대출 데이터, 전국, 2014~"}
    (SITE_DATA / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"export → {SITE_DATA}  ages={len(show)}  books={len(used)}  sample={args.sample}")


if __name__ == "__main__":
    main()
