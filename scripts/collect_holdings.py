"""도서관 디렉터리 + 도서의 소장 도서관 수집 (지역별, 병렬, 재개 가능)

  1) libSrch 지역별(17회) → data/processed/libs.parquet (참여 도서관 1,619곳, 좌표 포함)
  2) libSrchByBook: 책 × 지역 1회 → data/processed/holdings.parquet (isbn13, region, libCode)
     - 대상 책은 --books 로 선택: core(나이별 목록 노출분) / index(나이별 색인 합집합) / all(전체 지표 도서)
     - 우선순위: 나이별 추천도 최고값이 높은 책부터 (중간에 멈춰도 사용자가 보는 책부터 채워짐)
     - 이미 수집한 (책, 지역) 은 건너뛰므로 재실행이 곧 증분·재개

실행 예:
  python scripts/collect_holdings.py --regions 21 --books index --workers 4     # 부산 전체(약 4시간)
  python scripts/collect_holdings.py --regions 21,38 --books core               # 핵심 도서만(빠름)
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
SAVE_EVERY = 2000            # 중간 저장 간격(호출 수)


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


def target_isbns(kind: str) -> list[str]:
    """대상 ISBN 을 추천도 우선순위로 정렬해 반환."""
    if kind == "core":
        p = ROOT / "site" / "src" / "data" / "books.json"
        base = set(json.load(open(p, encoding="utf-8")).keys())
    elif kind == "index":
        base = set()
        for f in glob.glob(str(ROOT / "site" / "public" / "data" / "index" / "*.json")):
            base |= {r[0] for r in json.load(open(f, encoding="utf-8"))["rows"]}
    else:
        base = set(pd.read_parquet(PROC / "books_metrics.parquet").isbn13)
    ranks = pd.read_parquet(PROC / "age_ranks.parquet")
    best = ranks[ranks.fit_rank.notna()].groupby("isbn13")["fit_score"].max()
    s = pd.Series({i: best.get(i, 0.0) for i in base}).sort_values(ascending=False)
    return list(s.index)


def fetch_holdings(isbns: list[str], regions: list[str], workers: int) -> pd.DataFrame:
    out_p = PROC / "holdings.parquet"
    old = pd.read_parquet(out_p) if out_p.exists() else pd.DataFrame(columns=["isbn13", "region", "libCode"])
    done = set(zip(old.isbn13, old.region)) if len(old) else set()
    jobs = [(i, r) for r in regions for i in isbns if (i, r) not in done]
    print(f"대상 {len(isbns):,}권 × 지역 {regions} → 남은 호출 {len(jobs):,}회 (동시 {workers})", flush=True)
    rows: list[dict] = []
    lock = threading.Lock()
    state = {"n": 0, "err": 0, "t0": time.time()}

    def save():
        new = pd.DataFrame(rows, columns=["isbn13", "region", "libCode"])
        allh = pd.concat([old, new], ignore_index=True).drop_duplicates()
        allh.to_parquet(out_p, index=False)
        return allh

    def work(job):
        isbn, region = job
        try:
            r = naru.call("libSrchByBook", cache=False, isbn=isbn, region=region, pageNo=1, pageSize=2000)
        except Exception as e:   # NaruError 외에 연결 끊김(ConnectionReset) 등도 한 건 오류로 넘기고 계속
            with lock:
                state["err"] += 1
                if state["err"] <= 5:
                    print(f"  ! {isbn} {region}: {e}", flush=True)
            return
        libs = (r.get("response", r)).get("libs", []) or []
        with lock:
            if libs:
                for x in libs:
                    rows.append({"isbn13": isbn, "region": region, "libCode": str(x.get("lib", x).get("libCode"))})
            else:
                rows.append({"isbn13": isbn, "region": region, "libCode": ""})   # 소장 없음도 완료로 기록
            state["n"] += 1
            if state["n"] % SAVE_EVERY == 0:
                save()
                el = time.time() - state["t0"]
                left = (len(jobs) - state["n"]) * el / state["n"] / 3600
                print(f"  {state['n']:,}/{len(jobs):,}회 · {el/60:.0f}분 경과 · 남은 {left:.1f}시간 · 오류 {state['err']}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, jobs))
    allh = save()
    have = allh[allh.libCode != ""]
    print(f"소장 정보: {len(have):,}쌍, 책 {have.isbn13.nunique():,}권, 도서관 {have.libCode.nunique():,}곳 → holdings.parquet "
          f"(이번 {state['n']:,}회, 오류 {state['err']})")
    return allh


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--regions", default="21", help="지역 코드 쉼표 구분 또는 all")
    ap.add_argument("--books", choices=["core", "index", "all"], default="index")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="대상 책 수 상한(우선순위 상위 N권)")
    ap.add_argument("--skip-directory", action="store_true")
    args = ap.parse_args()
    regions = list(REGIONS) if args.regions == "all" else [r.strip() for r in args.regions.split(",")]
    if not args.skip_directory or not (PROC / "libs.parquet").exists():
        print("[디렉터리]")
        fetch_directory()
    isbns = target_isbns(args.books)
    if args.limit:
        isbns = isbns[:args.limit]
    print(f"[소장 정보] books={args.books}")
    fetch_holdings(isbns, regions, args.workers)


if __name__ == "__main__":
    main()
