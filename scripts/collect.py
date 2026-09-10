"""연령 × 기간(× 분할)별 인기대출도서 수집 → data/processed/loans_long*.parquet + cells*.parquet

실행 예:
  python scripts/collect.py --slices allonly --region        # 전체기간, 0~13세, 17개 지역 분할 (238회) ← 기본 권장
  python scripts/collect.py --slices allonly --region --gender   # + 성별 분할 (714회)
  python scripts/collect.py --slices all                      # 연도별·최근12개월 슬라이스(무분할, 추이용)
  python scripts/collect.py --no-kids-filter --ages 0-3       # addCode 없이(오염 비교용)
  python scripts/collect.py --parents                         # 30·40대 카드로 빌린 아동서(보조 랭킹)

검증된 API 특성 (2026-09-09 스모크 테스트)
  * pageSize=5000 한 번에 가능. 상한은 '행' 기준이며 같은 ISBN 이 vol 표기 차이로 여러 행에 나뉜다
    → 한 쿼리 안에서는 ISBN 별로 loan_count 를 합산해야 한다 (build_metrics 에서 처리).
  * 무분할 전국 상위 5,000행은 고유 ISBN 4,280권·대출의 59% 만 담았고, 지역 17개 분할 합집합은 28,667권·101k 대출.
  * 연령은 대출 당시 나이. addCode 는 1자리(7=아동)만 허용.

컷오프(5,000행 상한) 처리 원칙
  * 셀(slice, age, region, gender, kcell) 하나가 5,000행 미만이면 '완전 관측' → 없는 책은 0건.
  * 5,000행이면 '캡됨' → 없는 책은 [0, cutoff) 구간 (cutoff = 마지막 행의 loan_count).
  * cells.parquet 에 셀별 n/capped/cutoff 를 기록해 build_metrics 가 상한을 계산한다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
import naru  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed"
CAP = 5000
DATA_START = "2014-01-01"
KEEP = ["ranking", "isbn13", "bookname", "authors", "publisher", "publication_year",
        "addition_symbol", "vol", "class_no", "class_nm", "bookImageURL", "bookDtlUrl", "loan_count"]
REGIONS = ["11", "21", "22", "23", "24", "25", "26", "29", "31", "32", "33", "34", "35", "36", "37", "38", "39"]
# KDC 셀: 대주제 0~7,9 는 kdc=, 문학(8)은 세부주제 80~89 로 쪼갬 (도서 속성 → 합집합)
KCELLS = [("kdc", str(k)) for k in (0, 1, 2, 3, 4, 5, 6, 7, 9)] + [("dtl_kdc", str(k)) for k in range(80, 90)]


def yesterday() -> str:
    return (dt.date.today() - dt.timedelta(days=1)).isoformat()


def slices(kind: str) -> dict[str, tuple[str, str]]:
    end = yesterday()
    s = {"all": (DATA_START, end),
         "last12m": ((dt.date.today() - dt.timedelta(days=365)).isoformat(), end)}
    if kind == "all":
        for year in range(2014, dt.date.today().year + 1):
            s[f"y{year}"] = (f"{year}-01-01", min(f"{year}-12-31", end))
    if kind == "allonly":
        s = {"all": s["all"]}
    if kind == "last12m":
        s = {"last12m": s["last12m"]}
    return s


def fetch_cell(slice_name, start, end, age_kw, add_code, region, gender, kcell, page_size):
    """한 셀 수집. 반환: (rows DataFrame, cell record)"""
    extra = {}
    if region != "*":
        extra["region"] = region
    if gender != "*":
        extra["gender"] = gender
    if kcell != "*":
        param, val = kcell.split("=")
        extra[param] = val
    t = time.time()
    rows = naru.popular_all(max_items=CAP, start=start, end=end, add_code=add_code,
                            page_size=page_size, **age_kw, **extra)
    age_label = str(age_kw.get("from_age", age_kw.get("age")))
    capped = len(rows) >= CAP
    cutoff = int(rows[-1]["loan_count"]) if rows else 0
    rec = dict(slice=slice_name, age=age_label, region=region, gender=gender, kcell=kcell,
               n=len(rows), capped=capped, cutoff=cutoff if capped else 0)
    print(f"  {slice_name:>8} age={age_label:>5} r={region:<2} g={gender} k={kcell:<10} {len(rows):>5}행"
          f"{' CAP cutoff=' + str(cutoff) if capped else ''}  {time.time() - t:5.1f}s", flush=True)
    if not rows:
        return pd.DataFrame(columns=["slice", "age", "region", "gender", "kcell", *KEEP]), rec
    df = pd.DataFrame(rows)
    for c in KEEP:
        if c not in df.columns:
            df[c] = None
    df = df[KEEP].copy()
    for k, v in (("kcell", kcell), ("gender", gender), ("region", region), ("age", age_label), ("slice", slice_name)):
        df.insert(0, k, v)
    return df, rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slices", choices=["basic", "all", "allonly", "last12m"], default="basic")
    ap.add_argument("--ages", default="0-13", help="초등까지 수집해야 lift 분모가 맞음")
    ap.add_argument("--page-size", type=int, default=5000, help="한 번에 5,000행 가능(검증됨)")
    ap.add_argument("--region", action="store_true", help="지역 17개 분할(합산 대상)")
    ap.add_argument("--gender", action="store_true", help="성별 0;1;2 분할(합산 대상)")
    ap.add_argument("--kdc", action="store_true", help="KDC 셀 분할(합집합 대상, 기본 셀 목록)")
    ap.add_argument("--cells", default=None,
                    help="KDC 셀 직접 지정. 예: 'kdc=0,kdc=1,dtl_kdc=81' (무분할 '*' 는 포함하지 않음)")
    ap.add_argument("--tag", default=None, help="출력 파일 접미사 (예: kdc → loans_long_kdc.parquet)")
    ap.add_argument("--no-kids-filter", action="store_true")
    ap.add_argument("--parents", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if "-" in args.ages:
        a, b = args.ages.split("-")
        ages = list(range(int(a), int(b) + 1))
    else:
        ages = [int(x) for x in args.ages.split(",")]
    add_code = None if args.no_kids_filter else 7
    regions = REGIONS if args.region else ["*"]
    genders = ["0", "1", "2"] if args.gender else ["*"]
    if args.cells:
        kcells = [c.strip() for c in args.cells.split(",") if c.strip()]
    elif args.kdc:
        kcells = ["*"] + [f"{p}={v}" for p, v in KCELLS]
    else:
        kcells = ["*"]

    frames, cells = [], []
    for name, (start, end) in slices(args.slices).items():
        print(f"[{name}] {start} ~ {end}", flush=True)
        for a in ages:
            for r in regions:
                for g in genders:
                    for k in kcells:
                        df, rec = fetch_cell(name, start, end, dict(from_age=a, to_age=a), add_code, r, g, k, args.page_size)
                        frames.append(df)
                        cells.append(rec)
        if args.parents:
            df, rec = fetch_cell(name, start, end, dict(age="30;40"), 7, "*", "*", "*", args.page_size)
            frames.append(df)
            cells.append(rec)

    long = pd.concat(frames, ignore_index=True)
    OUT.mkdir(parents=True, exist_ok=True)
    tag = f"_{args.tag}" if args.tag else (("_region" if args.region else "") + ("_gender" if args.gender else "")
                                            + ("_kdc" if (args.kdc or args.cells) else "") + ("" if add_code else "_nofilter"))
    out = Path(args.out) if args.out else OUT / f"loans_long{tag}.parquet"
    long.to_parquet(out, index=False)
    pd.DataFrame(cells).to_parquet(out.with_name(out.name.replace("loans_long", "cells")), index=False)
    print(f"\n저장: {out}  ({len(long):,} rows, {long.isbn13.nunique():,} ISBN, {len(cells)} cells)")


if __name__ == "__main__":
    main()
