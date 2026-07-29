#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
건축 표준시방서(KCS, .hwp) 일괄 파싱 파이프라인
================================================

목적
----
공정별 폴더(KCS 코드 체계)로 정리된 .hwp 표준시방서 원문을
"공정 코드 - 세부 코드 - 표준 섹션(1.일반사항/2.자재/3.시공) - 표"
구조의 JSON으로 변환한다.

이 스크립트가 하는 일
--------------------
1. 루트 폴더를 스캔하여 폴더명에서 대공정 KCS 코드(예: "KCS 41 30 00")와
   대공정명(예: "콘크리트 공사")을 추출한다.
2. 각 .hwp 파일명에서 세부 KCS 코드와 세부공사명을 추출한다.
   ("(제정)", "(개정)" 접두어는 별도 status 필드로 분리한다.)
3. hwp5txt로 본문 텍스트를 추출하고, KCS 표준 목차 구조
   (1. 일반사항 / 2. 자재 / 3. 시공 등 최상위 장 번호)를 기준으로
   섹션을 분리한다.
4. hwp5html로 표(table) 데이터를 별도 추출한다. (자재 규격표, 허용오차표 등)
5. 실패하는 파일은 건너뛰지 않고 status=FAILED로 기록하여 후속 조치가
   가능하도록 한다. (관공서 납품용이므로 "누락"이 아니라 "실패 표시"가 원칙)
6. 결과를 대공정 폴더 단위 JSON + 전체 인덱스 JSON으로 출력한다.

사용법
------
    python3 parse_specs.py <원본_hwp_루트폴더> <출력폴더>

    예) python3 parse_specs.py ./spec_archive ./output

주의
----
- 이 스크립트는 "표준시방서 원문"을 있는 그대로 추출하는 용도이며,
  텍스트를 요약·재작성하지 않는다. (표준시방서는 원문 그대로 인용되어야
  법적 정확성이 보장되기 때문)
- 표/그림이 포함된 페이지는 <표>, <그림> 플레이스홀더가 본문에 남는다.
  표 내용은 별도 tables 필드에서 확인한다.
"""

import os
import re
import sys
import json
import subprocess
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("bs4(BeautifulSoup)가 필요합니다: pip install beautifulsoup4 lxml --break-system-packages")
    sys.exit(1)


# ---------------------------------------------------------------------------
# 1. 폴더명 / 파일명 파싱
# ---------------------------------------------------------------------------

# 폴더명 예: "KCS 41 30 00 콘크리트 공사"
FOLDER_CODE_RE = re.compile(r"^(KCS\s*\d{2}\s*\d{2}\s*\d{2})\s*(.*)$")

# 파일명 예: "(개정) KCS 41 30 02 무근콘크리트공사.hwp"
#           "KCS 41 30 01 건축물 콘크리트공사 일반.hwp"
FILE_STATUS_RE = re.compile(r"^\(?(제정|개정)\)?_?\s*")
FILE_CODE_RE = re.compile(r"(KCS[\s_]*\d{2}[\s_]*\d{2}[\s_]*\d{2})[\s_]*(.*)$")


def parse_folder(folder_name: str):
    m = FOLDER_CODE_RE.match(folder_name.strip())
    if m:
        code = re.sub(r"\s+", " ", m.group(1)).strip()
        title = m.group(2).strip()
        return code, title
    return None, folder_name.strip()


def parse_filename(filename: str):
    name = filename
    if name.lower().endswith((".hwp", ".pdf")):
        name = name[:-4]
    status_match = FILE_STATUS_RE.match(name)
    status = None
    if status_match:
        status = status_match.group(1)  # 제정 / 개정
        name = name[status_match.end():]
    m = FILE_CODE_RE.search(name)
    if m:
        code = re.sub(r"[\s_]+", " ", m.group(1)).strip()
        title = m.group(2).strip(" _")
        return code, title, status
    return None, name.strip(), status


# ---------------------------------------------------------------------------
# 2. 본문 텍스트 추출 (hwp5txt)
# ---------------------------------------------------------------------------

# KCS 표준시방서는 항상 최상위 장이 "N. 제목" 형태이고,
# 하위 절은 "N.N 제목", "N.N.N 제목"처럼 소수점이 붙는다.
# 최상위 장만 골라내려면 "숫자+ 마침표 + 공백" 뒤에 숫자가 다시 나오지
# 않는 패턴만 잡으면 된다.
TOP_CHAPTER_RE = re.compile(r"^(\d+)\.\s+(\S.*)$", re.MULTILINE)


def extract_text(src_path: str) -> tuple[str, Optional[str]]:
    """
    본문 텍스트 추출. 확장자에 따라 hwp5txt 또는 pdftotext를 사용한다.

    PDF 우회 경로 안내:
    hwp 파일 중 pyhwp가 파싱하지 못하는 최신 레코드 형식(개정이력/필드 등)이 있는 경우,
    한컴오피스에서 "다른 이름으로 저장 → PDF"로 export한 뒤 이 스크립트에 넣으면
    pdftotext(-layout)로 텍스트를 안정적으로 추출할 수 있다.
    PDF는 표준 개방형 포맷이라 텍스트/레이아웃 추출 라이브러리의 성숙도가 높아
    hwp 직접 파싱보다 오히려 안정적인 경우가 많다.
    """
    ext = os.path.splitext(src_path)[1].lower()
    try:
        if ext == ".pdf":
            proc = subprocess.run(
                ["pdftotext", "-layout", src_path, "-"],
                capture_output=True,
                timeout=60,
            )
        else:
            proc = subprocess.run(
                ["hwp5txt", src_path],
                capture_output=True,
                timeout=60,
            )
        text = proc.stdout.decode("utf-8", errors="replace")
        if proc.returncode != 0 or not text.strip():
            err = proc.stderr.decode("utf-8", errors="replace")[:300]
            return text, (err or "빈 결과 / 알 수 없는 오류")
        return text, None
    except Exception as e:  # noqa: BLE001
        return "", str(e)


TOC_LEADER_RE = re.compile(r"[·.\u2024\u2025\u2026]{3,}")  # 목차의 점선 리더(····) 패턴


def split_sections(raw_text: str) -> dict:
    """
    최상위 장(1. 일반사항 / 2. 자재 / 3. 시공 ...) 기준으로 텍스트를 분리한다.
    표/그림 플레이스홀더(<표>, <그림>)는 그대로 남겨 표 데이터와 대조 가능하게 한다.

    PDF에는 본문 앞에 "목차" 페이지가 있고, 거기의 "1. 일반사항 ······· 1"과
    같은 점선 리더 라인도 정규식상 최상위 장 패턴과 일치해버린다. 이를 실제
    장 제목으로 오인하지 않도록, 점선 리더가 포함된 줄은 후보에서 제외한다.
    """
    all_matches = list(TOP_CHAPTER_RE.finditer(raw_text))
    matches = [m for m in all_matches if not TOC_LEADER_RE.search(m.group(0))]
    if not matches:
        return {"미분류": raw_text.strip()}

    sections = {}
    for i, m in enumerate(matches):
        chapter_no = m.group(1)
        chapter_title = m.group(2).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_text)
        body = raw_text[start:end].strip()
        key = f"{chapter_no}_{chapter_title}"
        sections[key] = body
    return sections


# ---------------------------------------------------------------------------
# 3. 표(table) 추출 (hwp5html)
# ---------------------------------------------------------------------------

def extract_tables(hwp_path: str, tmp_dir: str) -> tuple[list, Optional[str]]:
    """hwp5html로 변환 후 표를 리스트[리스트[리스트[str]]] 형태로 추출."""
    out_dir = os.path.join(tmp_dir, "html_tmp")
    os.makedirs(out_dir, exist_ok=True)
    try:
        proc = subprocess.run(
            ["hwp5html", hwp_path, f"--output={out_dir}"],
            capture_output=True,
            timeout=90,
        )
        index_path = os.path.join(out_dir, "index.xhtml")
        if not os.path.exists(index_path):
            err = proc.stderr.decode("utf-8", errors="replace")[:300]
            return [], (err or "index.xhtml 생성 실패")

        with open(index_path, encoding="utf-8") as f:
            soup = BeautifulSoup(f.read(), "lxml")

        tables = []
        for t in soup.find_all("table"):
            rows = []
            for tr in t.find_all("tr"):
                cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                if any(cells):
                    rows.append(cells)
            if rows:
                tables.append(rows)
        return tables, None
    except Exception as e:  # noqa: BLE001
        return [], str(e)
    finally:
        # 임시 html 폴더 정리 (다음 파일 처리를 위해)
        import shutil
        shutil.rmtree(out_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4. 레코드 구조
# ---------------------------------------------------------------------------

@dataclass
class SpecRecord:
    process_code: Optional[str]        # 대공정 KCS 코드 (폴더명 기준)
    process_name: str                  # 대공정명 (폴더명 기준)
    detail_code: Optional[str]         # 세부 KCS 코드 (파일명 기준)
    detail_name: str                   # 세부공사명 (파일명 기준)
    revision_status: Optional[str]     # 제정 / 개정 / None
    source_file: str                   # 원본 파일 상대경로
    status: str                        # OK / FAILED
    error: Optional[str] = None
    sections: dict = field(default_factory=dict)
    tables: list = field(default_factory=list)
    raw_text_length: int = 0


def process_one_file(folder_path: str, filename: str, root: str, tmp_dir: str) -> SpecRecord:
    folder_name = os.path.basename(folder_path)
    process_code, process_name = parse_folder(folder_name)
    detail_code, detail_name, revision_status = parse_filename(filename)

    full_path = os.path.join(folder_path, filename)
    rel_path = os.path.relpath(full_path, root)

    raw_text, text_err = extract_text(full_path)
    is_pdf = filename.lower().endswith(".pdf")
    if is_pdf:
        # PDF는 pdftotext -layout이 표 내용을 정렬된 텍스트로 이미 보존하므로
        # 별도 표 구조화(hwp5html 방식)를 시도하지 않는다 — 강제로 표를 만들면
        # 오히려 셀이 깨져 정확도가 떨어진다. 표 내용은 sections 텍스트 안에 남아있다.
        tables, table_err = [], None
    else:
        tables, table_err = extract_tables(full_path, tmp_dir)

    if text_err and not raw_text.strip():
        return SpecRecord(
            process_code=process_code,
            process_name=process_name,
            detail_code=detail_code,
            detail_name=detail_name,
            revision_status=revision_status,
            source_file=rel_path,
            status="FAILED",
            error=f"본문추출실패: {text_err}",
        )

    sections = split_sections(raw_text)

    return SpecRecord(
        process_code=process_code,
        process_name=process_name,
        detail_code=detail_code,
        detail_name=detail_name,
        revision_status=revision_status,
        source_file=rel_path,
        status="OK",
        error=(f"표추출경고: {table_err}" if table_err else None),
        sections=sections,
        tables=tables,
        raw_text_length=len(raw_text),
    )


# ---------------------------------------------------------------------------
# 5. 메인 파이프라인
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 3:
        print("사용법: python3 parse_specs.py <원본_hwp_루트폴더> <출력폴더> [시작인덱스] [끝인덱스]")
        print("  대량 파일 처리 시 시간제한을 피하기 위해 배치(구간) 단위로 나눠 실행할 수 있습니다.")
        print("  예) python3 parse_specs.py ./spec_archive ./output 0 40")
        sys.exit(1)

    root = sys.argv[1]
    out_dir = sys.argv[2]
    batch_start = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    batch_end = int(sys.argv[4]) if len(sys.argv) > 4 else None

    os.makedirs(out_dir, exist_ok=True)
    tmp_dir = os.path.join(out_dir, "_tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    # 이전 배치 실행 결과가 있으면 이어서 누적한다 (재실행/부분실행 대비)
    index_path = os.path.join(out_dir, "index.json")
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = []
    already_done = {r["source_file"] for r in index}

    by_folder_dir = os.path.join(out_dir, "by_process")
    by_folder = {}
    if os.path.exists(by_folder_dir):
        for fn in os.listdir(by_folder_dir):
            if fn.endswith(".json"):
                with open(os.path.join(by_folder_dir, fn), encoding="utf-8") as f:
                    by_folder[fn[:-5]] = json.load(f)

    hwp_jobs = []
    for cur_root, dirs, files in os.walk(root):
        for fn in files:
            if fn.lower().endswith((".hwp", ".pdf")):
                hwp_jobs.append((cur_root, fn))
    hwp_jobs.sort()  # 배치 실행 간 순서 고정 (재현성)

    total = len(hwp_jobs)
    batch_end = total if batch_end is None else min(batch_end, total)
    batch_jobs = hwp_jobs[batch_start:batch_end]
    print(f"전체 {total}개 중 [{batch_start}:{batch_end}] 구간 {len(batch_jobs)}개 처리")

    for i, (folder_path, filename) in enumerate(batch_jobs, batch_start + 1):
        rel_check = os.path.relpath(os.path.join(folder_path, filename), root)
        if rel_check in already_done:
            print(f"[{i}/{total}] SKIP (이미 처리됨) | {filename}")
            continue
        record = process_one_file(folder_path, filename, root, tmp_dir)
        # 배치 실행 시 로드된 키(safe_name)와 새 레코드 키가 일치하도록 정규화
        folder_key = re.sub(r"[^\w가-힣]+", "_", os.path.basename(folder_path)).strip("_")
        by_folder.setdefault(folder_key, []).append(asdict(record))

        index.append({
            "process_code": record.process_code,
            "process_name": record.process_name,
            "detail_code": record.detail_code,
            "detail_name": record.detail_name,
            "revision_status": record.revision_status,
            "source_file": record.source_file,
            "status": record.status,
            "error": record.error,
        })

        mark = "OK " if record.status == "OK" else "FAIL"
        print(f"[{i}/{total}] {mark} | {record.detail_code or '?'} {record.detail_name}")

    # 폴더(대공정) 단위 JSON 저장 (folder_key는 이미 safe_name으로 정규화됨)
    specs_dir = os.path.join(out_dir, "by_process")
    os.makedirs(specs_dir, exist_ok=True)
    for folder_key, records in by_folder.items():
        with open(os.path.join(specs_dir, f"{folder_key}.json"), "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

    # 전체 인덱스 저장
    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    ok_count = sum(1 for r in index if r["status"] == "OK")
    fail_count = sum(1 for r in index if r["status"] == "FAILED")

    # 실패 목록만 별도 리포트 (관공서 납품 전 반드시 확인해야 할 목록)
    with open(os.path.join(out_dir, "FAILED_files_report.txt"), "w", encoding="utf-8") as f:
        f.write(f"전체 {total}개 중 실패 {fail_count}개\n")
        f.write("=" * 60 + "\n")
        for r in index:
            if r["status"] == "FAILED":
                f.write(f"- {r['source_file']}\n  사유: {r['error']}\n\n")

    print()
    print(f"완료: 성공 {ok_count} / 실패 {fail_count} / 전체 {total}")
    print(f"결과물: {out_dir}/index.json, {out_dir}/by_process/*.json, {out_dir}/FAILED_files_report.txt")

    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
