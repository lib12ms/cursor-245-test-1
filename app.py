import os

import streamlit as st

from komarc_from_isbn import (
    DEFAULT_ALADIN_TTB_KEY,
    build_komarc,
    build_markdown_output,
    fetch_book_info_by_isbn,
    normalize_isbn,
)


st.set_page_config(page_title="ISBN -> 245/700", page_icon="📚", layout="centered")
st.title("ISBN -> 24500/7001 자동 생성")
st.caption(
    "[알라딘 API](https://blog.aladin.co.kr/openapi/category/29154417)로 조회해 "
    "KORMARC `245  00`과 `700  1` 필드를 자동 생성합니다."
)

isbn_input = st.text_input("ISBN 입력", placeholder="예: 9788998139766")
run = st.button("24500/7001 생성", type="primary")

st.info(f"현재 기본 TTBKey: `{DEFAULT_ALADIN_TTB_KEY}` (환경변수 `ALADIN_TTB_KEY`로 변경 가능)")

if run:
    try:
        isbn = normalize_isbn(isbn_input.strip())
        book = fetch_book_info_by_isbn(
            isbn=isbn,
            source="aladin",
            aladin_ttb_key=os.getenv("ALADIN_TTB_KEY", "").strip() or DEFAULT_ALADIN_TTB_KEY,
        )
        komarc_lines = build_komarc(book)
        result_text = "\n".join(komarc_lines)
        markdown_text = build_markdown_output(book, komarc_lines)

        st.success("24500/7001 생성 완료")
        st.code(result_text, language="text")
        st.download_button(
            label="결과 다운로드 (.txt)",
            data=result_text,
            file_name=f"komarc_{isbn}.txt",
            mime="text/plain",
        )
        st.download_button(
            label="마크다운 다운로드 (.md)",
            data=markdown_text,
            file_name=f"komarc_{isbn}.md",
            mime="text/markdown",
        )
    except Exception as exc:  # noqa: BLE001 - 사용자 입력 도구이므로 예외 메시지 직접 노출
        st.error(f"생성 실패: {exc}")


