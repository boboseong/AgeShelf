#!/usr/bin/env bash
# 나머지 14개 지역 소장 정보를 책 우선순위대로 이어 받는다(2026-09-27 시작, 하루 3만 회 기준 약 28일).
#   책을 추천도 높은 순으로 놓고, 한 권마다 14개 지역을 모두 조회 → 인기 도서부터 전국이 먼저 채워진다.
#   (장서 목록 itemSrch 방식은 호출이 1/4 이지만 울산 도서관 목록이 장서 일부만 줘서 폐기, PLAN 9-2)
# 30분마다 한도 확인 → 되면 수집(한도에 걸리면 collect_holdings 가 즉시 중단) → 다시 대기. 이어받기 가능.
# 사용: "C:\Program Files\Git\bin\bash.exe" scripts/resume_nationwide.sh   (PowerShell 의 bash 는 WSL 이라 안 됨)
cd "$(dirname "$0")/.." || exit 1
REGIONS="26,22,37,31,23,34,36,35,33,32,24,25,39,29"
MAX_WAIT="${1:-2400}"        # 30분 × 2400 = 50일
LOG="data/processed/collect_nationwide.log"
echo "[$(date '+%m-%d %H:%M')] 시작 · 책 우선순위 × 지역 $REGIONS" >> "$LOG"
for i in $(seq 1 "$MAX_WAIT"); do
  if PYTHONIOENCODING=utf-8 python -c "
import sys; sys.path.insert(0,'scripts'); import naru
naru.call('libSrchByBook', cache=False, isbn='9788936446819', region=26, pageSize=10)" 2>/dev/null; then
    echo "[$(date '+%m-%d %H:%M')] 한도 회복 → 수집 시작" >> "$LOG"
    PYTHONIOENCODING=utf-8 python scripts/collect_holdings.py --regions "$REGIONS" --books index --workers 4 --skip-directory --book-major >> "$LOG" 2>&1
    LEFT=$(PYTHONIOENCODING=utf-8 python scripts/holdings_status.py --left 2>/dev/null)
    echo "[$(date '+%m-%d %H:%M')] 회차 종료 · 남은 호출 ${LEFT}" >> "$LOG"
    [ "${LEFT:-1}" = "0" ] && { echo "[$(date '+%m-%d %H:%M')] 전국 완료" >> "$LOG"; exit 0; }
  fi
  sleep 1800
done
echo "[$(date '+%m-%d %H:%M')] 대기 한도 초과" >> "$LOG"
exit 1
