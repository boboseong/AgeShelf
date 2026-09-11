#!/usr/bin/env bash
# 정보나루 일일 호출 한도가 풀리면 부산 소장 정보 수집을 자동으로 이어받는다.
# 30분마다 시험 호출 → 성공하면 수집 실행 → 완료 후 종료. (최대 24시간 대기)
cd "$(dirname "$0")/.." || exit 1
LOG=data/processed/collect_holdings_busan_resume.log
: > "$LOG"
for i in $(seq 1 48); do
  if PYTHONIOENCODING=utf-8 python -c "
import sys; sys.path.insert(0,'scripts'); import naru
naru.call('libSrchByBook', cache=False, isbn='9788936446819', region=21, pageSize=10)
" 2>/dev/null; then
    echo \"[$(date '+%m-%d %H:%M')] 한도 회복 확인 → 수집 시작\" >> "$LOG"
    PYTHONIOENCODING=utf-8 python scripts/collect_holdings.py --regions 21 --books index --workers 4 --skip-directory >> "$LOG" 2>&1
    tail -2 "$LOG"
    exit 0
  fi
  echo "[$(date '+%m-%d %H:%M')] 아직 한도 제한 (시도 $i/48)" >> "$LOG"
  sleep 1800
done
echo "24시간 대기 후에도 한도가 풀리지 않음" >> "$LOG"
exit 1
