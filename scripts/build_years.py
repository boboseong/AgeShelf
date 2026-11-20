"""연도별·연령별 대출순 → data/processed/year_ranks.parquet  (PLAN.md 10절)

실행: python scripts/collect.py --years 2022-2025      # loans_long_years.parquet (연도 × 0~13세 무분할, 나이당 상위 5,000행)
      python scripts/build_years.py

규칙 (지금 '많이 빌린 책' 탭과 같은 기준을 한 해 자료에 적용)
  합산     (연도, 나이) 안에서 ISBN 별 합산 (vol 표기로 쪼개진 행)
  잡음     0세 제외. 1~3세는 '명백한 초등 책' 게이트 + 그 해 8~13세 분포 비례 차감 (build_metrics.denoise).
           게이트는 전 기간 자료(books_metrics)로 정한 것을 쓴다 — 한 해 자료는 나이당 5,000행뿐이라 다시 정하면 불안정.
           전 기간 자료에 없는 책만 그 해 자료로 판정.
           + 1~3세에서 한 나이에만 튀는 초등 책(단체대출성 급등)은 그 해 자료로 따로 뺀다 (burst_mask).
             그 해 자료만으로 판정이 안 되면 다른 해 자료를 보고, 그 칸 말고 어디에도 기록이 없는 책도 뺀다.
  수록     그 해 그 나이 대출 MIN_YEAR_LOANS(30)건 이상. 30건은 모든 나이에서 컷오프(5,000번째 행)의 2배 이상이거나
           1,000위 안쪽이라, vol 쪼개짐으로 합계가 덜 잡힐 위험이 없는 구간이다.
  순위     대출 많은 순. 사이트 수록 도서(총 대출 30건 이상) 중 전 기간 연령 적합도 POP_CENT_MIN(20%) 이상이고
           같은 책 다른 판 중 맨 앞 한 권 → rank 1…RANK_MAX. 그 사이에 낀 '다른 나이 책'·다른 판은 rank 없이 order 만 갖는다
           (사이트에서 '타연령 책도 보기'를 켜거나 도서관 필터로 다른 판이 남을 때 쓰임).
  변동     prev_rank = 같은 책(판 묶음)의 전년 rank. 전년 자료가 없는 첫 해는 변동 없이 순위만 있다.
  이력     rank_{연도} = 같은 책(판 묶음)의 그 나이·그 해 rank (순위 밖이면 빈 값). 순위가 있는 행에만 채운다.
           해마다 맨 앞에 서는 판(ISBN)이 달라도 한 책으로 이어진다.

출력 열: year, age, isbn13, order(그 해 그 나이 대출순 위치), loan(잡음 제거 후), loan_raw, rank, prev_rank, cent, in_site,
         work(판 묶음 키), rank_{연도}…
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_metrics import DENOISE_AGES, DROP_AGES, denoise, gate_mask  # noqa: E402
from groups import build_groups  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"

MIN_YEAR_LOANS = 30     # 그 해 그 나이 대출 하한 (사용자 결정 2026-11-19)
RANK_MAX = 1000         # 나이·연도마다 순위를 매기는 최대 권수
POP_CENT_MIN = 0.2      # '많이 빌린 책' 기본 화면이 숨기는 '다른 나이 책' 문턱 (사이트·export_lists 와 같은 값)
SITE_MIN_TOTAL = 30     # export_site.SITE_MIN_TOTAL 과 같은 값 (사이트 수록 하한)
ISOLATED_RATIO = 3.0    # burst_mask: 인접 나이 대비 1인당 선택률 배수
OTHERS_MEDIAN_MIN = 7   # burst_mask: 그 나이를 뺀 나머지 분포의 중앙 나이


def year_matrix(long: pd.DataFrame) -> tuple[pd.DataFrame, list[int]]:
    """한 해 자료 → isbn × 나이 대출 행렬 (없는 칸 0)."""
    L = long.groupby(["isbn13", "age"])["loan_count"].sum().unstack("age").fillna(0.0)
    ages = sorted(L.columns)
    return L.reindex(columns=ages), ages


def burst_mask(L: pd.DataFrame, ages: list[int], cutoff: dict[int, float],
               pool_med: pd.Series | None = None, lone: pd.Series | None = None) -> pd.DataFrame:
    """1~3세의 '한 나이에서만 튀는 초등 책' (isbn × DENOISE_AGES, bool).

    한 해 자료는 0~2세 상위권이 100건 안팎이라, 초등 학급 한 곳이 어린 나이로 등록된 카드로 한 책을 빌리면 그대로 1위가 된다
    (2023년 1세 '깊은 밤 필통 안에서' 222건 — 같은 해 2세 12건·8세 807건, 2024년 2세 '햇빛초 대나무 숲에…' 163건).
    이런 책은 전 기간 분포까지 그 급등에 끌려가 연령 적합도로는 걸러지지 않는다. 그 해 자료 안에서 두 조건을 모두 만족하면 뺀다.
      고립   그 나이의 1인당 선택률(대출 ÷ 그 나이 총대출)이 인접 나이(±1세) 중 큰 쪽의 ISOLATED_RATIO 배 이상.
             인접 나이에 관측되지 않은 책은 그 나이 컷오프(5,000번째 행의 대출)만큼 빌린 것으로 쳐서 보수적으로 잰다.
      초등   그 나이를 뺀 나머지 나이의 1인당 선택률 분포 중앙이 OTHERS_MEDIAN_MIN(7)세 이상.
    아기 책은 1세→2세로 선택률이 3~9배 떨어져 '고립'에는 걸리지만 나머지 분포가 2~4세라 남고, 알사탕처럼 넓게 읽히는 책은
    인접 나이와 이어져 있어 남는다.

    '초등' 판정의 빈틈 세 가지를 메운다 (2016~2025년 자료를 넣으며 발견, PLAN 10-10):
      a. 나머지 분포에서 **다른 고립 칸은 뺀다**. 한 해에 두 나이에서 튄 책(2025년 '탄소 중립이 뭐예요?' 1세 51건·3세 165건,
         2세 0건)은 서로가 서로의 '나머지'가 되어 중앙이 1~3세로 나왔다.
      b. 그 해 4세 이상 관측이 하나도 없으면 **여러 해를 합친 4세 이상 분포**(pool_med)로 판정한다. 그 해엔 초등 나이 대출이
         컷오프 밑이라 안 보이는 책(2024년 2세 '변신돼지' 옛 판 85건, 2016년 3세 'Why? 인공 지능' 139건).
      c. **이 칸 말고는 어디에도 기록이 없는 책**(lone: 모든 해·모든 나이·전 기간 자료 통틀어 이 칸뿐)은 판정할 근거가 없으니
         고립만으로 뺀다 (2016년 2세 1위였던 '바이오 로봇 미미' 217건, 같은 해 2세 75~77건 전집 묶음 20권).
    정상 연도에는 a~c 로 더 빠지는 책이 한 해 0~5권이다."""
    T = L.sum()
    W = L / T
    cols = [a for a in DENOISE_AGES if a in ages]
    iso = pd.DataFrame(False, index=L.index, columns=cols)
    for a in cols:
        adj = [x for x in (a - 1, a + 1) if x in ages]
        adj_rate = pd.concat([L[x].where(L[x] > 0, float(cutoff.get(x, 0))) / T[x] for x in adj], axis=1).max(axis=1)
        ratio = W[a] / adj_rate.replace(0, np.nan)
        iso[a] = (L[a] > 0) & (ratio.fillna(np.inf) >= ISOLATED_RATIO)
    has_older = (L[[x for x in ages if x not in DENOISE_AGES]] > 0).any(axis=1)
    out = pd.DataFrame(False, index=L.index, columns=cols)
    for a in cols:
        Wo = W.drop(columns=[a]).copy()
        for b in cols:
            if b != a:
                Wo[b] = Wo[b].where(~iso[b], 0.0)
        tot = Wo.sum(axis=1)
        C = Wo.cumsum(axis=1).div(tot.replace(0, np.nan), axis=0)
        med = (C >= 0.5).idxmax(axis=1).where(tot > 0)
        elem = med >= OTHERS_MEDIAN_MIN
        if pool_med is not None:
            elem = elem | (~has_older & (pool_med.reindex(L.index) >= OTHERS_MEDIAN_MIN))
        if lone is not None:
            elem = elem | lone.reindex(L.index).fillna(False).astype(bool)
        out[a] = iso[a] & elem
    return out


def pooled_older_median(long: pd.DataFrame) -> pd.Series:
    """여러 해를 합친 4세 이상(잡음 제거 대상이 아닌 나이) 1인당 선택률 분포의 중앙 나이. 4세 이상 관측이 없는 책은 없다(NaN)."""
    P = long[~long.age.isin(DENOISE_AGES + DROP_AGES)].groupby(["isbn13", "age"])["loan_count"].sum().unstack("age").fillna(0.0)
    P = P.reindex(columns=sorted(P.columns))
    PW = P / P.sum()
    C = PW.cumsum(axis=1).div(PW.sum(axis=1).replace(0, np.nan), axis=0)
    return (C >= 0.5).idxmax(axis=1).where(PW.sum(axis=1) > 0)


def lone_cells(long: pd.DataFrame, bm: pd.DataFrame) -> pd.Series:
    """연도별 자료 전체에서 (연도, 나이) 한 칸에만 나오고, 전 기간 자료에서도 그 나이 말고는 대출이 없는 책 (isbn → True)."""
    obs = long[long.loan_count > 0].assign(cell=lambda d: d.year * 100 + d.age).groupby("isbn13").agg(n=("cell", "nunique"), age=("age", "first"))
    one = obs[obs.n == 1]
    at = bm.reindex(one.index)[[c for c in bm.columns if c.startswith("loan_")]].fillna(0.0)
    own = pd.Series([float(at.at[i, f"loan_{a}"]) if f"loan_{a}" in at.columns else 0.0 for i, a in zip(one.index, one.age)], index=one.index)
    return ((at.sum(axis=1) - own) <= 0).loc[lambda s: s]


def alltime_gate(bm: pd.DataFrame) -> pd.Series:
    """전 기간 자료의 '명백한 초등 책'. books_metrics 의 4세 이상 loan_a 는 잡음 제거 전 값 그대로라 build_metrics 와 같은 집합이 나온다."""
    ages = sorted(int(c[5:]) for c in bm.columns if c.startswith("loan_"))
    L = bm[[f"loan_{a}" for a in ages]].astype(float)
    L.columns = ages
    return gate_mask(L, ages)


def prep_long(long: pd.DataFrame) -> pd.DataFrame:
    """loans_long_years 원자료 → age·loan_count 숫자형, year 열 추가."""
    long = long.copy()
    long["age"] = long["age"].astype(int)
    long["loan_count"] = pd.to_numeric(long["loan_count"], errors="coerce").fillna(0)
    long["year"] = long["slice"].str[1:].astype(int)
    return long


def year_cutoffs(cells: pd.DataFrame | None, year: int) -> dict[int, float]:
    """그 해 나이별 컷오프(5,000번째 행의 대출). 상한에 안 걸린 칸은 0."""
    if cells is None:
        return {}
    cy = cells[cells["slice"] == f"y{year}"]
    return {int(r.age): float(r.cutoff) if bool(r.capped) else 0.0 for r in cy.itertuples()}


def clean_years(long: pd.DataFrame, bm: pd.DataFrame, cells: pd.DataFrame | None = None, verbose: bool = True,
                extra: pd.DataFrame | None = None):
    """연도마다 (year, ages, L, Lc) 를 낸다. long 은 prep_long 을 거친 것.
    L 은 isbn × 나이 원자료, Lc 는 잡음 제거(게이트 + 비례 차감)와 급등 제외까지 끝낸 값(소수, 수록 하한 적용 전).
    연도별 대출순(build)과 도서관당 대출(build_perlib.py)이 같은 값을 쓰도록 한 곳에서 계산한다.

    extra(분야별 보충 수집, 열 year·age·isbn13·loan_count — (연도, 나이, 책)당 한 행)를 주면 무분할 자료에 안 보이던 칸을
    채운 값을 낸다(도서관당 대출용). 잡음 모형(게이트·비례 계수·급등 판정)은 **무분할 자료로만** 정하고 그 결과를 채운 값에
    그대로 적용한다 — 보충은 1~7세만 있어 섞어서 판정하면 8~13세와 관측 깊이가 달라지고, 무분할에 보이던 칸의 값이
    대출순 목록과 달라지기 때문이다. 무분할과 보충에 다 있는 칸은 큰 쪽(보충 쪽이 vol 로 쪼개진 행을 더 많이 담는다)."""
    gate_all = alltime_gate(bm)
    pool_med = pooled_older_median(long)
    lone = lone_cells(long, bm)
    for year, ly in long.groupby("year"):
        L, ages = year_matrix(ly)
        ages = [a for a in ages if a not in DROP_AGES]
        L = L[ages]
        M = None
        ey = extra[extra.year == year] if extra is not None else None
        if ey is not None and len(ey):
            E = year_matrix(ey)[0].reindex(columns=ages).fillna(0.0)
            idx = L.index.union(E.index)
            L = L.reindex(idx).fillna(0.0)          # 보충에만 있는 책은 0 행으로 — 아래 판정에는 영향이 없다
            M = np.maximum(L, E.reindex(idx).fillna(0.0))
        known = L.index.isin(gate_all.index)
        gate = gate_all.reindex(L.index).fillna(False).astype(bool) | (gate_mask(L, ages) & ~known)
        Lc, Mc, f = denoise(L, L.copy() if M is None else M, ages, verbose=False, gate=gate)
        burst = burst_mask(L, ages, year_cutoffs(cells, year), pool_med, lone)
        names = ly.drop_duplicates("isbn13").set_index("isbn13")["bookname"] if "bookname" in ly.columns else pd.Series(dtype=str)
        if verbose:
            print(f"[{year}] 도서 {len(L):,}권 · 게이트 {int(gate.sum()):,}권(전 기간 자료에 없어 그 해 자료로 판정 {int((gate & ~known).sum()):,}) · "
                  + "비례 계수 " + ", ".join(f"{a}세 {v:.0%}" for a, v in f.items()))
        for a in burst.columns:
            hit = burst.index[burst[a] & ~gate & (Lc[a] >= MIN_YEAR_LOANS)]
            if verbose and len(hit):
                print(f"   {a}세 급등 제외 {len(hit)}권: " + ", ".join(f"{str(names.get(i, i)).strip()[:16]} {int(L.at[i, a])}건"
                                                                   for i in L.loc[hit, a].sort_values(ascending=False).index[:8]))
            Lc.loc[burst[a], a] = 0.0
            if M is not None:
                Mc.loc[burst[a], a] = 0.0
        if M is None:
            yield int(year), ages, L, Lc
        else:
            yield int(year), ages, M, Mc


def build(long: pd.DataFrame, bm: pd.DataFrame, cent: pd.Series, wk: dict, cells: pd.DataFrame | None = None,
          verbose: bool = True) -> pd.DataFrame:
    long = prep_long(long)
    in_site = set(bm.index[bm.total_loans >= SITE_MIN_TOTAL])
    out = []
    for year, ages, L, Lc in clean_years(long, bm, cells, verbose):
        for a in ages:
            d = pd.DataFrame({"isbn13": L.index, "loan": Lc[a].round(0).astype(int).values, "loan_raw": L[a].astype(int).values})
            d = d[d.loan >= MIN_YEAR_LOANS].sort_values(["loan", "loan_raw", "isbn13"], ascending=[False, False, True]).reset_index(drop=True)
            # 사이트 색인이 적합도를 소수 둘째 자리로 싣고 그 값으로 20% 문턱을 보므로 여기서도 같은 값으로 판정한다
            d["cent"] = pd.Series(cent.reindex(pd.MultiIndex.from_arrays([[a] * len(d), d.isbn13])).values, index=d.index).round(2)
            d["in_site"] = d.isbn13.isin(in_site)
            work = d.isbn13.map(lambda i: wk.get(i, i))
            ok = d.in_site & (d.cent >= POP_CENT_MIN) & ~work.where(d.in_site & (d.cent >= POP_CENT_MIN)).duplicated()
            rank = ok.cumsum().where(ok)
            last = int(rank[rank <= RANK_MAX].index.max()) if (rank <= RANK_MAX).any() else -1
            d["rank"] = rank
            d["work"] = work
            d = d.iloc[: last + 1]
            d.insert(0, "order", range(1, len(d) + 1))
            d.insert(0, "age", a)
            d.insert(0, "year", year)
            out.append(d)
    res = pd.concat(out, ignore_index=True)
    # 전년 순위: 같은 책(판 묶음) 기준 — 해마다 맨 앞에 서는 판이 달라도 이어지게
    prev = res[res["rank"].notna()][["year", "age", "work", "rank"]].rename(columns={"rank": "prev_rank"})
    prev["year"] = prev["year"] + 1
    res = res.merge(prev, on=["year", "age", "work"], how="left")
    res.loc[res["rank"].isna(), "prev_rank"] = np.nan
    # 연도별 순위 이력: (나이, 판 묶음) × 연도. 한 해 한 나이에 순위를 갖는 판은 묶음당 하나뿐이라 겹치지 않는다
    hist = res[res["rank"].notna()].pivot(index=["age", "work"], columns="year", values="rank")
    hist.columns = [f"rank_{int(y)}" for y in hist.columns]
    res = res.merge(hist.reset_index(), on=["age", "work"], how="left")
    res.loc[res["rank"].isna(), list(hist.columns)] = np.nan
    return res


def hist_years(yr: pd.DataFrame) -> list[int]:
    """year_ranks 의 이력 열(rank_{연도})이 있는 연도, 오름차순."""
    return sorted(int(c[5:]) for c in yr.columns if c.startswith("rank_") and c[5:].isdigit())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=str(PROC / "loans_long_years.parquet"))
    args = ap.parse_args()
    long = pd.read_parquet(args.inp)
    bm = pd.read_parquet(PROC / "books_metrics.parquet").set_index("isbn13", drop=False)
    ar = pd.read_parquet(PROC / "age_ranks.parquet", columns=["age", "isbn13", "cent"])
    cent = ar.set_index(["age", "isbn13"])["cent"]
    tags_p = PROC / "book_tags.parquet"
    gb = bm.join(pd.read_parquet(tags_p).set_index("isbn13")[["form", "publisher_norm"]]) if tags_p.exists() else bm
    wk = build_groups(gb).wk.to_dict()

    cells_p = Path(args.inp).with_name(Path(args.inp).name.replace("loans_long", "cells"))
    res = build(long, bm, cent, wk, pd.read_parquet(cells_p) if cells_p.exists() else None)
    res.to_parquet(PROC / "year_ranks.parquet", index=False)
    years = sorted(res.year.unique())
    print(f"\n저장: year_ranks.parquet  {len(res):,}행 · 연도 {years[0]}~{years[-1]} · 나이 {res.age.min()}~{res.age.max()}세")

    ranked = res[res["rank"].notna()]
    print("\n순위 매긴 권수 (연도 × 나이)")
    print(ranked.groupby(["year", "age"]).size().unstack("age").fillna(0).astype(int).to_string())
    print("\n30건 이상인데 순위 밖: 사이트 수록 도서가 아님(전 기간 자료에 없음·총 대출 30건 미만) — 연도 × 나이")
    print(res[~res.in_site].groupby(["year", "age"]).size().unstack("age").fillna(0).astype(int).to_string())
    print("\n30건 이상인데 순위 밖: 다른 나이 책(전 기간 연령 적합도 20% 미만) — 연도 × 나이")
    print(res[res.in_site & (res.cent < POP_CENT_MIN)].groupby(["year", "age"]).size().unstack("age").fillna(0).astype(int).to_string())
    for a in (1, 2, 4):
        for y in years[-1:]:
            top = ranked[(ranked.year == y) & (ranked.age == a)].nsmallest(10, "rank")
            print(f"\n[{y}년 {a}세 TOP10]  순위 / 대출 / 전년 순위 / 제목")
            for r in top.itertuples():
                pv = f"{int(r.prev_rank):>4}" if pd.notna(r.prev_rank) else " new"
                print(f"  {int(r.rank):>3}  {r.loan:>5}  {pv}  {str(bm.bookname.get(r.isbn13, ''))[:40]}")


if __name__ == "__main__":
    main()
