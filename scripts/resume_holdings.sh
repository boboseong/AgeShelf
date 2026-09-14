#!/usr/bin/env bash
# 정보나루 일일 호출 한도가 풀릴 때까지 기다렸다가 소장 정보 수집을 이어받는다.
# 사용: bash scripts/resume_holdings.sh <지역코드> [최대 대기 횟수]   예) bash scripts/resume_holdings.sh 11
# 30분마다 시험 호출 → 성공하면 수집 실행 → 한도에 다시 걸리면 또 대기. 대상이 다 차면 종료.
cd "$(dirname "$0")/.." || exit 1
REGION="${1:-21}"
MAX_WAIT="${2:-96}"          # 30분 × 96 = 48시간
LOG="data/processed/collect_holdings_${REGION}.log"
: > "$LOG"
for i in $(seq 1 "$MAX_WAIT"); do
  if PYTHONIOENCODING=utf-8 python -c "
import sys; sys.path.insert(0,'scripts'); import naru
naru.call('libSrchByBook', cache=False, isbn='9788936446819', region=$REGION, pageSize=10)
" 2>/dev/null; then
    echo "[$(date '+%m-%d %H:%M')] 한도 회복 → 수집 시작" >> "$LOG"
    PYTHONIOENCODING=utf-8 python scripts/collect_holdings.py --regions "$REGION" --books index --workers 4 --skip-directory >> "$LOG" 2>&1
    # 남은 대상이 없으면 종료, 있으면 (한도 소진) 다시 대기
    LEFT=$(PYTHONIOENCODING=utf-8 python -c "
import pandas as pd, json, glob
h = pd.read_parquet('data/processed/holdings.parquet')
b = pd.read_parquet('data/processed/books_metrics.parquet')
site = set(b[b.total_loans>=30].isbn13)
idx = set()
for f in glob.glob('site/public/data/index/*.json'):
    idx |= {r[0] for r in json.load(open(f, encoding='utf-8'))['rows']}
print(len((idx & site) - set(h[h.region=='$REGION'].isbn13)))
" 2>/dev/null)
    echo "[$(date '+%m-%d %H:%M')] 이번 회차 종료 · 남은 대상 ${LEFT}권" >> "$LOG"
    [ "${LEFT:-1}" = "0" ] && { echo "완료" >> "$LOG"; exit 0; }
  else
    echo "[$(date '+%m-%d %H:%M')] 한도 제한 대기 ($i/$MAX_WAIT)" >> "$LOG"
  fi
  sleep 1800
done
echo "대기 한도 초과" >> "$LOG"
exit 1
