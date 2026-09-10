"""합성 샘플 데이터 생성 (실데이터 전 파이프라인·사이트 검증용). 5,000건 컷오프도 흉내낸다.
실행: python scripts/make_sample.py [--cap 120]
"""
import argparse, sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent)); sys.stdout.reconfigure(encoding="utf-8")
from build_metrics import build

ROOT = Path(__file__).resolve().parents[1]; OUT = ROOT / "data" / "sample"
AGES = list(range(9)); COHORT = np.array([300, 900, 1600, 2000, 2200, 2000, 1500, 1200, 1000], float)
TITLES = ["달님 안녕", "사과가 쿵!", "두드려 보아요", "누가 내 머리에 똥 쌌어?", "구름빵", "괜찮아", "엄마 마중", "코를 킁킁",
          "동물원", "기차 ㄱㄴㄷ", "곰 사냥을 떠나자", "배고픈 애벌레", "우리 아빠가 최고야", "고구마구마", "치카치카 양치"]

def skewed_profile(rng, mode, sd, skew):
    x = np.array(AGES, float); z = (x - mode) / sd
    w = np.exp(-0.5 * z**2) * (1 + np.tanh(skew * z))          # 왜도 있는 종형
    return np.clip(w, 1e-6, None) / w.sum()

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--cap", type=int, default=120); ap.add_argument("--n", type=int, default=400)
    a = ap.parse_args(); rng = np.random.default_rng(7); rows = []
    truth = []
    for i in range(a.n):
        isbn = f"978890000{i:04d}"; mode = rng.uniform(0, 8); sd = rng.uniform(0.6, 3.0); sk = rng.uniform(-1.5, 1.5)
        base = int(np.exp(rng.uniform(np.log(200), np.log(30000))))
        w = skewed_profile(rng, mode, sd, sk)
        x = np.array(AGES, float); m = (w*x).sum(); v = (w*(x-m)**2).sum(); tsk = (w*(x-m)**3).sum()/v**1.5
        truth.append((isbn, int(np.argmax(w)), tsk))
        name = TITLES[i] if i < len(TITLES) else f"샘플 그림책 {i}"
        for age in AGES:
            cnt = int(base * w[age] * COHORT[age] / COHORT.mean())
            if cnt > 0:
                rows.append(dict(slice="all", age=age, region="*", gender="*", kcell="*", ranking=None, isbn13=isbn, bookname=name,
                                 authors="지은이: 홍길동", publisher="샘플출판", publication_year=str(rng.integers(2005, 2026)),
                                 addition_symbol="77810", vol=None, class_no="813.8", class_nm="문학 > 한국문학 > 동화",
                                 bookImageURL="", bookDtlUrl="", loan_count=cnt))
    full = pd.DataFrame(rows)
    # 컷오프 흉내: 나이별 상위 cap 권만 관측
    parts, cells = [], []
    for age, g in full.groupby("age"):
        g = g.sort_values("loan_count", ascending=False)
        top = g.head(a.cap).copy(); top["ranking"] = range(1, len(top) + 1)
        capped = len(g) > a.cap
        cells.append(dict(slice="all", age=str(age), region="*", gender="*", kcell="*", n=len(top), capped=capped,
                          cutoff=int(top.loan_count.iloc[-1]) if capped else 0))
        parts.append(top)
    long = pd.concat(parts); cells = pd.DataFrame(cells)
    OUT.mkdir(parents=True, exist_ok=True)
    long.to_parquet(OUT / "loans_long.parquet", index=False); cells.to_parquet(OUT / "cells.parquet", index=False)
    books, ranks = build(long, cells)
    books.to_parquet(OUT / "books_metrics.parquet", index=False); ranks.to_parquet(OUT / "age_ranks.parquet", index=False)

    # 검증: 관측이 충분한 책에서 peak/skew 방향이 진실과 맞는지
    t = pd.DataFrame(truth, columns=["isbn13", "true_peak", "true_skew"]).merge(books, on="isbn13")
    ok = t[t.observed_ages >= 5]
    print(f"books {len(books)} (관측 {a.cap}권/나이), 충분관측 {len(ok)}권")
    print(f"  peak == true_peak : {(ok.peak_age == ok.true_peak).mean():.0%},  |diff|<=1 : {(abs(ok.peak_age - ok.true_peak) <= 1).mean():.0%}")
    print(f"  skew 부호 일치(|true|>0.5): {(np.sign(ok[abs(ok.true_skew)>0.3]["skew"]) == np.sign(ok[abs(ok.true_skew)>0.3].true_skew)).mean():.0%}")
    print("  shape 분포:", ok["shape"].value_counts().to_dict())
    few = t[t.observed_ages <= 2]
    print(f"  관측 2개 이하 {len(few)}권의 max_lift 중앙값 {few.max_lift.median():.2f} (비관적 lift 로 억제되어야 함) vs 충분관측 {ok.max_lift.median():.2f}")
    print(ok.sort_values("skew").iloc[[0, -1]][["bookname", "peak_age", "mean_age", "skew", "band", "shape"]].to_string(index=False))

if __name__ == "__main__":
    main()
