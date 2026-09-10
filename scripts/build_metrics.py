"""도서×연령 행렬 → 연령 분포·중심도·집중도·점수 → data/processed/books_metrics.parquet / csv

실행: python scripts/build_metrics.py [--slice all] [--in loans_long.parquet --in loans_long_kdc.parquet]

v3 점수 설계 (PLAN.md 2-8)
  정규화   W[b,a] = L[b,a] / T[a]^γ  (γ=1: lift 곡선과 같은 모양. 0.75가 통념 권장연령과 근소하게 더 맞지만 정의의 명료성을 택함)
  스무딩   P[b,a] = (W + κ·prior_form) / (ΣW + κ)  — 같은 발행형태(그림책/전집/단행본) 평균 분포 쪽으로 κ=150건만큼
  중심도   c[b,a] = 1 - 2|F_mid(a) - 0.5|  (F_mid: 나이 a 의 구간 중앙 누적비율). 중앙이면 1, 꼬리면 0.
           비대칭: 독자 대부분이 더 어린 '지난 책'(F>0.5)은 그대로, 더 큰 '앞으로 볼 책'(F<0.5)은 완만하게(지수 1.5)
  집중도   s[b,a] = min(3, A·P[b,a])  — 13개 나이에 고르면 1. 점수에는 넣지 않고 배지로만 사용
  자격     c ≥ 0.3 이고 그 나이 대출 ≥ 30건
  점수     fit[b,a] = W_CENT·c[b,a] + W_POP·log(L[b,a])/log(Lmax_a)  (0~100점, Lmax_a = 그 나이 후보 최대 대출)  ← v3.1 2026-09-10
  밴드     P 의 25~75% 분위 구간, 중앙 나이 = 50% 분위(보간)
  lift     참고값으로 유지 (비관적: 1~8세는 상한 U, 9세 이상은 관측값)

오염 처리 (PLAN.md 2-4, 2-6): 0세 제외, 1~3세 배경 제거·초등 게이트, 결측 상한 U.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"

SHRINK_M = 30        # lift 가상 표본(대출 건)
DROP_AGES = (0,)                 # 지표에서 제외할 코호트 (0세: 기관 단체대출 오염)
DENOISE_AGES = (1, 2, 3)         # 배경(초등 독자 오등록) 제거를 적용할 나이
NOISE_REF = (8, 9, 10, 11, 12, 13)  # 잡음 모형: 초등 코호트의 도서 분포
GATE_OLDER_SHARE = 0.85          # 4~13세 대출 중 8~13세 비중이 이 이상이면 '명백한 초등 책'
BOUND_AGES = (1, 2, 3, 4, 5, 6, 7, 8)  # 상한 U 를 lift 에 반영하는 나이 (분야별 보충 수집 범위)

# v3
GAMMA = 1.0            # lift 기준(코호트 총량으로 완전 정규화). 사용자 결정 2026-09-10
SMOOTH_K = 150.0
CENT_MIN = 0.3         # 위치 필터 문턱. 사용자 결정 2026-09-10
MIN_LOANS_AT_AGE = 30
SPEC_CAP = 3.0
BAND_Q = (0.25, 0.75)   # 밴드 = 중앙 50% 질량 (통념 권장연령과 가장 근접)
EARLY_POWER = 1.5
W_CENT, W_POP = 50.0, 50.0   # 추천도 100점 배분 (위치 : 인기). 사용자 결정 2026-09-10


def kcell_of(class_no) -> str:
    s = str(class_no or "").strip()
    if not s or not s[0].isdigit():
        return "?"
    if s[0] == "8" and len(s) > 1 and s[1].isdigit():
        return f"dtl_kdc={s[:2]}"
    return f"kdc={s[0]}"


def bounds(long: pd.DataFrame, cells: pd.DataFrame | None):
    """관측 L 과 상한 U (isbn × age) 를 만든다."""
    long = long.copy()
    long["age"] = long["age"].astype(int)
    long["loan_count"] = pd.to_numeric(long["loan_count"], errors="coerce").fillna(0)
    for c in ("region", "gender", "kcell"):
        if c not in long.columns:
            long[c] = "*"
    # 1) 한 쿼리(셀) 안의 같은 ISBN 여러 행(vol 표기 차이) → 합산
    per_query = long.groupby(["isbn13", "age", "region", "gender", "kcell"])["loan_count"].sum()
    # 2) KDC 셀/무분할 쿼리는 같은 값의 재관측 → max (합집합)
    obs_add = per_query.groupby(level=["isbn13", "age", "region", "gender"]).max()
    # 3) 지역·성별은 대출자 속성 → 합산
    L = obs_add.groupby(level=["isbn13", "age"]).sum().unstack("age")
    ages = sorted(L.columns)
    L = L.reindex(columns=ages)
    observed = L.notna()
    L = L.fillna(0.0)

    U = L.copy()
    if cells is None or cells.empty:
        cells = pd.DataFrame(columns=["age", "region", "gender", "kcell", "capped", "cutoff"])
    cells = cells.copy()
    cells["age"] = cells["age"].astype(int)
    for c in ("region", "gender", "kcell"):
        if c not in cells.columns:
            cells[c] = "*"
    book_kcell = (long.sort_values("loan_count", ascending=False).drop_duplicates("isbn13")
                  .set_index("isbn13")["class_no"].map(kcell_of))
    seen = obs_add.reset_index().pivot_table(index="isbn13", columns=["age", "region", "gender"],
                                             values="loan_count", aggfunc="max")
    book_k1 = book_kcell.map(lambda k: "kdc=" + k.split("=")[1][0] if k.startswith("dtl_kdc=") else k)

    for (a, r, g), ca in cells.groupby(["age", "region", "gender"]):
        if a not in ages:
            continue
        capped = ca[ca.capped.astype(bool)]
        if capped.empty:          # 이 가산 셀은 모든 쿼리가 완전 관측 → 상한 = 관측값
            continue
        # 쿼리별 상한: 캡됐으면 cutoff, 완전 관측이면 0 (그 쿼리에 없음 = 0건 확정)
        q_bound = {row.kcell: (float(row.cutoff) if bool(row.capped) else 0.0) for row in ca.itertuples()}
        fallback = float(capped["cutoff"].max())
        if list(q_bound) == ["*"]:
            bound = pd.Series(q_bound["*"], index=L.index)
        else:
            # 책마다 가장 좁은 쿼리부터: 세부주제 셀 → 대주제 셀 → 무분할 → (없으면) 최대 cutoff
            def pick(isbn):
                for key in (book_kcell.get(isbn), book_k1.get(isbn), "*"):
                    if key in q_bound:
                        return q_bound[key]
                return fallback
            bound = pd.Series([pick(i) for i in L.index], index=L.index, dtype=float)
        if (a, r, g) in seen.columns:
            missing = seen[(a, r, g)].reindex(L.index).isna()
        else:
            missing = pd.Series(True, index=L.index)
        U[a] = U[a] + bound.where(missing, 0.0)
    return L, U, observed, ages


def denoise(L: pd.DataFrame, U: pd.DataFrame, ages: list[int], verbose: bool = True):
    """1~3세 코호트에 섞인 초등 독자(생년 오등록·기관 대출) 배경을 뺀다."""
    ref = [a for a in NOISE_REF if a in ages]
    if not ref:
        return L, U, {}
    N = L[ref].sum(axis=1)
    p_noise = N / N.sum()
    mid = [a for a in ages if 4 <= a <= 7]
    older_share = N / (N + L[mid].sum(axis=1) + 1e-9)
    seen_older = N[N > 0]
    gated = (older_share > GATE_OLDER_SHARE) & (N > (seen_older.quantile(0.5) if len(seen_older) else 0))
    Lc, Uc, f = L.copy(), U.copy(), {}
    for a in DENOISE_AGES:
        if a not in ages:
            continue
        T_a = L[a].sum()
        denom = T_a * p_noise[gated].sum()
        f_a = float(L.loc[gated, a].sum() / denom) if denom > 0 else 0.0
        f_a = min(f_a, 1.0)
        f[a] = f_a
        noise = f_a * T_a * p_noise
        Lc[a] = (L[a] - noise).clip(lower=0.0)
        Uc[a] = (U[a] - noise).clip(lower=0.0)
        Lc.loc[gated, a] = 0.0
        Uc.loc[gated, a] = 0.0
        Uc[a] = Uc[a].where(Uc[a] >= Lc[a], Lc[a])
    if verbose:
        removed = {a: 1 - Lc[a].sum() / max(L[a].sum(), 1) for a in f}
        print("배경 제거: 게이트(명백한 초등 책) " + f"{int(gated.sum()):,}권, "
              + "비례 계수 f_a " + ", ".join(f"{a}세 {v:.0%}" for a, v in f.items())
              + " → 코호트 대출 제거 비율 " + ", ".join(f"{a}세 {v:.0%}" for a, v in removed.items()))
    return Lc, Uc, f


def quantile_age(P: np.ndarray, ages: list[int], q: float, interpolate: bool) -> np.ndarray:
    """행별 q 분위 나이. interpolate=True 면 구간(a-0.5~a+0.5) 안에서 선형 보간."""
    C = np.cumsum(P, axis=1)
    idx = np.argmax(C >= q - 1e-12, axis=1)
    if not interpolate:
        return np.array(ages)[idx].astype(float)
    prev = np.where(idx > 0, np.take_along_axis(C, np.maximum(idx - 1, 0)[:, None], axis=1)[:, 0], 0.0)
    p_i = np.take_along_axis(P, idx[:, None], axis=1)[:, 0]
    return np.array(ages)[idx] - 0.5 + (q - prev) / np.maximum(p_i, 1e-12)


def build(long: pd.DataFrame, cells: pd.DataFrame | None = None, shrink_m: float = SHRINK_M,
          do_denoise: bool = True):
    L, U, observed, ages = bounds(long, cells)
    drop = [a for a in DROP_AGES if a in ages]
    if drop:
        L, U, observed = L.drop(columns=drop), U.drop(columns=drop), observed.drop(columns=drop)
        ages = [a for a in ages if a not in drop]
    if do_denoise:
        L, U, _ = denoise(L, U, ages)
    keep = L.sum(axis=1) > 0
    if (~keep).any():
        print(f"관측 0 도서 제외: {int((~keep).sum()):,}권")
        L, U, observed = L[keep], U[keep], observed[keep]

    meta_cols = ["bookname", "authors", "publisher", "publication_year", "addition_symbol",
                 "vol", "class_no", "class_nm", "bookImageURL", "bookDtlUrl"]
    meta = (long.sort_values("loan_count", ascending=False).drop_duplicates("isbn13")
            .set_index("isbn13")[meta_cols]).reindex(L.index)

    T = L.sum(axis=0)
    prior_age = T / T.sum()
    A = len(ages)
    age_arr = np.array(ages, dtype=float)

    # ---- v3: 정규화 + 발행형태 사전분포 스무딩 ----
    W = L / (T ** GAMMA)
    Wtot = W.sum(axis=1)
    P_raw = W.div(Wtot.replace(0, np.nan), axis=0).fillna(0.0)
    form = meta["addition_symbol"].fillna("").astype(str).str[1:2].replace("", "x")
    prior_form = P_raw.groupby(form.values).mean()
    PRI = prior_form.reindex(form.values)
    PRI.index = L.index
    scale = Wtot / L.sum(axis=1).clip(lower=1)                 # 대출 건수 → W 스케일 환산
    kappa = SMOOTH_K * scale
    P = (W + PRI.mul(kappa, axis=0)).div(Wtot + kappa, axis=0)
    profile = P

    # ---- 중심도(비대칭) / 집중도 / 점수 ----
    Pn = P.to_numpy(float)
    F_mid = np.cumsum(Pn, axis=1) - Pn / 2
    cent = 1 - 2 * np.abs(F_mid - 0.5)
    early = 1 - np.clip(2 * (0.5 - F_mid), 0, 1) ** EARLY_POWER
    cent = np.where(F_mid < 0.5, early, cent)
    cent = pd.DataFrame(np.clip(cent, 0, 1), index=L.index, columns=ages)
    spec = (A * P).clip(upper=SPEC_CAP)
    # 추천도(0~100): 위치 점수 + 인기 점수(log 대출을 그 나이 후보 최대 대비 정규화)
    elig_all = (cent >= CENT_MIN) & (L >= MIN_LOANS_AT_AGE)
    lmax = pd.Series({a: float(L[a][elig_all[a]].max()) if elig_all[a].any() else float(L[a].max()) for a in ages})
    pop = pd.DataFrame({a: (np.log(L[a].clip(lower=1)) / np.log(max(lmax[a], 2.0))).clip(0, 1) for a in ages})
    popscore = W_POP * pop
    fit = W_CENT * cent + popscore

    # ---- lift (참고값, 비관적) ----
    U_eff = U.copy()
    for a in ages:
        if a not in BOUND_AGES:
            U_eff[a] = L[a]
    lift = pd.DataFrame(index=L.index, columns=ages, dtype=float)
    for a in ages:
        total_other = U_eff.drop(columns=[a]).sum(axis=1) + L[a]
        La = L[a] + shrink_m * prior_age[a]
        p_b_given_a = La / (T[a] + shrink_m)
        p_b = (total_other + shrink_m) / (T.sum() + shrink_m * len(ages))
        lift[a] = p_b_given_a / p_b

    # ---- 분포 요약 ----
    mean_age = (profile * age_arr).sum(axis=1)
    dev = age_arr[None, :] - mean_age.values[:, None]
    spread = np.sqrt((profile * dev ** 2).sum(axis=1))
    skew = ((profile * dev ** 3).sum(axis=1) / (spread ** 3).replace(0, np.nan)).fillna(0)
    peak = profile.idxmax(axis=1).astype(int)
    median_age = quantile_age(Pn, ages, 0.5, interpolate=True)
    q_lo = quantile_age(Pn, ages, BAND_Q[0], interpolate=False).astype(int)
    q_hi = quantile_age(Pn, ages, BAND_Q[1], interpolate=False).astype(int)
    band = pd.Series([f"{lo}세" if lo == hi else f"{lo}~{hi}세" for lo, hi in zip(q_lo, q_hi)], index=L.index)

    def shape_of(row: pd.Series) -> str:
        p = int(row.idxmax())
        below = row[[a for a in ages if a < p]].sum()
        above = row[[a for a in ages if a > p]].sum()
        if above > 1.5 * max(below, 1e-9):
            return "더 큰 아이 쪽으로 넓음"
        if below > 1.5 * max(above, 1e-9):
            return "더 어린 아이부터 봄"
        return "대칭"

    books = meta.join(pd.DataFrame({
        "total_loans": L.sum(axis=1).round(0).astype(int),
        "total_upper": U_eff.sum(axis=1).round(0).astype(int),
        "observed_ages": observed.sum(axis=1),
        "peak_age": peak,
        "median_age": np.round(median_age, 2),
        "mean_age": mean_age.round(2),
        "spread": spread.round(2),
        "skew": skew.round(2),
        "band": band,
        "band_lo": q_lo,
        "band_hi": q_hi,
        "shape": profile.apply(shape_of, axis=1),
        "max_lift": lift.max(axis=1).round(2),
        "max_spec": spec.max(axis=1).round(2),
        "is_kids": meta["addition_symbol"].fillna("").str.startswith("7"),
        "is_picture": meta["addition_symbol"].fillna("").str[1:2].eq("7"),
    }))
    for a in ages:
        books[f"loan_{a}"] = L[a].round(0).astype(int)
        books[f"upper_{a}"] = U_eff[a].round(0).astype(int)
        books[f"prof_{a}"] = profile[a].round(4)
        books[f"cent_{a}"] = cent[a].round(3)
        books[f"spec_{a}"] = spec[a].round(3)
        books[f"lift_{a}"] = lift[a].round(3)
        books[f"fit_{a}"] = fit[a].round(1)
        books[f"pop_{a}"] = popscore[a].round(1)
        books[f"obs_{a}"] = observed[a]

    ranks = []
    for a in ages:
        pop_rank = L[a].where(observed[a]).rank(ascending=False, method="first")
        eligible = observed[a] & (cent[a] >= CENT_MIN) & (L[a] >= MIN_LOANS_AT_AGE)
        fit_rank = fit[a].where(eligible).rank(ascending=False, method="first")
        ranks.append(pd.DataFrame({
            "age": a, "isbn13": L.index,
            "loan_count": L[a].round(0).astype(int).values,
            "observed": observed[a].values,
            "pop_rank": pop_rank.values,
            "cent": cent[a].round(3).values,
            "spec": spec[a].round(3).values,
            "lift": lift[a].round(3).values,
            "fit_score": fit[a].round(1).values,
            "pop_score": popscore[a].round(1).values,
            "fit_rank": fit_rank.values,
        }))
    return books.reset_index(), pd.concat(ranks, ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", action="append", default=None,
                    help="loans_long*.parquet (여러 번 지정하면 합집합으로 병합, 기본: loans_long.parquet + loans_long_kdc.parquet)")
    ap.add_argument("--slice", default="all")
    ap.add_argument("--shrink", type=float, default=SHRINK_M)
    ap.add_argument("--no-denoise", action="store_true")
    args = ap.parse_args()
    inputs = [Path(p) for p in args.inp] if args.inp else \
             [p for p in (PROC / "loans_long.parquet", PROC / "loans_long_kdc.parquet") if p.exists()]
    longs, cell_frames = [], []
    for inp in inputs:
        df = pd.read_parquet(inp)
        for c in ("region", "gender", "kcell"):
            if c not in df.columns:
                df[c] = "*"
        longs.append(df)
        cells_p = inp.with_name(inp.name.replace("loans_long", "cells"))
        if cells_p.exists():
            cf = pd.read_parquet(cells_p)
            for c in ("region", "gender", "kcell"):
                if c not in cf.columns:
                    cf[c] = "*"
            cell_frames.append(cf)
        print(f"입력: {inp.name}  {len(df):,} rows")
    long = pd.concat(longs, ignore_index=True)
    cells = pd.concat(cell_frames, ignore_index=True) if cell_frames else None
    long = long[long["slice"] == args.slice]
    if cells is not None:
        cells = cells[cells["slice"] == args.slice]
    if long.empty:
        sys.exit(f"slice={args.slice} 데이터가 없습니다.")
    books, ranks = build(long, cells, args.shrink, do_denoise=not args.no_denoise)

    tag = "" if args.slice == "all" else f"_{args.slice}"
    books.to_parquet(PROC / f"books_metrics{tag}.parquet", index=False)
    books.to_csv(PROC / f"books_metrics{tag}.csv", index=False, encoding="utf-8-sig")
    ranks.to_parquet(PROC / f"age_ranks{tag}.parquet", index=False)
    print(f"books: {len(books):,}  ranks: {len(ranks):,}  → {PROC}")
    print("\n중앙 나이 분포:\n" + books["median_age"].round(0).astype(int).value_counts().sort_index().to_string())
    for a in (2, 4):
        top = ranks[(ranks.age == a) & ranks.fit_rank.notna()].nsmallest(10, "fit_rank")
        print(f"\n[{a}세 적합도 TOP10]  대출 / 중심도 / 집중도 / 밴드")
        for _, r in top.merge(books[["isbn13", "bookname", "band"]], on="isbn13").iterrows():
            print(f"  {int(r.loan_count):>7}  c {r.cent:4.2f}  추천도 {r.fit_score:5.1f}  {r.band:>7}  {r.bookname[:36]}")


if __name__ == "__main__":
    main()
