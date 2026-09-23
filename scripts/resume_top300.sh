#!/usr/bin/env bash
# 정보나루 일일 한도가 풀리면 상위 300권 × 전국 17지역 소장 정보를 수집한다(이미 받은 (책,지역)은 건너뜀).
# 사용: bash scripts/resume_top300.sh   (30분마다 시험 호출, 최대 48시간 대기)
cd "$(dirname "$0")/.." || exit 1
LOG=data/processed/collect_holdings_top300.log
: > "$LOG"
for i in $(seq 1 96); do
  if PYTHONIOENCODING=utf-8 python -c "
import sys; sys.path.insert(0,'scripts'); import naru
naru.call('libSrchByBook', cache=False, isbn='9788936446819', region=22, pageSize=10)" 2>/dev/null; then
    echo "[$(date '+%m-%d %H:%M')] 한도 회복 → 수집 시작" >> "$LOG"
    PYTHONIOENCODING=utf-8 python scripts/collect_holdings.py --regions all --books index --limit 300 --workers 4 --skip-directory >> "$LOG" 2>&1
    echo "[$(date '+%m-%d %H:%M')] 수집 종료" >> "$LOG"
    exit 0
  fi
  echo "[$(date '+%m-%d %H:%M')] 한도 대기 $i" >> "$LOG"
  sleep 1800
done
