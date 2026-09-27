"""소장 정보 수집 일일 점검(책 우선순위 × 14개 지역): 진행·인기 상위 N권 전국 완성도·오류·수집기 생존·예상 완료일.

실행: python scripts/holdings_status.py          # 점검 보고
      python scripts/holdings_status.py --left   # 남은 호출 수만(스크립트용)
"""
from __future__ import annotations
import math, re, subprocess, sys
from datetime import date, datetime, timedelta
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
LOG = PROC / "collect_nationwide.log"
REGIONS = ["26", "22", "37", "31", "23", "34", "36", "35", "33", "32", "24", "25", "39", "29"]
NAMES = {"22": "대구", "23": "인천", "24": "광주", "25": "대전", "26": "울산", "29": "세종", "31": "경기", "32": "강원",
         "33": "충북", "34": "충남", "35": "전북", "36": "전남", "37": "경북", "39": "제주"}
DAILY = 30000


def main():
    from collect_holdings import target_isbns
    order = target_isbns("index")                       # 수집기와 같은 우선순위(추천도 높은 순)
    h = pd.read_parquet(PROC / "holdings.parquet", columns=["isbn13", "region"])
    h = h[h.region.isin(REGIONS)]
    done = set(zip(h.isbn13, h.region))
    left = sum(1 for i in order for r in REGIONS if (i, r) not in done)
    if "--left" in sys.argv:
        print(left)
        return
    total = len(order) * len(REGIONS)
    # 앞에서부터 14개 지역이 모두 채워진 책 수 = 전국 완성된 인기 도서
    full = 0
    for i in order:
        if all((i, r) in done for r in REGIONS):
            full += 1
        else:
            break
    mtime = datetime.fromtimestamp((PROC / "holdings.parquet").stat().st_mtime)
    print(f"[점검 {datetime.now():%Y-%m-%d %H:%M}] 진행 {total - left:,}/{total:,}회 ({100 * (total - left) / total:.1f}%) · 남은 {left:,}회 · holdings 저장 {mtime:%m-%d %H:%M}")
    print(f"  인기 상위 {full:,}권까지 14개 지역 모두 수집됨 (전체 {len(order):,}권)")
    per = {r: sum(1 for i in order if (i, r) in done) for r in REGIONS}
    print("  지역별 책 수: " + " · ".join(f"{NAMES[r]} {per[r]:,}" for r in REGIONS))
    try:
        ps = subprocess.run(["powershell", "-NoProfile", "-Command",
                             "@(Get-CimInstance Win32_Process | ? { $_.CommandLine -match 'resume_nationwide' -and $_.CommandLine -notmatch 'Get-CimInstance' }).Count"],
                            capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception:  # noqa: BLE001
        ps = "?"
    print(f"  대기 스크립트: {ps}개 {'(정상)' if ps not in ('0', '?') else '(!! 멈춤 — 다시 띄워야 함)'}")
    if LOG.exists():
        lines = LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
        runs = [l for l in lines if l.startswith("소장 정보:")]
        prog = [l.strip() for l in lines if re.match(r"\s+[\d,]+/[\d,]+회", l)]
        errs = [l.strip() for l in lines if l.startswith("  ! ")]
        print("  최근 회차: " + (runs[-1][:150] if runs else "아직 없음"))
        if prog:
            print("  최근 진행: " + prog[-1][:150])
        if errs:
            print(f"  오류 표본({len(errs)}건 중 최근): " + errs[-1][:150])
        print("  로그 끝: " + (lines[-1][:150] if lines else ""))
    days = math.ceil(left / DAILY)
    print(f"  예상 완료: {(date.today() + timedelta(days=days)):%m-%d} (하루 {DAILY:,}회 기준 약 {days}일)")


if __name__ == "__main__":
    main()
