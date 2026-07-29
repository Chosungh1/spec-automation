#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
특기시방서 생성 엔진
====================

STANDARD   : DB에서 표준시방서 원문 인용 (AI 개입 없음)
STANDARD_REF: 근접 KCS 원문 참조 + AI 초안 (검수필요 표기)
SPEC_ONLY  : 웹검색 기반 AI 전체 초안 (검수필요 표기)

사용법:
    python3 spec_generator.py <공종목록.json> <출력폴더> [--project 프로젝트명]

환경변수:
    ANTHROPIC_API_KEY  - Anthropic API 키 (STANDARD_REF, SPEC_ONLY 생성에 필요)
"""

import os
import re
import sys
import json
import sqlite3
import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, asdict

AI_DRAFT_HEADER = (
    "\n\n" + "=" * 60 + "\n"
    "⚠️  [AI 초안 — 검수필요]\n"
    "   아래 내용은 AI가 생성한 초안입니다.\n"
    "   관공서 납품 전 반드시 담당자가 검토·수정하십시오.\n"
    + "=" * 60 + "\n\n"
)

AI_DRAFT_FOOTER = (
    "\n" + "=" * 60 + "\n"
    "⚠️  [AI 초안 끝 — 위 내용 검수 후 사용]\n"
    + "=" * 60 + "\n"
)

KCS_CITE_TEMPLATE = (
    "【표준시방서 원문 인용】\n"
    "출처: {detail_code} {detail_name}\n"
    "인용일: {date}\n"
    "※ 원문을 그대로 인용하며 임의 수정 금지\n"
    + "-" * 60 + "\n"
)


# ---------------------------------------------------------------------------
# 결과 구조
# ---------------------------------------------------------------------------

@dataclass
class SpecSection:
    key: str        # "1_일반사항"
    no: str         # "1"
    title: str      # "일반사항"
    body: str
    is_ai: bool = False


@dataclass
class GeneratedSpec:
    input_name: str
    status: str
    detail_code: Optional[str]
    detail_name: Optional[str]
    process_code: Optional[str]
    process_name: Optional[str]
    sections: list = field(default_factory=list)   # List[SpecSection]
    note: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# DB 조회
# ---------------------------------------------------------------------------

def get_sections_from_db(db_path: str, detail_code: str) -> list:
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT section_key, section_no, section_title, body "
        "FROM sections WHERE detail_code = ? ORDER BY section_no",
        (detail_code,)
    ).fetchall()
    con.close()
    return [
        SpecSection(key=r[0], no=r[1], title=r[2], body=r[3], is_ai=False)
        for r in rows
    ]


def get_tables_from_db(db_path: str, detail_code: str) -> list:
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT table_index, table_json FROM spec_tables "
        "WHERE detail_code = ? ORDER BY table_index",
        (detail_code,)
    ).fetchall()
    con.close()
    return [(r[0], json.loads(r[1])) for r in rows]


# ---------------------------------------------------------------------------
# AI 생성 (Anthropic API)
# ---------------------------------------------------------------------------

def _get_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=api_key)
    except ImportError:
        return None


def ai_generate(prompt: str, max_tokens: int = 4000) -> Optional[str]:
    client = _get_client()
    if client is None:
        return None
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text
    except Exception as e:
        return f"[AI 생성 오류: {e}]"


def build_prompt_ref(item_name: str, kcs_code: str, kcs_name: str,
                     kcs_excerpt: str) -> str:
    return f"""당신은 건축시방서 작성 전문가입니다.

아래는 "{kcs_name}" ({kcs_code}) 표준시방서의 일부 발췌입니다.
이를 참조하여 "{item_name}"에 대한 특기시방서 초안을 작성하세요.

【참조 표준시방서 발췌】
{kcs_excerpt[:3000]}

【작성 지침】
- KCS 표준 목차 구조(1.일반사항 / 2.자재 / 3.시공)를 유지하세요.
- 참조 표준시방서와 다른 부분은 명확히 표시하세요.
- 수치·규격은 임의로 기재하지 말고 [설계도서 참조] 또는 [담당자 확인 필요]로 표시하세요.
- 관공서 납품용 문체(공식적, 정제된 한국어)로 작성하세요.
- 분량: 핵심 내용만 간결하게, 과도한 부연 금지.

"{item_name}" 특기시방서 초안:"""


def build_prompt_spec_only(item_name: str) -> str:
    return f"""당신은 건축시방서 작성 전문가입니다.

"{item_name}"에 대한 건축 특기시방서 초안을 작성하세요.
KCS 표준시방서에 해당 항목이 없어 AI가 초안을 생성합니다.

【작성 지침】
- KCS 표준 목차 구조를 따르세요:
  1. 일반사항 (1.1 적용범위, 1.2 관련기준, 1.3 용어 정의)
  2. 자재 (품질기준, 자재 승인)
  3. 시공 (시공절차, 검사, 품질관리)
- 수치·규격·제조사는 임의로 기재하지 말고 [설계도서 참조] 또는 [담당자 확인 필요]로 표시하세요.
- 관련 KS 규격이 있다면 번호만 기재하고 내용은 담당자가 확인하도록 하세요.
- 관공서 납품용 공식 문체로 작성하세요.
- 분량: 핵심 사항만 간결하게.

"{item_name}" 특기시방서 초안:"""


# ---------------------------------------------------------------------------
# 생성 로직
# ---------------------------------------------------------------------------

def generate_standard(db_path: str, item: dict) -> GeneratedSpec:
    """STANDARD: DB 원문 그대로 인용"""
    date_str = datetime.date.today().isoformat()
    cite = KCS_CITE_TEMPLATE.format(
        detail_code=item.get("detail_code", ""),
        detail_name=item.get("detail_name", ""),
        date=date_str,
    )
    sections = get_sections_from_db(db_path, item["detail_code"])
    if sections:
        # 첫 섹션 앞에 인용 출처 삽입
        sections[0].body = cite + sections[0].body

    return GeneratedSpec(
        input_name=item["input_name"],
        status="STANDARD",
        detail_code=item.get("detail_code"),
        detail_name=item.get("detail_name"),
        process_code=item.get("process_code"),
        process_name=item.get("process_name"),
        sections=sections,
        note="표준시방서 원문 인용",
    )


def generate_standard_ref(db_path: str, item: dict) -> GeneratedSpec:
    """STANDARD_REF: 근접 KCS 원문 참조 + AI 초안"""
    ref_sections = get_sections_from_db(db_path, item["detail_code"])
    # 참조용 발췌: 1.일반사항 + 2.자재 앞부분만
    excerpt_parts = []
    for s in ref_sections:
        if s.no in ("1", "2"):
            excerpt_parts.append(s.body[:1500])
    kcs_excerpt = "\n\n".join(excerpt_parts)

    prompt = build_prompt_ref(
        item_name=item["input_name"],
        kcs_code=item.get("detail_code", ""),
        kcs_name=item.get("detail_name", ""),
        kcs_excerpt=kcs_excerpt,
    )
    ai_text = ai_generate(prompt)

    if ai_text is None:
        # API 없음 → 참조 원문만 제공 + 안내
        ai_text = (
            "[API 키 없음: AI 초안을 생성하려면 ANTHROPIC_API_KEY 환경변수를 설정하세요]\n\n"
            f"참조 표준시방서: {item.get('detail_code')} {item.get('detail_name')}\n"
            "위 표준시방서를 참고하여 담당자가 직접 작성하십시오."
        )
        is_ai = False
    else:
        is_ai = True

    # 참조 섹션들을 원문으로 포함하고 AI 초안을 별도 섹션으로 추가
    date_str = datetime.date.today().isoformat()
    ref_note = (
        f"【참조 표준시방서】 {item.get('detail_code')} {item.get('detail_name')}\n"
        f"(인용일: {date_str})\n"
        "아래 참조 항목을 바탕으로 본 공종에 맞게 수정·적용하십시오.\n"
        + "-" * 60 + "\n"
    )

    sections = [
        SpecSection(
            key="0_참조원문",
            no="0",
            title=f"참조 표준시방서 ({item.get('detail_name', '')})",
            body=ref_note + "\n".join(s.body for s in ref_sections),
            is_ai=False,
        ),
        SpecSection(
            key="AI_초안",
            no="AI",
            title=f"{item['input_name']} 특기시방서 AI 초안",
            body=AI_DRAFT_HEADER + ai_text + AI_DRAFT_FOOTER,
            is_ai=is_ai,
        ),
    ]

    return GeneratedSpec(
        input_name=item["input_name"],
        status="STANDARD_REF",
        detail_code=item.get("detail_code"),
        detail_name=item.get("detail_name"),
        process_code=item.get("process_code"),
        process_name=item.get("process_name"),
        sections=sections,
        note=item.get("note", "근접 KCS 참조 + AI 초안"),
    )


def generate_spec_only(item: dict) -> GeneratedSpec:
    """SPEC_ONLY: 전체 AI 초안"""
    prompt = build_prompt_spec_only(item["input_name"])
    ai_text = ai_generate(prompt)

    if ai_text is None:
        ai_text = (
            "[API 키 없음: AI 초안을 생성하려면 ANTHROPIC_API_KEY 환경변수를 설정하세요]\n\n"
            "KCS 표준시방서에 해당 항목이 없습니다.\n"
            "담당자가 직접 특기시방서를 작성하거나, 제조사 시방서를 첨부하십시오."
        )
        is_ai = False
    else:
        is_ai = True

    sections = [
        SpecSection(
            key="AI_초안",
            no="AI",
            title=f"{item['input_name']} 특기시방서 AI 초안",
            body=AI_DRAFT_HEADER + ai_text + AI_DRAFT_FOOTER,
            is_ai=is_ai,
        )
    ]

    return GeneratedSpec(
        input_name=item["input_name"],
        status="SPEC_ONLY",
        detail_code=None,
        detail_name=None,
        process_code=None,
        process_name=None,
        sections=sections,
        note="KCS 없음 - AI 전체 초안",
    )


# ---------------------------------------------------------------------------
# 메인 파이프라인
# ---------------------------------------------------------------------------

def generate_all(work_list: list, db_path: str,
                 project_name: str = "건축공사") -> list:
    results = []
    total = len(work_list)
    for i, item in enumerate(work_list, 1):
        status = item.get("status", "SPEC_ONLY")
        name = item.get("input_name", "")
        print(f"[{i}/{total}] {status} | {name}", end="... ", flush=True)

        try:
            if status == "STANDARD":
                result = generate_standard(db_path, item)
            elif status == "STANDARD_REF":
                result = generate_standard_ref(db_path, item)
            else:
                result = generate_spec_only(item)
            print("완료")
        except Exception as e:
            print(f"오류: {e}")
            result = GeneratedSpec(
                input_name=name, status=status,
                detail_code=item.get("detail_code"),
                detail_name=item.get("detail_name"),
                process_code=item.get("process_code"),
                process_name=item.get("process_name"),
                error=str(e),
            )
        results.append(result)
    return results


def save_results(results: list, out_dir: str, project_name: str = "건축공사"):
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    date_str = datetime.date.today().strftime("%Y%m%d")

    # JSON 저장 (전체)
    data = []
    for r in results:
        d = asdict(r)
        # SpecSection 리스트를 직렬화
        d["sections"] = [
            {"key": s.key, "no": s.no, "title": s.title,
             "body": s.body, "is_ai": s.is_ai}
            for s in r.sections
        ] if r.sections else []
        data.append(d)

    json_path = out_path / f"{project_name}_특기시방서_{date_str}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 텍스트 미리보기 저장 (공종별 폴더)
    preview_dir = out_path / "preview"
    preview_dir.mkdir(exist_ok=True)
    for r in results:
        safe = re.sub(r"[^\w가-힣]", "_", r.input_name).strip("_")
        txt_path = preview_dir / f"{safe}.txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"{'='*70}\n")
            f.write(f"프로젝트: {project_name}\n")
            f.write(f"공종: {r.input_name}\n")
            f.write(f"구분: {r.status}\n")
            if r.detail_code:
                f.write(f"KCS 코드: {r.detail_code} {r.detail_name}\n")
            f.write(f"{'='*70}\n\n")
            if r.error:
                f.write(f"[생성 오류] {r.error}\n")
            else:
                for s in (r.sections or []):
                    f.write(f"\n{'─'*60}\n")
                    f.write(f"[{s.title}]\n")
                    f.write(f"{'─'*60}\n")
                    f.write(s.body)
                    f.write("\n")

    # 요약 리포트
    ok = sum(1 for r in results if not r.error)
    std = sum(1 for r in results if r.status == "STANDARD")
    ref = sum(1 for r in results if r.status == "STANDARD_REF")
    spo = sum(1 for r in results if r.status == "SPEC_ONLY")
    ai_count = sum(
        1 for r in results
        if any(getattr(s, "is_ai", False) for s in (r.sections or []))
    )

    report_path = out_path / f"{project_name}_생성리포트_{date_str}.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"특기시방서 생성 리포트\n")
        f.write(f"프로젝트: {project_name}\n")
        f.write(f"생성일: {datetime.date.today().isoformat()}\n")
        f.write("=" * 60 + "\n")
        f.write(f"전체 공종: {len(results)}건\n")
        f.write(f"  표준시방서 원문 인용: {std}건\n")
        f.write(f"  근접 참조 + AI 초안: {ref}건\n")
        f.write(f"  AI 전체 초안:        {spo}건\n")
        f.write(f"  AI 초안 포함 항목:   {ai_count}건 (반드시 검수 필요)\n")
        f.write(f"  오류:                {len(results)-ok}건\n")
        f.write("=" * 60 + "\n\n")
        f.write("[AI 초안 포함 항목 목록 — 검수 대상]\n")
        for r in results:
            if any(getattr(s, "is_ai", False) for s in (r.sections or [])):
                f.write(f"  - {r.input_name} ({r.status})\n")
        f.write("\n[오류 항목]\n")
        for r in results:
            if r.error:
                f.write(f"  - {r.input_name}: {r.error}\n")

    print(f"\n저장 완료:")
    print(f"  {json_path}")
    print(f"  {preview_dir}/ ({len(results)}개 txt)")
    print(f"  {report_path}")
    return str(json_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("사용법: python3 spec_generator.py <공종목록.json> <출력폴더> [--project 프로젝트명] [--db DB경로]")
        sys.exit(1)

    work_list_path = sys.argv[1]
    out_dir = sys.argv[2]
    project_name = "건축공사"
    db_path = str(Path(__file__).parent / "output" / "specs.db")

    for i, arg in enumerate(sys.argv):
        if arg == "--project" and i + 1 < len(sys.argv):
            project_name = sys.argv[i + 1]
        if arg == "--db" and i + 1 < len(sys.argv):
            db_path = sys.argv[i + 1]

    with open(work_list_path, encoding="utf-8") as f:
        work_list = json.load(f)

    has_api = bool(os.environ.get("ANTHROPIC_API_KEY"))
    print(f"API 키: {'있음' if has_api else '없음 (STANDARD 항목만 원문 인용, 나머지는 안내문 삽입)'}")
    print(f"처리 대상: {len(work_list)}건\n")

    results = generate_all(work_list, db_path, project_name)
    save_results(results, out_dir, project_name)
