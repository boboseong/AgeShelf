"""도서관 장서 목록(itemSrch) 방식 소장 수집 — 책×지역(libSrchByBook) 방식보다 호출이 훨씬 적은 지역용.

  책×지역 방식은 지역마다 대상 도서 수(약 6만)만큼 호출한다. 장서 목록 방식은 그 지역 도서관들의 장서를
  500건씩 받아 우리 대상 ISBN 과 대조하므로 호출 수 = Σ ceil(장서 수 / 500). 경기(400곳, 4천만 권)를 뺀
  13개 지역은 모두 이쪽이 싸다(2026-09-27 계산: 13.8만 회 vs 77만 회).
  검증(2026-09-27): 부산 우암도서관 장서 74,407건을 149회로 받아, 책×지역 방식으로 찾은 우리 책 8,080권과 100% 일치.

  - 지역 순서대로 하나씩 끝낸다. 지역의 모든 도서관을 다 받으면 그때 holdings.parquet 에 합친다
    (그 지역 기존 행 ∪ 장서 목록 결과 + 어디에도 없는 책은 libCode '' = 소장 없음으로 기록).
    반쯤 받은 지역은 holdings_dump.parquet·holdings_dump_state.json 에만 있어 사이트에 섞이지 않는다.
  - 진행은 페이지 단위로 저장(200 페이지마다)하므로 멈췄다 다시 띄우면 이어서 한다.
  - 일일 호출 한도(outOfMaxlimit)에 걸리면 즉시 멈춘다. 같은 도서관이 3회차 연속 오류면 오류로 표시하고 넘어간다.

실행: python scripts/collect_holdings_dump.py --regions 26,22,37 [--workers 4]
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
import naru  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
STATE_P = PROC / "holdings_dump_state.json"
DUMP_P = PROC / "holdings_dump.parquet"
HOLD_P = PROC / "holdings.parquet"
PAGE = 500
SAVE_EVERY = 200
MAX_ERR_RUNS = 3


def targets() -> set[str]:
    b = pd.read_parquet(PROC / "books_metrics.parquet", columns=["isbn13", "total_loans"])
    site = set(b[b.total_loans >= 30].isbn13)
    idx = set()
    for f in glob.glob(str(ROOT / "site" / "public" / "data" / "index" / "*.json")):
        idx |= {r[0] for r in json.load(open(f, encoding="utf-8"))["rows"]}
    return idx & site


def load_state() -> dict:
    return json.load(open(STATE_P, encoding="utf-8")) if STATE_P.exists() else {"libs": {}, "merged": []}


def merge_region(region: str, tgt: set[str], state: dict) -> None:
    """지역의 장서 목록 결과를 holdings.parquet 에 합친다."""
    dump = pd.read_parquet(DUMP_P) if DUMP_P.exists() else pd.DataFrame(columns=["isbn13", "region", "libCode"])
    d = dump[dump.region == region]
    h = pd.read_parquet(HOLD_P)
    keep_other = h[h.region != region]
    old_have = h[(h.region == region) & (h.libCode != "")]
    have = pd.concat([old_have, d], ignore_index=True).drop_duplicates()
    none = pd.DataFrame({"isbn13": sorted(tgt - set(have.isbn13)), "region": region, "libCode": ""})
    allh = pd.concat([keep_other, have, none], ignore_index=True)
    allh.to_parquet(HOLD_P, index=False)
    state["merged"].append(region)
    print(f"  [합침] 지역 {region}: 소장 {len(have):,}쌍 · 책 {have.isbn13.nunique():,}권 · 도서관 {have.libCode.nunique():,}곳 "
          f"(장서 목록 {len(d):,}쌍 + 기존 {len(old_have):,}쌍) · 소장 없음 {len(none):,}권", flush=True)


def run(regions: list[str], workers: int) -> None:
    tgt = targets()
    libs = pd.read_parquet(PROC / "libs.parquet")
    state = load_state()
    today = str(date.today())
    lock = threading.Lock()
    rows: list[dict] = []
    stat = {"calls": 0, "err": 0, "stop": False, "t0": time.time()}

    def save():
        nonlocal rows
        if rows:
            new = pd.DataFrame(rows, columns=["isbn13", "region", "libCode"])
            old = pd.read_parquet(DUMP_P) if DUMP_P.exists() else new.iloc[:0]
            pd.concat([old, new], ignore_index=True).drop_duplicates().to_parquet(DUMP_P, index=False)
            rows = []
        STATE_P.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def fetch(code: str, region: str, page: int) -> None:
        if stat["stop"]:
            return
        st = state["libs"][code]
        try:
            r = naru.call("itemSrch", cache=False, libCode=code, startDt="1900-01-01", endDt=today, pageNo=page, pageSize=PAGE)
        except Exception as e:  # noqa: BLE001
            with lock:
                if "outOfMaxlimit" in str(e):
                    if not stat["stop"]:
                        stat["stop"] = True
                        print(f"  일일 호출 한도 도달 → 중단 ({stat['calls']:,}회 완료)", flush=True)
                    return
                stat["err"] += 1
                st["err_now"] = True
                if stat["err"] <= 5:
                    print(f"  ! {code} p{page}: {str(e)[:100]}", flush=True)
            return
        resp = r.get("response", r)
        docs = resp.get("docs", []) or []
        with lock:
            stat["calls"] += 1
            if page == 1:
                nf = int(resp.get("numFound") or 0)
                st["numFound"] = nf
                st["pages"] = max(1, math.ceil(nf / PAGE))
            for x in docs:
                isbn = str(x.get("doc", x).get("isbn13", "")).strip()
                if isbn in tgt:
                    rows.append({"isbn13": isbn, "region": region, "libCode": code})
            st.setdefault("done_pages", [])
            if page not in st["done_pages"]:
                st["done_pages"].append(page)
            if len(st["done_pages"]) >= st.get("pages", 10 ** 9):
                st["done"] = True
            if stat["calls"] % SAVE_EVERY == 0:
                save()
                el = time.time() - stat["t0"]
                print(f"  {stat['calls']:,}회 · {el / 60:.0f}분 · 오류 {stat['err']}", flush=True)

    for region in regions:
        if region in state["merged"] or stat["stop"]:
            continue
        codes = [str(c) for c in libs[libs.region == region].libCode]
        for c in codes:
            st = state["libs"].setdefault(c, {"region": region})
            st.pop("err_now", None)
        todo = [c for c in codes if not state["libs"][c].get("done")]
        print(f"[지역 {region}] 도서관 {len(codes)}곳 중 남은 {len(todo)}곳", flush=True)
        # 1) 첫 페이지(장서 수 확인)
        first = [c for c in todo if 1 not in state["libs"][c].get("done_pages", [])]
        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(lambda c: fetch(c, region, 1), first))
        # 2) 나머지 페이지
        jobs = [(c, p) for c in todo for p in range(2, state["libs"][c].get("pages", 1) + 1)
                if p not in state["libs"][c].get("done_pages", [])]
        print(f"  남은 페이지 {len(jobs):,}", flush=True)
        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(lambda j: fetch(j[0], region, j[1]), jobs))
        # 오류 난 도서관: 회차 수 누적, 3회차 연속이면 오류로 넘김
        for c in codes:
            st = state["libs"][c]
            if st.pop("err_now", None) and not st.get("done"):
                st["err_runs"] = st.get("err_runs", 0) + 1
                if st["err_runs"] >= MAX_ERR_RUNS:
                    st["done"] = True
                    st["error"] = True
                    print(f"  ! 도서관 {c}: {MAX_ERR_RUNS}회차 연속 오류 → 건너뜀", flush=True)
        save()
        if all(state["libs"][c].get("done") for c in codes):
            merge_region(region, tgt, state)
            save()
        elif stat["stop"]:
            break
    save()
    print(f"회차 종료: 호출 {stat['calls']:,}회 · 오류 {stat['err']}{' · 한도로 중단' if stat['stop'] else ''} · 합친 지역 {state['merged']}", flush=True)


def left_calls(regions: list[str]) -> int:
    """남은 호출 추정(시작 안 한 도서관은 BookCount 로)."""
    libs = pd.read_parquet(PROC / "libs.parquet")
    state = load_state()
    n = 0
    for x in libs[libs.region.isin(regions)].itertuples():
        st = state["libs"].get(str(x.libCode), {})
        if str(x.region) in state["merged"] or st.get("done"):
            continue
        pages = st.get("pages", max(1, math.ceil(int(x.BookCount or 0) / PAGE)))
        n += pages - len(st.get("done_pages", []))
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--regions", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--left", action="store_true", help="남은 호출 추정만 출력")
    a = ap.parse_args()
    regs = [r.strip() for r in a.regions.split(",")]
    if a.left:
        print(left_calls(regs))
    else:
        run(regs, a.workers)
