# 전체 파이프라인: 수집(캐시 재사용) → 지표 → 사이트 데이터 → 목록 CSV → 정적 빌드
# 사용: pwsh ./run_pipeline.ps1 [-SkipCollect]
param([switch]$SkipCollect)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not $SkipCollect) {
  python scripts/collect.py --slices allonly            # 전국 무분할, 0~13세, 아동 도서 (14회)
  python scripts/collect.py --slices allonly --ages 1-8 --tag kdc --cells "kdc=0,kdc=1,kdc=2,kdc=3,kdc=4,kdc=5,kdc=6,kdc=7,kdc=8,kdc=9,dtl_kdc=81,dtl_kdc=84,dtl_kdc=37,dtl_kdc=83,dtl_kdc=40,dtl_kdc=80,dtl_kdc=86,dtl_kdc=91,dtl_kdc=41,dtl_kdc=85,dtl_kdc=71,dtl_kdc=49,dtl_kdc=90,dtl_kdc=32,dtl_kdc=51,dtl_kdc=99"   # 분야별 보충: 1~8세 결측 채우기 (208회, 합집합)
  python scripts/collect.py --slices last12m --tag recent     # 최근 12개월 (요즘 인기 태그용, 14회)
}
python scripts/build_metrics.py --slice all   # loans_long + loans_long_kdc 자동 병합
python scripts/tags.py                        # 태그(세부 라벨·형태·출판사·성격·요즘 인기)
python scripts/collect_holdings.py --regions 21 --books index --workers 4   # 부산: 색인 전체 도서 소장(재개 가능, ~4시간)
python scripts/collect_holdings.py --regions 38 --books core --skip-directory  # 경남: 핵심 도서만
python scripts/collect_keywords.py --top 3000 --workers 3   # 책소개 키워드 (1~7세 추천도 상위 3,000권 합집합, 재개 가능)
python scripts/topics.py                                     # 주제 27개 + 원문 키워드 → book_topics.parquet
python scripts/export_site.py
python scripts/export_lists.py
Push-Location site; npm run build; Pop-Location
Write-Host "done → site/dist"
