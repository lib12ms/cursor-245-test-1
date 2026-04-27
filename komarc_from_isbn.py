import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from argparse import ArgumentParser
from dataclasses import dataclass


OPENLIBRARY_API = "https://openlibrary.org/api/books"
ALADIN_API = "http://www.aladin.co.kr/ttb/api/ItemLookUp.aspx"
NL_SEOKJI_API = "https://www.nl.go.kr/seoji/SearchApi.do"
NL_SEOKJI_API_ALT = "https://seoji.nl.go.kr/landingPage/SearchApi.do"
DEFAULT_ALADIN_TTB_KEY = "ttbboyeong09010919001"


@dataclass
class BookInfo:
    isbn: str
    title: str = ""
    subtitle: str = ""
    part_number: str = ""
    authors: list[str] | None = None
    publish_places: list[str] | None = None
    publishers: list[str] | None = None
    publish_date: str = ""
    number_of_pages: int | None = None
    subjects: list[str] | None = None
    translators: list[str] | None = None
    illustrators: list[str] | None = None
    designers: list[str] | None = None
    foreign_author_originals: dict[str, str] | None = None


def normalize_isbn(raw: str) -> str:
    cleaned = re.sub(r"[^0-9Xx]", "", raw).upper()
    if len(cleaned) not in (10, 13):
        raise ValueError("ISBN 길이는 10자리 또는 13자리여야 합니다.")
    return cleaned


def _parse_korean_contributors(
    author_raw: str,
) -> tuple[list[str], list[str], list[str], list[str], dict[str, str]]:
    """저자 문자열을 역할별로 분리 (알라딘·국립중앙도서관 등 '이름 (역할)' 나열 형식)."""
    authors: list[str] = []
    translators: list[str] = []
    illustrators: list[str] = []
    designers: list[str] = []
    foreign_author_originals: dict[str, str] = {}
    if not author_raw.strip():
        return authors, translators, illustrators, designers, foreign_author_originals

    segments = [seg.strip() for seg in author_raw.split(",") if seg.strip()]
    for segment in segments:
        matched = re.match(r"^(.*?)\s*\((.*?)\)\s*$", segment)
        if matched:
            name = matched.group(1).strip()
            role = matched.group(2).strip()
        else:
            name = segment
            role = ""

        if not name:
            continue

        if re.search(r"번역|옮긴이|역자", role):
            translators.append(name)
        elif re.search(r"그림|그린이|삽화|일러스트", role):
            illustrators.append(name)
        elif re.search(r"디자인|디자이너", role):
            designers.append(name)
        elif role and re.search(r"[A-Za-z]", role) and not re.search(r"지은이|저자|글", role):
            # 예: "라미 카민스키 (Rami Kaminski)"
            authors.append(name)
            foreign_author_originals[name] = role
        else:
            authors.append(name)

    if not authors and not translators and not illustrators and not designers:
        authors = [name.strip() for name in re.split(r",|;|\\|&| and ", author_raw) if name.strip()]

    return authors, translators, illustrators, designers, foreign_author_originals


def _extract_part_number(title: str, subtitle: str) -> tuple[str, str, str]:
    """
    제목/부제에서 권차/편차기호를 추출해 245 $n으로 이동.
    예: 1권, 제2권, 3편, 상/중/하
    """
    marker_pattern = r"(제?\s*\d+\s*[권편판]|[0-9]+\s*[권편판]|개정판|증보판|합본판|상|중|하|초판|재판|에디션)"
    trailing_pattern = re.compile(rf"(?:\s*[-:.,]\s*)?({marker_pattern})\s*$")
    exact_pattern = re.compile(rf"^\s*({marker_pattern})\s*$")

    title_out = title.strip()
    subtitle_out = subtitle.strip()
    part_number = ""

    # 부제가 권차/편차만 있으면 우선 $n으로 사용
    if subtitle_out and exact_pattern.fullmatch(subtitle_out):
        part_number = subtitle_out
        subtitle_out = ""
        return title_out, subtitle_out, part_number

    # 제목 말미의 권차/판차/에디션 기호를 추출
    matched = trailing_pattern.search(title_out)
    if matched:
        part_number = matched.group(1).strip()
        title_out = title_out[: matched.start()].rstrip()
        return title_out, subtitle_out, part_number

    # 부제 말미의 권차/판차/에디션 기호를 추출
    matched = trailing_pattern.search(subtitle_out)
    if matched:
        part_number = matched.group(1).strip()
        subtitle_out = subtitle_out[: matched.start()].rstrip()

    # 제목 중간의 불필요한 판차/에디션 표기를 제거 (245$a 정리)
    inline_pattern = re.compile(rf"\s*\(\s*{marker_pattern}\s*\)")
    inline_match = inline_pattern.search(title_out)
    if inline_match and not part_number:
        part_number = re.sub(r"[()]", "", inline_match.group(0)).strip()
    title_out = inline_pattern.sub("", title_out).strip()

    return title_out, subtitle_out, part_number


def _split_title_subtitle(title_raw: str, subtitle_raw: str = "") -> tuple[str, str]:
    """서명/부제를 안정적으로 분리."""
    title = title_raw.strip()
    subtitle = subtitle_raw.strip()

    # API가 별도 부제를 주는 경우 최우선 사용
    if subtitle:
        return title, subtitle

    # 일반적인 구분자 기반 분리: " : ", ":", "：" 모두 허용
    # 예) "지층거주자 : 반지하로부터의 수기"
    colon_match = re.search(r"\s*[:：]\s*", title)
    if colon_match:
        left = title[: colon_match.start()].strip()
        right = title[colon_match.end() :].strip()
        if left and right:
            return left, right

    # 대시 기반 분리: "서명 - 부제" 형태
    dash_match = re.search(r"\s[-–—]\s", title)
    if dash_match:
        left = title[: dash_match.start()].strip()
        right = title[dash_match.end() :].strip()
        # 너무 짧은 꼬리말 분리는 피하고, 충분히 설명적인 경우만 부제로 간주
        if left and right and len(right) >= 8:
            return left, right

    # 부제가 누락된 경우를 위한 약한 휴리스틱:
    # 제목이 과도하게 길고 "... 위한 ..." 구조가 있으면 부제로 분리
    if len(title) >= 24:
        matched = re.search(r"\s+(?=[^ ]+\s+위한\s+)", title)
        if matched:
            left = title[: matched.start()].strip()
            right = title[matched.start() :].strip()
            if left and right:
                return left, right

    return title, ""


def _extract_original_names_from_aladin_page(link: str, names: list[str]) -> dict[str, str]:
    """
    알라딘 상품 페이지의 '저자 및 역자소개' 본문에서
    '한글명(영문명)' 패턴을 찾아 원어명을 추출.
    """
    if not link or not names:
        return {}

    req = urllib.request.Request(
        link,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except urllib.error.URLError:
        return {}

    result: dict[str, str] = {}
    target_names = [name for name in names if not _is_corporate_name(name)]

    # 1차: 상품 페이지 본문에서 직접 추출
    for name in target_names:
        pattern = re.compile(rf"{re.escape(name)}\s*\(\s*([A-Za-z][A-Za-z .,'-]+)\s*\)", re.IGNORECASE)
        match = pattern.search(html)
        if match:
            result[name] = match.group(1).strip()

    if len(result) == len(target_names):
        return result

    # 2차: 저자 개요 페이지(wauthor_overview)에서 추가 추출
    author_search_values = re.findall(r"AuthorSearch=([^\"'&\\s]+)", html, flags=re.IGNORECASE)
    author_search_values = list(dict.fromkeys(author_search_values))

    for value in author_search_values:
        author_url = f"https://www.aladin.co.kr/author/wauthor_overview.aspx?AuthorSearch={value}"
        req = urllib.request.Request(
            author_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                author_html = response.read().decode("utf-8", errors="ignore")
        except urllib.error.URLError:
            continue

        # 예: "벡 에반스(Bec Evans)", "크리스 스미스(Chris Smith)"
        pairs = re.findall(r"([가-힣][가-힣\s\.\-]{0,40})\s*\(\s*([A-Za-z][A-Za-z .,'-]{1,80})\s*\)", author_html)
        for korean_name, original_name in pairs:
            kn = korean_name.strip()
            on = original_name.strip()
            if kn in target_names and kn not in result:
                result[kn] = on
    return result


def fetch_book_info_openlibrary(isbn: str) -> BookInfo:
    bib_key = f"ISBN:{isbn}"
    query = urllib.parse.urlencode(
        {
            "bibkeys": bib_key,
            "jscmd": "data",
            "format": "json",
        }
    )
    url = f"{OPENLIBRARY_API}?{query}"

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise ConnectionError(f"서지정보 조회 실패: {exc}") from exc

    if bib_key not in payload:
        raise LookupError("해당 ISBN에 대한 데이터를 찾지 못했습니다.")

    raw = payload[bib_key]

    authors = [a.get("name", "").strip() for a in raw.get("authors", []) if a.get("name")]
    places = [p.get("name", "").strip() for p in raw.get("publish_places", []) if p.get("name")]
    publishers = [p.get("name", "").strip() for p in raw.get("publishers", []) if p.get("name")]
    subjects = [s.get("name", "").strip() for s in raw.get("subjects", []) if s.get("name")]

    title, subtitle = _split_title_subtitle(
        (raw.get("title") or "").strip(),
        (raw.get("subtitle") or "").strip(),
    )

    return BookInfo(
        isbn=isbn,
        title=title,
        subtitle=subtitle,
        authors=authors or None,
        publish_places=places or None,
        publishers=publishers or None,
        publish_date=(raw.get("publish_date") or "").strip(),
        number_of_pages=raw.get("number_of_pages"),
        subjects=subjects or None,
    )


def fetch_book_info_aladin(isbn: str, ttb_key: str) -> BookInfo:
    query = urllib.parse.urlencode(
        {
            "ttbkey": ttb_key,
            "itemIdType": "ISBN13" if len(isbn) == 13 else "ISBN",
            "ItemId": isbn,
            "output": "js",
            "Version": "20131101",
            "OptResult": "ebookList,usedList,reviewList",
        }
    )
    url = f"{ALADIN_API}?{query}"

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            body = response.read()
    except urllib.error.URLError as exc:
        raise ConnectionError(f"알라딘 조회 실패: {exc}") from exc

    payload: dict
    try:
        payload = json.loads(body.decode("utf-8"))
    except UnicodeDecodeError:
        payload = json.loads(body.decode("cp949"))

    if payload.get("errorCode"):
        code = payload.get("errorCode")
        message = str(payload.get("errorMessage", "")).strip() or "알 수 없는 오류"
        raise LookupError(f"알라딘 API 오류({code}): {message}")

    items = payload.get("item") or []
    if not items:
        raise LookupError("알라딘에서 해당 ISBN 데이터를 찾지 못했습니다.")

    raw = items[0]

    author_raw = (raw.get("author") or "").strip()
    authors, translators, illustrators, designers, foreign_author_originals = _parse_korean_contributors(author_raw)
    if authors:
        page_link = (raw.get("link") or "").strip()
        fallback_originals = _extract_original_names_from_aladin_page(page_link, authors)
        if fallback_originals:
            foreign_author_originals.update(fallback_originals)

    title, subtitle = _split_title_subtitle(
        (raw.get("title") or "").strip(),
        (raw.get("subTitle") or "").strip(),
    )
    title, subtitle, part_number = _extract_part_number(title, subtitle)

    publisher = (raw.get("publisher") or "").strip()
    pubdate = (raw.get("pubDate") or "").strip()
    if len(pubdate) == 8 and pubdate.isdigit():
        pubdate = f"{pubdate[0:4]}-{pubdate[4:6]}-{pubdate[6:8]}"

    category = (raw.get("categoryName") or "").strip()

    return BookInfo(
        isbn=isbn,
        title=title,
        subtitle=subtitle,
        part_number=part_number,
        authors=authors or None,
        publish_places=None,
        publishers=[publisher] if publisher else None,
        publish_date=pubdate,
        number_of_pages=None,
        subjects=[category] if category else None,
        translators=translators or None,
        illustrators=illustrators or None,
        designers=designers or None,
        foreign_author_originals=foreign_author_originals or None,
    )


def _nl_seoji_extract_first_record(payload: dict) -> dict | None:
    """국립중앙도서관 ISBN 서지 API JSON에서 첫 레코드만 추출."""
    result = str(payload.get("RESULT", "")).upper()
    if result == "ERROR":
        raise LookupError(payload.get("ERR_MESSAGE", "국립중앙도서관 API 오류"))

    if "response" in payload:
        body = payload.get("response", {}).get("body", {})
        items = body.get("items")
        if isinstance(items, list):
            return items[0] if items and isinstance(items[0], dict) else None
        if isinstance(items, dict):
            item = items.get("item")
            if isinstance(item, list):
                return item[0] if item else None
            if isinstance(item, dict):
                return item
        return None

    for key in ("docs", "doc", "data"):
        block = payload.get(key)
        if isinstance(block, list) and block:
            first = block[0]
            return first if isinstance(first, dict) else None
        if isinstance(block, dict) and (block.get("TITLE") or block.get("EA_ISBN")):
            return block

    if payload.get("TITLE") or payload.get("EA_ISBN"):
        return payload

    if int(str(payload.get("TOTAL_COUNT", "0") or "0").strip() or "0") == 0:
        return None

    return None


def _nl_seoji_request(isbn: str, cert_key: str, base_url: str) -> dict:
    query = urllib.parse.urlencode(
        {
            "cert_key": cert_key,
            "result_style": "json",
            "page_no": 1,
            "page_size": 10,
            "isbn": isbn,
        }
    )
    url = f"{base_url}?{query}"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise ConnectionError(f"국립중앙도서관 서지 API 조회 실패: {exc}") from exc


def _bookinfo_from_nl_raw(raw: dict, isbn: str) -> BookInfo:
    title, subtitle = _split_title_subtitle((raw.get("TITLE") or "").strip(), "")
    title, subtitle, part_number = _extract_part_number(title, subtitle)

    author_raw = (raw.get("AUTHOR") or "").strip()
    authors, translators, illustrators, designers, _ = _parse_korean_contributors(author_raw)

    publisher = (raw.get("PUBLISHER") or "").strip()
    pub_raw = (raw.get("PUBLISH_PREDATE") or "").strip()
    pub_date = pub_raw
    if len(pub_raw) == 8 and pub_raw.isdigit():
        pub_date = f"{pub_raw[0:4]}-{pub_raw[4:6]}-{pub_raw[6:8]}"

    pages: int | None = None
    page_raw = str(raw.get("PAGE") or "").strip()
    if page_raw.isdigit():
        pages = int(page_raw)

    subject_raw = (raw.get("SUBJECT") or "").strip()
    subjects = [subject_raw] if subject_raw else None

    ea = (raw.get("EA_ISBN") or "").strip()
    out_isbn = re.sub(r"[^0-9Xx]", "", ea).upper() or isbn

    return BookInfo(
        isbn=out_isbn,
        title=title,
        subtitle=subtitle,
        part_number=part_number,
        authors=authors or None,
        publish_places=None,
        publishers=[publisher] if publisher else None,
        publish_date=pub_date,
        number_of_pages=pages,
        subjects=subjects,
        translators=translators or None,
        illustrators=illustrators or None,
        designers=designers or None,
    )


def fetch_book_info_nl_seoji(isbn: str, cert_key: str) -> BookInfo:
    """국립중앙도서관 ISBN 서지정보 OpenAPI (공공데이터포털에서 발급한 cert_key 필요)."""
    last_lookup: LookupError | None = None
    last_conn: ConnectionError | None = None
    for base in (NL_SEOKJI_API, NL_SEOKJI_API_ALT):
        try:
            payload = _nl_seoji_request(isbn, cert_key, base)
            raw = _nl_seoji_extract_first_record(payload)
            if raw:
                return _bookinfo_from_nl_raw(raw, isbn)
            last_lookup = LookupError("국립중앙도서관에서 해당 ISBN 서지를 찾지 못했습니다.")
        except LookupError as exc:
            last_lookup = exc
        except ConnectionError as exc:
            last_conn = exc
    if last_lookup:
        raise last_lookup
    if last_conn:
        raise last_conn
    raise LookupError("국립중앙도서관에서 해당 ISBN 서지를 찾지 못했습니다.")


def _merge_book_info(isbn: str, *layers: BookInfo | None) -> BookInfo:
    """앞선 레이어에 뒤 레이어가 덮어쓰기 (값이 있는 필드만)."""
    out = BookInfo(isbn=isbn)
    for layer in layers:
        if layer is None:
            continue
        for field in (
            "isbn",
            "title",
            "subtitle",
            "part_number",
            "authors",
            "publish_places",
            "publishers",
            "publish_date",
            "number_of_pages",
            "subjects",
            "translators",
            "illustrators",
            "designers",
            "foreign_author_originals",
        ):
            val = getattr(layer, field)
            if val is None:
                continue
            if isinstance(val, str) and not val.strip():
                continue
            if isinstance(val, list) and len(val) == 0:
                continue
            setattr(out, field, val)
    return out


def _book_has_bibliographic_data(book: BookInfo) -> bool:
    return bool(
        (book.title and book.title != "[표제없음]")
        or book.authors
        or book.publishers
        or book.publish_date
    )


def fetch_book_info_auto(isbn: str, nl_cert_key: str | None) -> BookInfo:
    """Open Library(키 불필요) 우선 + 국립중앙도서관 서지(선택 인증키)로 보강."""
    ol: BookInfo | None = None
    try:
        ol = fetch_book_info_openlibrary(isbn)
    except (LookupError, ConnectionError):
        ol = None

    nl: BookInfo | None = None
    if nl_cert_key:
        try:
            nl = fetch_book_info_nl_seoji(isbn, nl_cert_key)
        except (LookupError, ConnectionError):
            nl = None

    merged = _merge_book_info(isbn, ol, nl)
    if not _book_has_bibliographic_data(merged):
        raise LookupError(
            "모든 소스에서 서지를 찾지 못했습니다. "
            "Open Library에 없는 한국 도서는 공공데이터포털에서 발급한 "
            "NL_SEOKJI_CERT_KEY(국립중앙도서관 ISBN 서지 API)를 설정해 보세요."
        )
    return merged


def fetch_book_info_by_isbn(
    isbn: str,
    source: str,
    aladin_ttb_key: str | None = None,
    nl_seokji_cert_key: str | None = None,
) -> BookInfo:
    if source == "openlibrary":
        return fetch_book_info_openlibrary(isbn)
    if source == "aladin":
        if not aladin_ttb_key:
            raise ValueError(
                "알라딘 API 사용 시 TTB 키가 필요합니다. "
                "터미널에서는 환경변수 ALADIN_TTB_KEY, Streamlit에서는 secrets.toml을 설정하세요."
            )
        return fetch_book_info_aladin(isbn, aladin_ttb_key)
    if source == "nl_seoji":
        if not nl_seokji_cert_key:
            raise ValueError(
                "국립중앙도서관 ISBN 서지 API 사용 시 인증키가 필요합니다. "
                "환경변수 NL_SEOKJI_CERT_KEY(또는 NL_ISBN_CERT_KEY)에 공공데이터포털에서 발급받은 cert_key를 넣으세요."
            )
        return fetch_book_info_nl_seoji(isbn, nl_seokji_cert_key)
    if source == "auto":
        return fetch_book_info_auto(isbn, nl_seokji_cert_key)
    raise ValueError(f"지원하지 않는 source: {source}")


def _escape_marc_subfield_data(value: str) -> str:
    """서브필드 구분자($)가 데이터에 들어가면 이스케이프."""
    return value.replace("$", "$$")


def _build_245_responsibility(book: BookInfo) -> str:
    """245 책임표시: 첫 저자 /$d, 둘째 저자부터 ,$e, 번역·그림·디자인 등 ;$e."""
    chunks: list[str] = []
    authors = book.authors or []

    if authors:
        chunks.append(f"/$d{_escape_marc_subfield_data(authors[0])}")
        for name in authors[1:]:
            chunks.append(f",$e{_escape_marc_subfield_data(name)}")

    for name in book.translators or []:
        chunks.append(f";$e{_escape_marc_subfield_data(name)}")

    for name in book.illustrators or []:
        chunks.append(f";$e{_escape_marc_subfield_data(name)}")

    for name in book.designers or []:
        chunks.append(f";$e{_escape_marc_subfield_data(name)}")

    return "".join(chunks)


def _contributors_for_700(book: BookInfo) -> list[str]:
    """700 필드 생성용 개인명 목록 ($a만 사용)."""
    names: list[str] = []
    for name in book.authors or []:
        names.append(name)
    for name in book.translators or []:
        names.append(name)
    for name in book.illustrators or []:
        names.append(name)
    for name in book.designers or []:
        names.append(name)
    return names


def _is_corporate_name(name: str) -> bool:
    """개인명/단체명 단순 휴리스틱 판별 (단체면 True)."""
    corporate_tokens = (
        "협의회",
        "위원회",
        "센터",
        "재단",
        "학회",
        "연구회",
        "연구소",
        "도서관",
        "박물관",
        "미술관",
        "학교",
        "대학",
        "출판사",
        "출판문화사",
        "기관",
        "협회",
        "공사",
        "공단",
        "회사",
        "연합",
        "조합",
        "정부",
        "부",
        "청",
        "원",
        "국",
        "시청",
        "구청",
        "군청",
        "법인",
        "주식회사",
        "Inc",
        "Corp",
        "Ltd",
        "University",
        "Institute",
        "Council",
        "Committee",
        "Association",
        "Foundation",
        "Center",
        "Library",
        "Museum",
    )
    return any(token in name for token in corporate_tokens)


def _invert_personal_name(name: str) -> str:
    """이름을 '성, 이름' 형태로 도치."""
    chunks = [x for x in name.strip().split() if x]
    if len(chunks) < 2:
        return name.strip()
    surname = chunks[-1]
    given = " ".join(chunks[:-1])
    return f"{surname}, {given}"


def build_komarc(book: BookInfo) -> list[str]:
    """245 00 + 700 + 900 10 필드 생성."""
    lines: list[str] = []

    # 245: 지시기호 00 — 서명 $a, 부제 :$b, 권차/편차 .$n, 첫 저자 /$d, 공저 ,$e, 역할자 ;$e
    title_main = _escape_marc_subfield_data(book.title or "[표제없음]")
    base_245 = f"245  00$a{title_main}"
    if book.subtitle:
        sub = _escape_marc_subfield_data(book.subtitle)
        base_245 = f"{base_245}:$b{sub}"
    if book.part_number:
        part = _escape_marc_subfield_data(book.part_number)
        base_245 = f"{base_245}.$n{part}"

    resp = _build_245_responsibility(book)
    if resp:
        base_245 = f"{base_245}{resp}"

    lines.append(base_245)

    # 700: 개인명(지시기호 1), 단체명(지시기호 0)
    foreign_map = book.foreign_author_originals or {}
    for name in _contributors_for_700(book):
        if name in foreign_map:
            original_name = _invert_personal_name(foreign_map[name])
            escaped_original = _escape_marc_subfield_data(original_name)
            lines.append(f"700  1$a{escaped_original}")

            korean_inverted = _invert_personal_name(name)
            escaped_korean = _escape_marc_subfield_data(korean_inverted)
            lines.append(f"900  10$a{escaped_korean}")
            continue

        escaped_name = _escape_marc_subfield_data(name)
        indicator = "0" if _is_corporate_name(name) else "1"
        lines.append(f"700  {indicator}$a{escaped_name}")

    return lines


def build_markdown_output(book: BookInfo, komarc_lines: list[str]) -> str:
    authors = ", ".join(book.authors or []) or "-"
    publishers = ", ".join(book.publishers or []) or "-"
    subjects = ", ".join(book.subjects or []) or "-"
    publish_date = book.publish_date or "-"
    title = book.title or "[표제없음]"
    subtitle = book.subtitle or ""

    if subtitle:
        title_line = f"{title} : {subtitle}"
    else:
        title_line = title

    blocks = "\n".join(f"- `{line}`" for line in komarc_lines)

    return (
        f"# ISBN {book.isbn} KORMARC 결과\n\n"
        f"## 서지 정보\n"
        f"- 서명: {title_line}\n"
        f"- 저자: {authors}\n"
        f"- 출판사: {publishers}\n"
        f"- 출판일: {publish_date}\n"
        f"- 주제: {subjects}\n\n"
        f"## KORMARC 필드\n"
        f"{blocks}\n"
    )


def main() -> None:
    parser = ArgumentParser(description="ISBN으로 KORMARC(KOMARC) 필드 자동 생성")
    parser.add_argument("isbn", nargs="?", help="ISBN-10 또는 ISBN-13")
    parser.add_argument(
        "--source",
        choices=["auto", "openlibrary", "aladin", "nl_seoji"],
        default="aladin",
        help="기본은 알라딘 API(aladin)",
    )
    args = parser.parse_args()

    raw_isbn = args.isbn if args.isbn else input("ISBN을 입력하세요: ").strip()

    try:
        isbn = normalize_isbn(raw_isbn)
        nl_raw = os.getenv("NL_SEOKJI_CERT_KEY") or os.getenv("NL_ISBN_CERT_KEY") or ""
        aladin_key = os.getenv("ALADIN_TTB_KEY", "").strip() or DEFAULT_ALADIN_TTB_KEY
        book = fetch_book_info_by_isbn(
            isbn=isbn,
            source=args.source,
            aladin_ttb_key=aladin_key,
            nl_seokji_cert_key=nl_raw.strip() or None,
        )
        komarc = build_komarc(book)
        markdown_text = build_markdown_output(book, komarc)
    except Exception as exc:  # noqa: BLE001 - CLI 도구이므로 예외 메시지 단순 출력
        print(f"[오류] {exc}")
        sys.exit(1)

    print("\n[KORMARC/KOMARC 자동 생성 결과]")
    for line in komarc:
        print(line)

    md_path = f"komarc_{isbn}.md"
    with open(md_path, "w", encoding="utf-8") as fp:
        fp.write(markdown_text)
    print(f"\n마크다운 파일 저장 완료: {md_path}")


if __name__ == "__main__":
    main()
