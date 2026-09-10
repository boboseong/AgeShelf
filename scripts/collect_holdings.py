"""도서관 디렉터리 + 핵심 도서의 소장 도서관 수집

  1) libSrch 를 지역별(17회)로 호출해 참여 도서관 전체(1,619곳)를 지역 코드와 함께 저장 → data/processed/libs.parquet
  2) 핵심 도서(site/src/data/books.json 의 키 = 나이별 목록 노출 도서) × 지역 → libSrchByBook (책·지역당 1회, 전체 반환)
     → data/processed/holdings.parquet (isbn13, region, libCode)

실행: python scripts/collect_holdings.py --regions 21,38            # 부산·경남
      python scripts/collect_holdings.py --regions all --books core  # 전 지역
naru.call 캐시를 쓰므로 재실행·증분은 캐시된 호출을 건너뛴다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
import naru  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
REGIONS = {"11": "서울", "21": "부산", "22": "대구", "23": "인천", "24": "광주", "25": "대전", "26": "울산", "29": "세종",
           "31": "경기", "32": "강원", "33": "충북", "34": "충남", "35": "전북", "36": "전남", "37": "경북", "38": "경남", "39": "제주"}
LIB_FIELDS = ["libCode", "libName", "address", "tel", "latitude", "longitude", "homepage", "closed", "operatingTime", "BookCount"]


def fetch_directory() -> pd.DataFrame:
    rows = []
    for code, name in REGIONS.items():
        r = naru.call("libSrch", region=code, pageNo=1, pageSize=2000)
        resp = r.get("response", r)
        for x in resp.get("libs", []) or []:
            lib = x.get("lib", x)
            rows.append({k: lib.get(k) for k in LIB_FIELDS} | {"region": code, "region_name": name})
        print(f"  {name}({code}): {len(resp.get('libs', []) or [])}곳", flush=True)
    df = pd.DataFrame(rows).drop_duplicates("libCode")
    for c in ("latitude", "longitude"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["BookCount"] = pd.to_numeric(df["BookCount"], errors="coerce").fillna(0).astype(int)
    df.to_parquet(PROC / "libs.parquet", index=False)
    print(f"디렉터리: {len(df):,}곳 → libs.parquet")
    return df


def core_isbns() -> list[str]:
    p = ROOT / "site" / "src" / "data" / "books.json"
    return sorted(json.load(open(p, encoding="utf-8")).keys())


def fetch_holdings(isbns: list[str], regions: list[str]) -> pd.DataFrame:
    out_p = PROC / "holdings.parquet"
    old = pd.read_parquet(out_p) if out_p.exists() else pd.DataFrame(columns=["isbn13", "region", "libCode"])
    done = set(zip(old.isbn13, old.region)) if len(old) else set()
    rows, n_calls, t0 = [], 0, time.time()
    total = len(isbns) * len(regions)
    for region in regions:
        for i, isbn in enumerate(isbns):
            if (isbn, region) in done:
                continue
            try:
                r = naru.call("libSrchByBook", isbn=isbn, region=region, pageNo=1, pageSize=2000)
            except naru.NaruError as e:
                print(f"  ! {isbn} {region}: {e}", flush=True)
                continue
            resp = r.get("response", r)
            libs = resp.get("libs", []) or []
            for x in libs:
                rows.append({"isbn13": isbn, "region": region, "libCode": str(x.get("lib", x).get("libCode"))})
            if not libs:                        # 소장 도서관 없음도 '수집 완료'로 표시 (빈 행)
                rows.append({"isbn13": isbn, "region": region, "libCode": ""})
            n_calls += 1
            if n_calls % 100 == 0:
                el = time.time() - t0
                print(f"  {n_calls}회 / 남은 {total - len(done) - n_calls}  ({el/60:.1f}분 경과, 회당 {el/n_calls:.1f}s)", flush=True)
    new = pd.DataFrame(rows, columns=["isbn13", "region", "libCode"])
    allh = pd.concat([old, new], ignore_index=True).drop_duplicates()
    allh.to_parquet(out_p, index=False)
    have = allh[allh.libCode != ""]
    print(f"소장 정보: {len(have):,}쌍, 책 {have.isbn13.nunique():,}권, 도서관 {have.libCode.nunique():,}곳 → holdings.parquet  (이번 호출 {n_calls}회)")
    return allh


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--regions", default="21,38", help="지역 코드 쉼표 구분 또는 all")
    ap.add_argument("--books", choices=["core"], default="core")
    ap.add_argument("--skip-directory", action="store_true")
    args = ap.parse_args()
    regions = list(REGIONS) if args.regions == "all" else [r.strip() for r in args.regions.split(",")]
    if not args.skip_directory or not (PROC / "libs.parquet").exists():
        print("[디렉터리]")
        fetch_directory()
    isbns = core_isbns()
    print(f"[소장 정보] 책 {len(isbns):,}권 × 지역 {regions} = 최대 {len(isbns) * len(regions):,}회")
    fetch_holdings(isbns, regions)


if __name__ == "__main__":
    main()
