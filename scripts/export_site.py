"""books_metrics / age_ranks / book_tags / holdings → 사이트 데이터

  site/src/data/meta.json, ages/{a}.json(첫 화면 60권 + 필터 facet), books.json(개월 수 페이지 풀: 나이별 상위 300권 합집합)
  상세 페이지는 정적 생성 없이 /b/?isbn= 이 조각(books/NNN.json)에서 그린다 (2026-09-10 완전 전환)
  site/public/data/index/{a}.json   나이별 얇은 색인: 후보 전체 + 인기순 상위 (필터·정렬·더 보기용)
  site/public/data/search_index.json 검색 색인(전체 도서, 압축 행)
  site/public/data/books/NNN.json   전체 상세 조각,  libs.json / libs/{code}.json 소장 정보

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
from build_metrics import CENT_MIN, GAMMA, MIN_LOANS_AT_AGE, W_CENT, W_POP  # noqa: E402
from tags import KDC1_NAME, KDC2_NAME  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
SITE_DATA = ROOT / "site" / "src" / "data"
PUB = ROOT / "site" / "public" / "data"
SHOW_AGES = list(range(1, 8))     # 사이트 노출 1~7세 (0세 코호트는 기관 대출 오염으로 제외, 1세를 "0~1세"로 표기)
AGE_LABELS = {1: "0~1세"}
ALL_AGES = list(range(1, 14))     # 프로필 막대 (실제로는 books 컬럼에서 재계산)
TOP_N = 300                       # 나이별 fit/pop 각 300권 → books.json(개월 수 페이지 풀)에 포함
TEASER_N = 60                     # 나이 페이지 HTML 에 박는 첫 화면 카드 수 (나머지는 색인에서 클라이언트가 그림)
POP_EXTRA = 3000                  # 색인에 추가로 넣는 인기순 상위(자격 미달 포함)
SEARCH_MAX = None                 # 검색 색인 도서 수 (None = 전체)
SERIES_CAP = None                 # 시리즈당 권수 제한 (None = 제한 없음, 사용자 결정 2026-09-10)
POOL_PUBLISHERS = 60              # 나이별 출판사 facet 수 (그 나이 후보 도서 수 기준 상위)
FLAG_TAGS = ["요즘 인기", "베스트셀러", "여러 나이 스테디", "10년 스테디셀러", "먼저 보기 좋은", "커서도 보는"]
FLAG_CODE = {t: i for i, t in enumerate(FLAG_TAGS)}
FORMS = ["그림책", "전집", "단행본", "도감", "기타"]
SHAPE_CODE = {"대칭": 0, "더 큰 아이 쪽으로 넓음": 1, "더 어린 아이부터 봄": 2}
COVER_PREFIX = ["https://image.aladin.co.kr/product/", "http://image.aladin.co.kr/product/",
                "https://bookthumb-phinf.pstatic.net/cover/", "http://bookthumb-phinf.pstatic.net/cover/"]
Q36 = "0123456789abcdefghijklmnopqrstuvwxyz"
REGION_NAMES = {"11": "서울", "21": "부산", "22": "대구", "23": "인천", "24": "광주", "25": "대전", "26": "울산", "29": "세종",
                "31": "경기", "32": "강원", "33": "충북", "34": "충남", "35": "전북", "36": "전남", "37": "경북", "38": "경남", "39": "제주"}
HOLDINGS: dict[str, list[str]] = {}   # isbn13 → libCode 목록 (핵심 도서만)


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
    """순위 순서를 유지하면서 시리즈 키당 cap 권까지만 남기고 n 권을 채운다. cap 이 없으면 상위 n 권 그대로."""
    if not cap:
        return df.head(n)
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


def flag_bits(b: pd.Series) -> int:
    bits = 0
    for t in FLAG_TAGS:
        if bool(b.get(t, False)):
            bits |= 1 << FLAG_CODE[t]
    return bits


def prof_q(b: pd.Series) -> str:
    """나이별 비중(0~1)을 최고점 대비 0~35 로 양자화한 13글자 문자열 (막대 표시용)."""
    vals = [float(b[f"prof_{a}"]) for a in ALL_AGES]
    m = max(vals) or 1.0
    return "".join(Q36[min(35, int(round(35 * v / m)))] for v in vals)


def cover_pack(url) -> list:
    url = str(url or "")
    for i, pre in enumerate(COVER_PREFIX):
        if url.startswith(pre):
            return [i, url[len(pre):]]
    return [-1, url]


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
        "fits": [round(float(b.get(f"fit_{a}", 0.0)), 1) for a in ALL_AGES],
        "pops": [round(float(b.get(f"pop_{a}", 0.0)), 1) for a in ALL_AGES],
        "obs": [bool(b[f"obs_{a}"]) for a in ALL_AGES],
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


def write_age_index(a: int, ranks_a: pd.DataFrame, books: pd.DataFrame) -> tuple[dict, dict]:
    """나이 a 의 얇은 색인(후보 전체 + 인기순 상위)과 facet 요약.
    행: [isbn, 제목, 저자, 출판사idx, 연도, 표지[prefix,rest], 밴드, 중앙나이, 형태코드, 그림책, kdc2, 형태idx,
         성격비트, 추천도, 인기순위, 위치, 대출, 시리즈id, 비중13자, 자격]"""
    el = ranks_a[ranks_a.fit_rank.notna()].sort_values("fit_rank")
    pop_extra = ranks_a[ranks_a.fit_rank.isna() & ranks_a.pop_rank.notna()].nsmallest(POP_EXTRA, "pop_rank")
    src = pd.concat([el, pop_extra], ignore_index=True)
    pubs: dict[str, int] = {}
    pub_names: list[list[str]] = []
    series: dict[str, int] = {}
    rows = []
    for x in src.itertuples():
        b = books.loc[x.isbn13]
        pn = str(b.get("publisher_norm", "") or "")
        disp = (b.publisher if (b.publisher or "").strip() else "(출판사 정보 없음)")[:20]
        if pn not in pubs:
            pubs[pn] = len(pub_names)
            pub_names.append([pn, disp])
        sid = series.setdefault(series_key(b), len(series))
        form = str(b.get("form", "") or "")
        rows.append([
            x.isbn13, (b.bookname or "").strip(), (b.authors or "")[:30], pubs[pn], str(b.publication_year or ""),
            cover_pack(b.bookImageURL), b.band, round(float(b.get("median_age", b.peak_age)), 1),
            SHAPE_CODE.get(str(b.get("shape", "")), 0), 1 if bool(b.is_picture) else 0, str(b.get("kdc2", "") or ""),
            FORMS.index(form) if form in FORMS else 4, flag_bits(b),
            round(float(x.fit_score), 1) if pd.notna(x.fit_score) else 0,
            int(x.pop_rank) if pd.notna(x.pop_rank) else 0, round(float(x.cent), 2), int(x.loan_count), sid, prof_q(b),
            1 if pd.notna(x.fit_rank) else 0,
        ])
    elm = el.merge(books[["kdc2", "form", "publisher_norm", *FLAG_TAGS]], left_on="isbn13", right_index=True)
    facets: dict[str, list] = {"kdc2": [], "form": [], "pub": [], "flag": []}
    for k2, g in elm[elm.kdc2 != ""].groupby("kdc2"):
        facets["kdc2"].append([k2, KDC2_NAME.get(k2, k2), KDC1_NAME.get(k2[:1], ""), int(len(g))])
    for fm, g in elm.groupby("form"):
        facets["form"].append([fm, int(len(g))])
    for pn, cnt in elm[elm.publisher_norm != ""].publisher_norm.value_counts().head(POOL_PUBLISHERS).items():
        facets["pub"].append([pn, int(cnt)])
    for t in FLAG_TAGS:
        facets["flag"].append([FLAG_CODE[t], t, int(elm[t].astype(bool).sum())])
    facets["kdc2"].sort(key=lambda r: (r[0][:1], -r[3]))
    facets["form"].sort(key=lambda r: -r[1])
    index = {"age": a, "n_eligible": int(len(el)), "n": len(rows), "pubs": pub_names, "forms": FORMS,
             "cover_prefix": COVER_PREFIX, "ages": ALL_AGES, "rows": rows}
    return index, facets


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
    by_region = {r: int(g.isbn13.nunique()) for r, g in h.groupby(h.region.astype(str))}
    print(f"holdings: 책 {len(HOLDINGS):,}권, 도서관 {len(by_lib):,}곳(소장 파일), 디렉터리 {len(rows):,}곳, "
          + "지역별 " + ", ".join(f"{REGION_NAMES.get(r, r)} {n:,}권" for r, n in sorted(by_region.items())))
    return {"regions": regions, "n_books": len(HOLDINGS), "n_libs": len(by_lib), "by_region": by_region}


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
    (PUB / "index").mkdir(parents=True, exist_ok=True)
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
        fit_rows, pop_rows = rows(fit), rows(pop)          # used(상세 페이지 대상)은 300권 기준으로 유지
        index, facets = write_age_index(a, r, books)
        payload = {"age": a, "popular": pop_rows[:TEASER_N], "fit": fit_rows[:TEASER_N], "facets": facets,
                   "n_eligible": index["n_eligible"], "n_index": index["n"]}
        (SITE_DATA / "ages" / f"{a}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        pp = PUB / "index" / f"{a}.json"
        pp.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"  {a}세 색인 {index['n']:,}행 (후보 {index['n_eligible']:,}) {pp.stat().st_size / 1024 / 1024:.1f} MB, "
              f"facet kdc2 {len(facets['kdc2'])} / 출판사 {len(facets['pub'])}")

    detail = {i: book_card(books.loc[i]) for i in sorted(used)}
    (SITE_DATA / "books.json").write_text(json.dumps(detail, ensure_ascii=False), encoding="utf-8")

    # 검색 색인(전체 도서, 압축 행): [isbn, 제목, 저자, 출판사, 연도, 밴드, peak, 총대출, 상세페이지, 그림책, kdc2, 형태idx, 성격비트, 비중13자]
    idx = []
    top_books = books.sort_values("total_loans", ascending=False)
    if SEARCH_MAX:
        top_books = top_books.head(SEARCH_MAX)
    for b in top_books.itertuples():
        row = books.loc[b.isbn13]
        form = str(row.get("form", "") or "")
        idx.append([b.isbn13, (b.bookname or "").strip(), (b.authors or "")[:30], (b.publisher or "")[:15],
                    str(b.publication_year or ""), b.band, int(b.peak_age), int(b.total_loans),
                    1 if b.isbn13 in used else 0, 1 if bool(b.is_picture) else 0,
                    str(row.get("kdc2", "") or ""), FORMS.index(form) if form in FORMS else 4, flag_bits(row), prof_q(row)])
    PUB.mkdir(parents=True, exist_ok=True)
    (PUB / "search_index.json").write_text(json.dumps(idx, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    write_shards(books, PUB / "books")
    (SITE_DATA / "search_meta.json").write_text(json.dumps({"n": len(idx)}), encoding="utf-8")
    print(f"search index: {len(idx):,} books, {(PUB / 'search_index.json').stat().st_size / 1024 / 1024:.1f} MB → site/public/data/")

    meta = {"generated": dt.date.today().isoformat(), "build": dt.datetime.now().strftime("%Y%m%d%H%M%S"), "sample": args.sample, "ages": show,
            "labels": {str(a): AGE_LABELS.get(a, f"{a}세") for a in show},
            "profile_ages": ALL_AGES, "n_books_total": int(len(books)), "n_books_site": len(used),
            "cent_min": CENT_MIN, "min_loans": MIN_LOANS_AT_AGE, "gamma": GAMMA, "w_cent": W_CENT, "w_pop": W_POP,
            "kdc2_names": KDC2_NAME, "kdc1_names": KDC1_NAME, "flag_tags": FLAG_TAGS,
            "forms": FORMS, "teaser_n": TEASER_N,
            "holdings": holdings_meta, "region_names": REGION_NAMES,
            "source": "도서관정보나루(data4library.kr) 공공도서관 대출 데이터, 전국, 2014~"}
    (SITE_DATA / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"export → {SITE_DATA}  ages={len(show)}  books={len(used)}  sample={args.sample}")


if __name__ == "__main__":
    main()
