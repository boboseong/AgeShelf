"""실데이터 지표 점검: 알려진 책의 연령 프로필, 나이별 적합/인기 TOP, 컷오프·관측 통계.

실행: python scripts/inspect_metrics.py [--metrics data/processed/books_metrics.parquet] [--age 2]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"

KNOWN = ["달님 안녕", "사과가 쿵", "두드려 보아요", "괜찮아", "누가 내 머리에 똥 쌌어", "구름빵",
         "수박 수영장", "당근 유치원", "흔한남매", "긴긴밤", "변기에 쉬해요", "안돼, 데이빗", "곰 사냥을 떠나자",
         "배고픈 애벌레", "엄마 마중", "코를 킁킁"]


def bar(vals, width=12):
    m = max(vals) or 1
    return "".join("▁▂▃▄▅▆▇█"[min(7, int(v / m * 7.999))] for v in vals)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", default=str(PROC / "books_metrics.parquet"))
    ap.add_argument("--ranks", default=None)
    ap.add_argument("--age", type=int, default=2)
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
    books = pd.read_parquet(a.metrics)
    ranks_p = Path(a.ranks) if a.ranks else Path(a.metrics).with_name(Path(a.metrics).name.replace("books_metrics", "age_ranks"))
    ranks = pd.read_parquet(ranks_p)
    ages = sorted(int(c[5:]) for c in books.columns if c.startswith("prof_"))
    loan_cols = [f"loan_{x}" for x in ages]
    obs_cols = [f"obs_{x}" for x in ages]
    prof_cols = [f"prof_{x}" for x in ages]

    print(f"도서 {len(books):,}권, 연령 {ages[0]}~{ages[-1]}세")
    T = books[loan_cols].sum()
    print("\n[코호트 총량(관측)]")
    print("  " + "  ".join(f"{x}세 {int(T[f'loan_{x}']):,}" for x in ages))
    obs_rate = books[obs_cols].mean()
    print("[나이별 관측률(책 기준)]  " + "  ".join(f"{x}세 {obs_rate[f'obs_{x}']:.0%}" for x in ages))
    print(f"[관측 연령 수 분포] " + books["observed_ages"].value_counts().sort_index().to_dict().__str__())
    print(f"[peak_age 분포] " + books["peak_age"].value_counts().sort_index().to_dict().__str__())

    print("\n[알려진 책의 프로필]  (막대: 나이별 비중, 소문자 x = 미관측 상한 표시)")
    for name in KNOWN:
        hit = books[books.bookname.str.contains(name, regex=False, na=False)].sort_values("total_loans", ascending=False)
        if hit.empty:
            print(f"  {name:<14} (없음)"); continue
        b = hit.iloc[0]
        prof = [b[c] for c in prof_cols]
        marks = "".join("." if b[f"obs_{x}"] else "x" for x in ages)
        print(f"  {b.bookname[:22]:<24} 총 {int(b.total_loans):>8,}  peak {int(b.peak_age)}세  밴드 {b.band:<7} skew {b["skew"]:+.2f} {b['shape']:<12} "
              f"{bar(prof)} {marks}  lift@{a.age} {b[f'lift_{a.age}']:.2f}")

    tgt = a.age
    r = ranks[ranks.age == tgt].merge(books[["isbn13", "bookname", "publisher", "band", "shape", "peak_age", "observed_ages", "is_picture"]], on="isbn13")
    print(f"\n[{tgt}세 적합도(fit) TOP {a.top}]  대출 / lift / 밴드 / 관측나이수")
    for _, x in r[r.fit_rank.notna()].nsmallest(a.top, "fit_rank").iterrows():
        print(f"  {int(x.loan_count):>7} {x.lift:5.2f}  {x.band:<7} {x['shape']:<12} obs{int(x.observed_ages):>2} {'그림' if x.is_picture else '    '} {x.bookname[:34]} / {x.publisher[:10] if x.publisher else ''}")
    print(f"\n[{tgt}세 인기(pop) TOP {a.top}]  (lift<1 은 다른 나이 책이 섞인 것)")
    for _, x in r[r.observed].nsmallest(a.top, "pop_rank").iterrows():
        flag = "  ← 타연령" if x.lift < 0.8 else ""
        print(f"  {int(x.loan_count):>7} {x.lift:5.2f}  {x.band:<7} peak{int(x.peak_age):>2} {x.bookname[:34]}{flag}")


if __name__ == "__main__":
    main()
