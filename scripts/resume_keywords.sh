#!/usr/bin/env bash
# 정보나루 일일 한도에 맞춰 키워드 수집(사이트 전체)을 이어받는다. 30분마다 한도 확인 → 수집 → 남은 대상 0이면 종료.
cd "$(dirname "$0")/.." || exit 1
MAX_WAIT="${1:-144}"          # 30분 × 144 = 72시간
LOG="data/processed/collect_keywords_site.log"
: > "$LOG"
for i in $(seq 1 "$MAX_WAIT"); do
  if PYTHONIOENCODING=utf-8 python -c "
import sys; sys.path.insert(0,'scripts'); import naru
naru.call('keywordList', cache=False, isbn13='9788936446819', additionalYN='N')
" 2>/dev/null; then
    echo "[$(date '+%m-%d %H:%M')] 한도 회복 → 수집 시작" >> "$LOG"
    PYTHONIOENCODING=utf-8 python scripts/collect_keywords.py --books site --workers 3 >> "$LOG" 2>&1
    LEFT=$(PYTHONIOENCODING=utf-8 python -c "
import pandas as pd
b = pd.read_parquet('data/processed/books_metrics.parquet'); site = set(b[b.total_loans>=30].isbn13)
k = pd.read_parquet('data/processed/keywords.parquet'); print(len(site - set(k.isbn13)))" 2>/dev/null)
    echo "[$(date '+%m-%d %H:%M')] 이번 회차 종료 · 남은 대상 ${LEFT}권" >> "$LOG"
    [ "${LEFT:-1}" = "0" ] && { echo "완료" >> "$LOG"; exit 0; }
  else
    echo "[$(date '+%m-%d %H:%M')] 한도 제한 대기 ($i/$MAX_WAIT)" >> "$LOG"
  fi
  sleep 1800
done
echo "대기 한도 초과" >> "$LOG"; exit 1
