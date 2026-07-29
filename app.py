#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
건축시방서 자동화 파이프라인 — Streamlit UI
============================================

실행:
    streamlit run app.py -- --db ./output/specs.db

또는 DB 경로를 .streamlit/secrets.toml에 설정:
    [paths]
    db = "./output/specs.db"
"""

import os
import sys
import re
import sqlite3
import json
from pathlib import Path
from dataclasses import asdict

import streamlit as st

# ── 경로 설정 ────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DB_PATH = str(BASE_DIR / "specs.db")
# 커맨드라인 인자로 DB 경로 덮어쓰기 가능
for i, arg in enumerate(sys.argv):
    if arg == "--db" and i + 1 < len(sys.argv):
        DB_PATH = sys.argv[i + 1]

sys.path.insert(0, str(BASE_DIR))
from process_mapper import ProcessMapper, extract_from_excel, extract_from_text, get_excel_sheets, MappingResult

# ── 페이지 설정 ──────────────────────────────────────────────
st.set_page_config(
    page_title="건축시방서 자동화",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── CSS ──────────────────────────────────────────────────────
st.markdown("""
<style>
.status-standard   { background:#e8f5e9; color:#2e7d32; padding:2px 8px;
                     border-radius:4px; font-size:0.8em; font-weight:bold; }
.status-ref        { background:#fff8e1; color:#f57f17; padding:2px 8px;
                     border-radius:4px; font-size:0.8em; font-weight:bold; }
.status-speconly   { background:#fce4ec; color:#c62828; padding:2px 8px;
                     border-radius:4px; font-size:0.8em; font-weight:bold; }
.block-title { font-size:1.1em; font-weight:bold; margin-bottom:4px; }
.note-text   { font-size:0.82em; color:#666; }
</style>
""", unsafe_allow_html=True)


# ── 캐시 ────────────────────────────────────────────────────
@st.cache_resource
def get_mapper():
    if not os.path.exists(DB_PATH):
        st.error(f"DB 파일을 찾을 수 없습니다: {DB_PATH}")
        st.stop()
    return ProcessMapper(DB_PATH)


@st.cache_data
def get_all_specs():
    mapper = get_mapper()
    return mapper.all_specs()


# ── 상태 뱃지 ────────────────────────────────────────────────
def status_badge(status: str) -> str:
    if status == "STANDARD":
        return '<span class="status-standard">● 표준시방서</span>'
    elif status == "STANDARD_REF":
        return '<span class="status-ref">◑ 참조(특기 구체화)</span>'
    else:
        return '<span class="status-speconly">○ 특기시방서만</span>'


# ── 세션 상태 초기화 ──────────────────────────────────────────
if "mapped_items" not in st.session_state:
    st.session_state.mapped_items = []   # List[dict]  (MappingResult + 사용자 수정)
if "step" not in st.session_state:
    st.session_state.step = 1            # 1:입력 2:검토/수정 3:완료


# ============================================================
# STEP 1 — 입력
# ============================================================
def step_input():
    st.title("🏗️ 건축시방서 자동화")
    st.subheader("Step 1 — 공종 입력")

    mapper = get_mapper()

    col_a, col_b = st.columns([1, 1], gap="large")

    with col_a:
        st.markdown("#### 📄 도면 / 내역서 파일 업로드")
        uploaded = st.file_uploader(
            "공사비 내역서(xlsx) 또는 공종목록(txt) 업로드",
            type=["xlsx", "txt"],
            help="xlsx: 공종별집계표 시트를 우선 탐색합니다.\ntxt: 줄바꿈 또는 쉼표로 구분된 목록을 인식합니다.",
        )
        if uploaded:
            if uploaded.name.endswith(".xlsx"):
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                    tmp.write(uploaded.read())
                    tmp_path = tmp.name

                # 시트 목록 확인
                sheets = get_excel_sheets(tmp_path)
                has_detail = any("내역서" in s for s in sheets)
                has_summary = any("집계표" in s for s in sheets)

                if sheets:
                    sheet_info = f"시트: {', '.join(sheets)}"
                    if has_summary:
                        sheet_info += " → **공종별집계표** 시트 사용"
                    st.caption(sheet_info)

                # 공종별내역서 포함 여부 선택
                use_detail = False
                if has_detail:
                    use_detail = st.checkbox(
                        "공종별내역서도 포함 (세부 공종 추가)",
                        value=False,
                        help="공종별집계표(큰 얼개) + 공종별내역서(세부 공종)를 함께 추출합니다."
                    )

                extracted = extract_from_excel(tmp_path, detail=use_detail)
                os.unlink(tmp_path)
            else:
                content = uploaded.read().decode("utf-8", errors="replace")
                extracted = extract_from_text(content)

            if extracted:
                st.success(f"{len(extracted)}개 공종 추출됨")
                st.dataframe({"추출된 공종": extracted}, height=200)
                if st.button("이 목록으로 매핑 시작", type="primary"):
                    results = mapper.map_list(extracted)
                    st.session_state.mapped_items = [asdict(r) for r in results]
                    st.session_state.step = 2
                    st.rerun()
            else:
                st.warning("공종 항목을 추출하지 못했습니다. 시트 구조를 확인하거나 직접 입력해주세요.")

    with col_b:
        st.markdown("#### ✏️ 직접 입력")
        text_input = st.text_area(
            "공종명을 입력하세요 (줄바꿈 또는 쉼표로 구분)",
            placeholder="예)\n철골공사\n콘크리트\n방수공사, 단열, 창호\n거울설치",
            height=160,
        )
        st.markdown("#### 🔍 실시간 검색으로 추가")
        search_query = st.text_input("KCS 항목 검색", placeholder="예: 방수, 타일, 단열...")
        if search_query:
            results = mapper.search(search_query, top_k=8)
            if results:
                for r in results:
                    label = f"{r['detail_code']} {r['detail_name']} ({r['process_name']})"
                    if st.button(f"＋ {label}", key=f"add_{r['detail_code']}"):
                        existing_codes = {m.get("detail_code") for m in st.session_state.mapped_items}
                        if r["detail_code"] not in existing_codes:
                            st.session_state.mapped_items.append({
                                "input_name": r["detail_name"],
                                "status": "STANDARD",
                                "detail_code": r["detail_code"],
                                "detail_name": r["detail_name"],
                                "process_code": r["process_code"],
                                "process_name": r["process_name"],
                                "score": r["score"],
                                "note": "직접 선택",
                            })
                            st.rerun()

        if text_input.strip():
            if st.button("텍스트 입력으로 매핑", type="primary"):
                names = extract_from_text(text_input)
                results = mapper.map_list(names)
                existing_inputs = {m.get("input_name") for m in st.session_state.mapped_items}
                new_items = [asdict(r) for r in results if r.input_name not in existing_inputs]
                st.session_state.mapped_items.extend(new_items)
                st.session_state.step = 2
                st.rerun()

    # 이미 항목이 있으면 검토 단계로 바로 이동 가능
    if st.session_state.mapped_items:
        st.divider()
        st.info(f"현재 {len(st.session_state.mapped_items)}개 항목이 선택되어 있습니다.")
        if st.button("검토 단계로 이동 →", type="primary"):
            st.session_state.step = 2
            st.rerun()


# ============================================================
# STEP 2 — 검토 및 수정
# ============================================================
def step_review():
    st.title("🏗️ 건축시방서 자동화")
    st.subheader("Step 2 — 매핑 결과 검토 / 수정")

    mapper = get_mapper()
    all_specs = get_all_specs()
    spec_options = {
        f"{s['detail_code']} {s['detail_name']}": s
        for s in all_specs
    }

    items = st.session_state.mapped_items
    if not items:
        st.warning("입력된 항목이 없습니다.")
        if st.button("← 입력으로 돌아가기"):
            st.session_state.step = 1
            st.rerun()
        return

    # 요약 통계
    cnt = {"STANDARD": 0, "STANDARD_REF": 0, "SPEC_ONLY": 0}
    for m in items:
        cnt[m.get("status", "SPEC_ONLY")] += 1
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("전체", len(items))
    c2.metric("표준시방서 직접", cnt["STANDARD"])
    c3.metric("근접 참조", cnt["STANDARD_REF"])
    c4.metric("특기시방서만", cnt["SPEC_ONLY"])

    st.divider()

    # 각 항목 카드
    to_delete = []
    for i, item in enumerate(items):
        status = item.get("status", "SPEC_ONLY")
        with st.container(border=True):
            col1, col2, col3 = st.columns([3, 5, 1])

            with col1:
                st.markdown(f'<div class="block-title">{item["input_name"]}</div>', unsafe_allow_html=True)
                st.markdown(status_badge(status), unsafe_allow_html=True)

            with col2:
                if status == "SPEC_ONLY":
                    st.markdown('<span class="note-text">KCS 표준시방서 해당 없음 → 특기시방서(AI 초안) 생성</span>',
                                unsafe_allow_html=True)
                    # SPEC_ONLY도 수동으로 KCS 연결 가능
                    override = st.selectbox(
                        "KCS 항목 수동 연결 (선택사항)",
                        ["(연결 안 함)"] + list(spec_options.keys()),
                        key=f"override_{i}",
                    )
                    if override != "(연결 안 함)":
                        s = spec_options[override]
                        items[i].update({
                            "status": "STANDARD_REF",
                            "detail_code": s["detail_code"],
                            "detail_name": s["detail_name"],
                            "process_code": s["process_code"],
                            "process_name": s["process_name"],
                            "note": "사용자 수동 연결",
                        })
                else:
                    mapped_label = f"{item.get('detail_code','')} {item.get('detail_name','')}"
                    new_sel = st.selectbox(
                        "매핑된 KCS 항목",
                        list(spec_options.keys()),
                        index=next(
                            (j for j, k in enumerate(spec_options) if item.get("detail_code","") in k),
                            0
                        ),
                        key=f"sel_{i}",
                    )
                    if new_sel:
                        s = spec_options[new_sel]
                        items[i].update({
                            "detail_code": s["detail_code"],
                            "detail_name": s["detail_name"],
                            "process_code": s["process_code"],
                            "process_name": s["process_name"],
                        })
                    if item.get("note"):
                        st.markdown(f'<span class="note-text">{item["note"]}</span>', unsafe_allow_html=True)

            with col3:
                if st.button("🗑️", key=f"del_{i}", help="항목 삭제"):
                    to_delete.append(i)

    if to_delete:
        for idx in sorted(to_delete, reverse=True):
            items.pop(idx)
        st.rerun()

    st.divider()

    # 항목 추가
    with st.expander("➕ 항목 추가"):
        add_col1, add_col2 = st.columns(2)
        with add_col1:
            new_text = st.text_input("공종명 입력", key="add_text")
            if st.button("매핑해서 추가"):
                if new_text.strip():
                    r = mapper.map(new_text.strip())
                    items.append(asdict(r))
                    st.rerun()
        with add_col2:
            direct_sel = st.selectbox("KCS 직접 선택", [""] + list(spec_options.keys()), key="direct_add")
            if st.button("직접 선택으로 추가"):
                if direct_sel:
                    s = spec_options[direct_sel]
                    items.append({
                        "input_name": s["detail_name"],
                        "status": "STANDARD",
                        "detail_code": s["detail_code"],
                        "detail_name": s["detail_name"],
                        "process_code": s["process_code"],
                        "process_name": s["process_name"],
                        "score": 1.0,
                        "note": "직접 선택",
                    })
                    st.rerun()

    # SPEC_ONLY 항목에 공종명 직접 입력 추가
    with st.expander("➕ KCS 없는 항목 직접 추가 (특기시방서만)"):
        spec_name = st.text_input("항목명", placeholder="예: 프로젝터 설치", key="spec_only_name")
        spec_note = st.text_input("비고 (선택)", placeholder="예: 제조사 지정 시방 따름", key="spec_only_note")
        if st.button("특기시방서 항목으로 추가"):
            if spec_name.strip():
                items.append({
                    "input_name": spec_name.strip(),
                    "status": "SPEC_ONLY",
                    "detail_code": None,
                    "detail_name": None,
                    "process_code": None,
                    "process_name": None,
                    "score": 0.0,
                    "note": spec_note.strip() or "KCS 표준시방서 해당 없음",
                })
                st.rerun()

    st.divider()
    nav_col1, nav_col2 = st.columns(2)
    with nav_col1:
        if st.button("← 입력으로 돌아가기"):
            st.session_state.step = 1
            st.rerun()
    with nav_col2:
        if st.button("✅ 공종 목록 확정 →", type="primary"):
            st.session_state.step = 3
            st.rerun()


# ============================================================
# STEP 3 — 완료 / 내보내기
# ============================================================
def step_done():
    import datetime as dt
    st.title("🏗️ 건축시방서 자동화")
    st.subheader("Step 3 — 공종 목록 확정 및 시방서 생성")

    items = st.session_state.mapped_items
    if not items:
        st.warning("항목이 없습니다.")
        if st.button("← 처음으로"):
            st.session_state.step = 1
            st.rerun()
        return

    cnt = {"STANDARD": 0, "STANDARD_REF": 0, "SPEC_ONLY": 0}
    for m in items:
        cnt[m.get("status", "SPEC_ONLY")] += 1

    st.success(f"총 {len(items)}개 공종 확정 — 표준시방서 {cnt['STANDARD']}건 / 참조 {cnt['STANDARD_REF']}건 / 특기만 {cnt['SPEC_ONLY']}건")

    # 확정 목록 표시
    rows = []
    for item in items:
        status_label = {"STANDARD":"표준시방서","STANDARD_REF":"참조(특기구체화)","SPEC_ONLY":"특기시방서만"}.get(item["status"],"?")
        rows.append({
            "공종명": item["input_name"],
            "구분": status_label,
            "KCS 코드": item.get("detail_code") or "-",
            "KCS 항목명": item.get("detail_name") or "-",
            "대공정": item.get("process_name") or "-",
            "비고": item.get("note") or "",
        })

    import pandas as pd
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, height=300)

    col1, col2 = st.columns(2)
    with col1:
        json_str = json.dumps(items, ensure_ascii=False, indent=2)
        st.download_button("📥 공종목록 JSON", data=json_str.encode("utf-8"),
                           file_name="공종목록.json", mime="application/json")
    with col2:
        csv_str = df.to_csv(index=False, encoding="utf-8-sig")
        st.download_button("📥 공종목록 CSV", data=csv_str.encode("utf-8-sig"),
                           file_name="공종목록.csv", mime="text/csv")

    # ── ① 공사 개요 입력 ────────────────────────────────────────
    st.divider()
    st.markdown("#### 📋 공사 개요 입력")
    st.caption("표지, 목차, 공사개요 페이지에 사용됩니다.")

    saved = st.session_state.get("project_info", {})
    now = dt.datetime.now()

    c1, c2 = st.columns(2)
    with c1:
        pname = st.text_input("공사명 *", value=saved.get("project_name", ""),
                              placeholder="예: 부1리 경로당 신축공사", key="pi_pname")
        client = st.text_input("발주처 *", value=saved.get("client", ""),
                               placeholder="예: ○○군청", key="pi_client")
        location = st.text_input("대지위치", value=saved.get("location", ""),
                                 placeholder="예: ○○도 ○○군 ○○면 ○○리 123", key="pi_loc")
        year_month = st.text_input("준공 연월 (표지용)", key="pi_ym",
                                   value=saved.get("year_month", f"{now.year}. {now.month:02d}."),
                                   placeholder="예: 2026. 07.")
    with c2:
        site_area    = st.text_input("대지면적", value=saved.get("site_area",""),    placeholder="예: 500.00㎡", key="pi_sa")
        building_area= st.text_input("건축면적", value=saved.get("building_area",""),placeholder="예: 150.00㎡", key="pi_ba")
        total_floor  = st.text_input("연면적",   value=saved.get("total_floor_area",""),placeholder="예: 200.00㎡", key="pi_tf")
        bcov         = st.text_input("건폐율",   value=saved.get("building_coverage",""),placeholder="예: 30.00%", key="pi_bc")
        far          = st.text_input("용적율",   value=saved.get("floor_area_ratio",""), placeholder="예: 40.00%", key="pi_far")
        floors       = st.text_input("층수",     value=saved.get("floors",""),       placeholder="예: 지상 1층", key="pi_fl")

    c3, c4 = st.columns(2)
    with c3:
        structure  = st.text_input("주요구조", value=saved.get("structure",""), placeholder="예: 철근콘크리트조", key="pi_str")
    with c4:
        foundation = st.text_input("기초형식", value=saved.get("foundation",""), placeholder="예: 줄기초", key="pi_fnd")

    # 층별 바닥면적 (텍스트로 입력 → 파싱)
    with st.expander("층별 바닥면적 입력 (선택)", expanded=False):
        st.caption("층명, 면적(㎡), 비고 순으로 한 줄씩 입력 (쉼표 구분)")
        floor_default = "\n".join(
            ", ".join(str(x) for x in row)
            for row in saved.get("floor_areas", [])
        ) or "1층, 150.00, \n합계, 150.00, "
        floor_text = st.text_area("층별 바닥면적", value=floor_default,
                                  height=120, key="pi_floors")

    # 재료 마감 (텍스트 입력)
    with st.expander("재료 마감 입력 (선택)", expanded=False):
        st.caption("부위, 마감재 순으로 한 줄씩 (쉼표 구분)")
        finish_default = "\n".join(
            ", ".join(str(x) for x in row)
            for row in saved.get("finishes", [])
        ) or "외벽, 시멘트 뿜칠\n내벽, 수성페인트\n바닥, 강화마루\n천장, 텍스"
        finish_text = st.text_area("재료 마감", value=finish_default,
                                   height=100, key="pi_finish")

    def parse_table_text(text):
        rows = []
        for line in text.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if any(parts):
                rows.append(parts)
        return rows

    # ── ② API 키 ────────────────────────────────────────────────
    st.divider()
    st.markdown("#### 🤖 AI 초안 설정")
    api_key_input = st.text_input(
        "Anthropic API 키 (AI 초안 생성용)",
        value=st.session_state.get("api_key", ""),
        type="password",
        help="STANDARD_REF·SPEC_ONLY 항목 AI 초안에 필요. 없으면 표준 원문 인용만 생성됩니다.",
        key="api_key_input",
    )

    # ── ③ 생성 버튼 ─────────────────────────────────────────────
    st.divider()
    if st.button("🚀 특기시방서 생성 + Word 문서 조립", type="primary"):
        if not pname.strip():
            st.error("공사명을 입력해주세요.")
            st.stop()

        # 공사개요 저장
        project_info = {
            "project_name":    pname.strip(),
            "client":          client.strip(),
            "year_month":      year_month.strip(),
            "location":        location.strip(),
            "site_area":       site_area.strip(),
            "building_area":   building_area.strip(),
            "total_floor_area":total_floor.strip(),
            "building_coverage":bcov.strip(),
            "floor_area_ratio":far.strip(),
            "floors":          floors.strip(),
            "structure":       structure.strip(),
            "foundation":      foundation.strip(),
            "floor_areas":     parse_table_text(floor_text),
            "finishes":        parse_table_text(finish_text),
        }
        st.session_state["project_info"] = project_info
        st.session_state["project_name"] = pname.strip()

        if api_key_input.strip():
            os.environ["ANTHROPIC_API_KEY"] = api_key_input.strip()
            st.session_state["api_key"] = api_key_input.strip()
        elif hasattr(st, "secrets") and "ANTHROPIC_API_KEY" in st.secrets:
            os.environ["ANTHROPIC_API_KEY"] = st.secrets["ANTHROPIC_API_KEY"]

        out_dir = str(BASE_DIR / "generated")
        sys.path.insert(0, str(BASE_DIR))
        from spec_generator import generate_all, save_results

        # 시방서 내용 생성
        with st.spinner(f"시방서 내용 생성 중... (총 {len(items)}건)"):
            results = generate_all(items, DB_PATH, pname.strip())
            json_path = save_results(results, out_dir, pname.strip())

        ai_count = sum(
            1 for r in results
            if any(getattr(s, "is_ai", False) for s in (r.sections or []))
        )
        st.info(f"시방서 내용 생성 완료 — AI 초안 포함: {ai_count}건")

        # JSON 변환 (sections 를 dict 리스트로)
        specs_for_docx = []
        for r in results:
            sec_list = []
            for s in (r.sections or []):
                sec_list.append({
                    "is_ai": getattr(s, "is_ai", False),
                    "body":  getattr(s, "body", str(s)),
                })
            specs_for_docx.append({
                "input_name":  r.input_name,
                "status":      r.status,
                "detail_code": r.detail_code,
                "detail_name": r.detail_name,
                "sections":    sec_list,
                "error":       getattr(r, "error", None),
            })

        # Word 문서 조립
        with st.spinner("Word 문서 조립 중..."):
            try:
                from docx_assembler import assemble_docx
                docx_path = assemble_docx(project_info, specs_for_docx, out_dir)
                with open(docx_path, "rb") as f:
                    docx_bytes = f.read()
                safe_name = "".join(c for c in pname.strip() if c.isalnum() or c in "가-힣 _-")
                st.success(f"Word 문서 조립 완료!")
                st.download_button(
                    "📄 특기시방서 Word(.docx) 다운로드",
                    data=docx_bytes,
                    file_name=f"{safe_name}_특기시방서_{dt.date.today().strftime('%Y%m%d')}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            except Exception as e:
                st.warning(f"Word 조립 중 오류 (JSON은 아래에서 다운로드): {e}")

        # JSON 다운로드 (백업)
        with open(json_path, encoding="utf-8") as f:
            result_json = f.read()
        st.download_button(
            "📥 시방서 JSON 다운로드 (백업)",
            data=result_json.encode("utf-8"),
            file_name=f"{pname.strip()}_특기시방서_{dt.date.today().strftime('%Y%m%d')}.json",
            mime="application/json",
        )

        # 생성 리포트
        report_path = BASE_DIR / "generated" / f"{pname.strip()}_생성리포트_{dt.date.today().strftime('%Y%m%d')}.txt"
        if report_path.exists():
            with open(report_path, encoding="utf-8") as f:
                with st.expander("생성 리포트 보기"):
                    st.text(f.read())

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("← 수정하러 돌아가기"):
            st.session_state.step = 2
            st.rerun()
    with col_b:
        if st.button("🔄 새 프로젝트 시작"):
            st.session_state.mapped_items = []
            st.session_state.step = 1
            st.rerun()


# ============================================================
# 라우터
# ============================================================
# 상단 진행 표시
progress_labels = {1: "공종 입력", 2: "매핑 검토", 3: "목록 확정"}
cols = st.columns(3)
for idx, (step_no, label) in enumerate(progress_labels.items()):
    with cols[idx]:
        is_current = (step_no == st.session_state.step)
        style = "**" if is_current else ""
        icon = "🔵" if is_current else ("✅" if step_no < st.session_state.step else "⚪")
        st.markdown(f"{icon} {style}{label}{style}")

st.markdown("---")

if st.session_state.step == 1:
    step_input()
elif st.session_state.step == 2:
    step_review()
else:
    step_done()
