# stock-closes

미국 주식 종가 자동 수집. 시세만 저장하고 계좌·수량 정보는 없음.

- 평일 미국 장 마감 뒤(21:10·21:40·22:05 UTC) GitHub Actions가 `tickers.txt`의 종목을 받아 `data/`에 저장
- `data/latest.csv`: 종가·전일 대비·50/200일선·1/3/12개월 수익률·52주 고가 (`final=0`이면 장중 값)
- `data/closes_recent.csv`: 최근 30거래일 종가
- `data/meta.json`: 갱신 시각·기준 거래일·빠진 종목

데이터: Yahoo Finance(yfinance), 실패하면 Stooq. 정확성 보증 없음.
