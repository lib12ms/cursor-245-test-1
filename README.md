# ISBN -> KORMARC 자동 생성기

ISBN을 입력하면 **알라딘 API**를 조회해서  
KORMARC `245  00`, `700`, `900  10` 필드를 자동 생성하는 도구입니다.

## 출력 규칙

- `245  00`
  - 서명: `$a`
  - 부제: `:$b`
  - 권차/편차기호: `.$n` (부제 뒤)
  - 첫 번째 저자: `/$d`
  - 두 번째 저자부터: `,$e`
  - 번역자/그린이/디자이너 등: `;$e`
- `700  1`
  - 개인명은 `700  1$a이름`
  - 협의회/기관/위원회 등 단체명은 `700  0$a이름`
  - 역할어 `$e`는 생성하지 않음
- 외국인 원저자 처리
  - 알라딘 API에 원어명이 없으면 저자 개요 페이지를 추가 조회해 원어명을 보완
  - 원어명이 확인되면 `700  1$a성, 이름`으로 도치 출력
    - 예: `Rami Kaminski` -> `Kaminski, Rami`
  - 같은 저자의 한글 표기는 `900  10$a성, 이름`으로 도치 출력
    - 예: `라미 카민스키` -> `카민스키, 라미`

예시:

```text
245  00$a서명:$b부제.$n1권/$d첫저자,$e둘째저자;$e번역자;$e그린이
700  1$a첫저자
700  1$a둘째저자
700  1$a번역자
700  0$aOO협의회
700  1$aKaminski, Rami
900  10$a카민스키, 라미
```

## 사전 준비 (알라딘 API)

기본 내장 TTBKey: `ttbboyeong09010919001`

필요하면 환경변수 `ALADIN_TTB_KEY`로 덮어쓸 수 있습니다.

### PowerShell 설정 예시

```powershell
$env:ALADIN_TTB_KEY = "내_알라딘_TTBKey"
```

## 실행 방법

### 1) CLI

```powershell
python komarc_from_isbn.py 9788998139766
```

또는 인자 없이 실행 후 ISBN 입력:

```powershell
python komarc_from_isbn.py
```

> 기본 소스는 `aladin`입니다.
> 실행 시 `komarc_<ISBN>.md` 파일을 자동으로 생성합니다.

### 2) Streamlit 웹

설치:

```powershell
pip install -r requirements.txt
```

실행:

```powershell
python -m streamlit run app.py
```

## 생성 파일

- 텍스트 출력: `komarc_<ISBN>.txt` (Streamlit 다운로드)
- 마크다운 출력: `komarc_<ISBN>.md` (CLI 자동 생성 + Streamlit 다운로드)

## 파일 구성

- `komarc_from_isbn.py`: ISBN 조회 + 245/700/900 생성 + Markdown 생성
- `app.py`: Streamlit UI
- `requirements.txt`: 의존성
