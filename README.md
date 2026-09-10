# AgeShelf — 한 살 단위로 보는 우리 아이 도서관 인기 책

도서관정보나루(data4library.kr) 공공도서관 대출 데이터를 0~13세 **한 살 단위**로 나누어,
각 나이의 독자가 상대적으로 더 많이 고른 어린이책을 찾아 주는 정적 사이트입니다.

- 사이트: https://boboseong.github.io/AgeShelf/
- 설계·결정 이력: [PLAN.md](PLAN.md)

## 구성
- `scripts/` 수집(정보나루 Open API) → 지표 → 태그 → 사이트 데이터 내보내기
- `site/` Astro 정적 사이트 (`npm run build` → `site/dist`)
- `data/lists/` 나이별 추천 목록 CSV

## 실행
```powershell
# .env 에 DATA4LIBRARY_KEY=<정보나루 인증키>
pwsh ./run_pipeline.ps1          # 수집(캐시) → 지표 → 태그 → 소장 → 내보내기 → 빌드
```
배포는 `main` 브랜치 푸시 시 GitHub Actions 가 `site/` 를 빌드해 GitHub Pages 로 올립니다.
