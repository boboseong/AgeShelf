"""도서 키워드 수집 (정보나루 keywordList, 책당 1회, 병렬, 재개 가능)

  대상: 1~7세 추천도 상위 N권 합집합 (--top N). 추천도 최고값이 높은 책부터.
  출력: data/processed/keywords.parquet (isbn13, word, weight). 키워드가 없는 책은 word="" 한 행으로 '조회 완료' 표시.
  이미 조회한 ISBN 은 건너뛰므로 재실행이 곧 재개.

실행: python scripts/collect_keywords.py --top 3000 --workers 3
"""
from __future__ import annotations

import argparse
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
OUT = PROC / "keywords.parquet"
SAVE_EVERY = 1000
SITE_AGES = range(1, 8)


def targets(top: int) -> list[str]:
    r = pd.read_parquet(PROC / "age_ranks.parquet")
    e = r[r.fit_rank.notna() & r.age.isin(list(SITE_AGES)) & (r.fit_rank <= top)]
    best = e.groupby("isbn13")["fit_score"].max().sort_values(ascending=False)
    return list(best.index)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=3000)
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    isbns = targets(args.top)
    old = pd.read_parquet(OUT) if OUT.exists() else pd.DataFrame(columns=["isbn13", "word", "weight"])
    done = set(old.isbn13)
    jobs = [i for i in isbns if i not in done]
    print(f"대상 {len(isbns):,}권 (상위 {args.top}) → 남은 호출 {len(jobs):,}회 (동시 {args.workers})", flush=True)

    rows: list[dict] = []
    lock = threading.Lock()
    state = {"n": 0, "err": 0, "empty": 0, "t0": time.time()}

    def save():
        new = pd.DataFrame(rows, columns=["isbn13", "word", "weight"])
        allk = pd.concat([old, new], ignore_index=True).drop_duplicates(["isbn13", "word"])
        allk.to_parquet(OUT, index=False)
        return allk

    def work(isbn):
        try:
            r = naru.call("keywordList", cache=True, isbn13=isbn, additionalYN="N")
        except naru.NaruError as e:
            with lock:
                state["err"] += 1
                if state["err"] <= 5:
                    print(f"  ! {isbn}: {e}", flush=True)
            return
        items = (r.get("response", r)).get("items", []) or []
        with lock:
            if items:
                for x in items:
                    it = x.get("item", x)
                    w = str(it.get("word", "")).strip()
                    if w:
                        rows.append({"isbn13": isbn, "word": w, "weight": int(it.get("weight", 0) or 0)})
            else:
                rows.append({"isbn13": isbn, "word": "", "weight": 0})
                state["empty"] += 1
            state["n"] += 1
            if state["n"] % SAVE_EVERY == 0:
                save()
                el = time.time() - state["t0"]
                left = (len(jobs) - state["n"]) * el / state["n"] / 60
                print(f"  {state['n']:,}/{len(jobs):,}권 · {el/60:.0f}분 경과 · 남은 {left:.0f}분 · 빈 응답 {state['empty']} · 오류 {state['err']}", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, jobs))
    allk = save()
    have = allk[allk.word != ""]
    print(f"키워드: {len(have):,}행, 책 {allk.isbn13.nunique():,}권(키워드 있음 {have.isbn13.nunique():,}권) → keywords.parquet "
          f"(이번 {state['n']:,}회, 빈 응답 {state['empty']}, 오류 {state['err']})")


if __name__ == "__main__":
    main()
