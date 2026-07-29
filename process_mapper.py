#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import re
import sqlite3
from dataclasses import dataclass
from typing import Optional

SYNONYM_MAP = {
    "RC": "콘크리트", "레미콘": "콘크리트", "무근": "무근콘크리트",
    "철근콘크리트": "콘크리트", "PC": "프리캐스트",
    "철골": "강구조", "스틸": "강구조", "H빔": "강구조",
    "데크": "데크플레이트", "내화피복": "내화피복", "내화뿜칠": "내화피복",
    "벽돌": "벽돌공사", "블록": "블록공사", "ALC": "ALC블록", "조적": "조적공사",
    "방수": "방수공사", "도막방수": "도막방수공사",
    "시트방수": "시트 방수공사", "우레탄": "도막방수공사",
    "실링": "실링공사", "코킹": "실링공사",
    "벤토나이트": "벤토나이트 방수공사",
    "단열": "단열공사", "외단열": "외단열 공사", "EIFS": "외단열 공사",
    "결로": "결로방지 단열공사",
    "미장": "미장공사", "모르타르": "시멘트 모르타르 바름",
    "에폭시": "합성고분자 바닥바름", "바닥강화": "바닥강화재 바름",
    "석재": "석공사", "화강석": "화강석 공사", "대리석": "대리석 공사",
    "건식석재": "건식 석재공사",
    "도장": "도장공사", "페인트": "도장공사",
    "타일": "타일공사",
    "창호": "창호공사", "창문": "창호공사",
    "유리": "유리공사", "커튼월": "커튼월 공사",
    "알루미늄창호": "알루미늄 합금제 창호공사",
    "PVC창호": "합성수지제 창호공사",
    "스테인리스창호": "스테인리스 스틸 창호공사",
    "지붕": "지붕공사", "기와": "기와", "싱글": "아스팔트 싱글",
    "금속지붕": "금속판 지붕",
    "수장": "수장공사", "도배": "도배공사", "천장": "천장공사",
    "마루": "바닥 공사", "온돌": "온돌공사", "난방": "온돌공사",
    "금속": "금속공사", "난간": "금속 현장제작품 공사",
    "목공": "목공사", "목재": "목공사",
    "방화": "방화 및 내화공사", "내화충전": "내화충전시스템공사",
    "방화구획": "내화충전시스템공사",
    "외벽": "외벽공사", "패널": "조립식 패널 외벽공사",
    "ALC패널": "ALC 패널 공사",
    "해체": "해체공사", "철거": "해체공사",
    "블라인드": "커튼 및 블라인드공사", "버티컬": "커튼 및 블라인드공사",
}

SPEC_ONLY_KEYWORDS = [
    "프로젝터", "빔프로젝터", "스크린", "음향", "조명기구",
    "가구", "붙박이장", "주방기구", "싱크대", "욕실기구",
    "거울설치", "액자", "사인", "간판",
    "엘리베이터", "에스컬레이터", "리프트",
    "소화기", "소화설비", "스프링클러",
    "CCTV", "자동문",
    "에어컨", "냉난방기", "FCU",
    "태양광", "ESS",
    "주차설비", "턴테이블",
]


@dataclass
class MappingResult:
    input_name: str
    status: str
    detail_code: Optional[str] = None
    detail_name: Optional[str] = None
    process_code: Optional[str] = None
    process_name: Optional[str] = None
    score: float = 0.0
    note: str = ""


class ProcessMapper:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._load_index()

    def _load_index(self):
        con = sqlite3.connect(self.db_path)
        rows = con.execute(
            "SELECT process_code, process_name, detail_code, detail_name FROM specs"
        ).fetchall()
        con.close()
        self.index = [
            {
                "process_code": r[0],
                "process_name": r[1] or "",
                "detail_code": r[2],
                "detail_name": r[3],
                "tokens": self._tokenize(f"{r[1] or ''} {r[3]}"),
            }
            for r in rows
        ]

    @staticmethod
    def _tokenize(text: str) -> set:
        text = re.sub(r"[^\w가-힣]", " ", text).lower()
        tokens = set(t for t in text.split() if len(t) > 1)
        joined = re.sub(r"\s+", "", text)
        for n in (2, 3, 4):
            for i in range(len(joined) - n + 1):
                tokens.add(joined[i:i+n])
        return tokens

    @staticmethod
    def _normalize(text: str) -> str:
        for alias, canonical in SYNONYM_MAP.items():
            text = re.sub(re.escape(alias), canonical, text, flags=re.IGNORECASE)
        return text

    def _score(self, query_tokens: set, item: dict) -> float:
        if not query_tokens or not item["tokens"]:
            return 0.0
        intersection = query_tokens & item["tokens"]
        weighted = sum(len(t) for t in intersection)
        total = sum(len(t) for t in query_tokens | item["tokens"])
        return weighted / total if total else 0.0

    def map(self, input_name: str, threshold_standard: float = 0.25,
            threshold_ref: float = 0.08) -> MappingResult:
        lower = input_name.lower()
        for kw in SPEC_ONLY_KEYWORDS:
            if kw.lower() in lower:
                return MappingResult(
                    input_name=input_name,
                    status="SPEC_ONLY",
                    note="KCS 표준시방서 해당 없음 -> 특기시방서(AI초안) 생성 대상",
                )

        normalized = self._normalize(input_name)
        query_tokens = self._tokenize(normalized)

        scored = sorted(
            [(self._score(query_tokens, item), idx) for idx, item in enumerate(self.index)],
            reverse=True
        )
        if not scored:
            return MappingResult(input_name=input_name, status="SPEC_ONLY",
                                 note="인덱스 없음")
        best_score, best_idx = scored[0]
        best = self.index[best_idx]

        if best_score < threshold_ref:
            return MappingResult(input_name=input_name, status="SPEC_ONLY",
                                 note="KCS 유사 항목 없음")
        elif best_score >= threshold_standard:
            return MappingResult(
                input_name=input_name, status="STANDARD",
                detail_code=best["detail_code"], detail_name=best["detail_name"],
                process_code=best["process_code"], process_name=best["process_name"],
                score=round(best_score, 3),
            )
        else:
            return MappingResult(
                input_name=input_name, status="STANDARD_REF",
                detail_code=best["detail_code"], detail_name=best["detail_name"],
                process_code=best["process_code"], process_name=best["process_name"],
                score=round(best_score, 3),
                note=f"근접 참조(유사도 {round(best_score,3)}) -> 특기시방서에서 구체화 필요",
            )

    def map_list(self, names):
        return [self.map(n) for n in names]

    def all_specs(self):
        return [
            {"process_code": item["process_code"], "process_name": item["process_name"],
             "detail_code": item["detail_code"], "detail_name": item["detail_name"]}
            for item in self.index
        ]

    def search(self, query: str, top_k: int = 10):
        normalized = self._normalize(query)
        query_tokens = self._tokenize(normalized)
        scored = sorted(
            [(self._score(query_tokens, item), idx) for idx, item in enumerate(self.index)],
            reverse=True
        )
        results = []
        for score, idx in scored[:top_k]:
            if score > 0:
                item = self.index[idx]
                results.append({**item, "score": round(score, 3)})
        return results


def _normalize_korean(text: str) -> str:
    """한글 낱자 사이 공백 제거: '가 설 공 사' → '가설공사'"""
    # 한글 한 글자씩 공백으로 분리된 패턴 정규화
    text = re.sub(r'(?<=[가-힣])\s(?=[가-힣])', '', text)
    return text.strip()


def _extract_from_sheet(ws) -> list:
    """시트에서 품명 컬럼을 찾아 공종명 목록 반환"""
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    header_keywords = ["공종", "품명", "항목", "공사명", "내용", "세목"]
    col_idx = None
    header_row = None

    for i, row in enumerate(rows[:15]):
        for j, cell in enumerate(row):
            if cell and any(kw in str(cell) for kw in header_keywords):
                header_row = i
                col_idx = j
                break
        if header_row is not None:
            break

    if col_idx is None:
        col_idx = 0

    names = []
    start = (header_row + 1) if header_row is not None else 0
    for row in rows[start:]:
        val = row[col_idx] if col_idx < len(row) else None
        if not val or not isinstance(val, str):
            continue
        val = val.strip()
        if len(val) < 2:
            continue
        # 코드+명칭 형태 파싱: "010101 공통 가설 공사" → "공통가설공사"
        code_match = re.match(r'^(\d{4,8})\s+(.+)$', val)
        if code_match:
            code, name = code_match.group(1), code_match.group(2)
            # 6자리 코드(공종 레벨)만 추출, 4자리(대공종) 또는 2자리(전체) 제외
            if len(code) == 6:
                names.append(_normalize_korean(name))
        else:
            names.append(_normalize_korean(val))

    return list(dict.fromkeys(n for n in names if len(n) > 1))


def extract_from_excel(file_path: str, detail: bool = False) -> list:
    """
    엑셀 내역서에서 공종명 추출.

    Parameters
    ----------
    file_path : str
        엑셀 파일 경로
    detail : bool
        True이면 '공종별내역서' 시트도 함께 참조 (세부 공종 추가)

    Returns
    -------
    list[str]  공종명 목록
    """
    try:
        import openpyxl
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        sheet_names = wb.sheetnames

        # 우선순위: 공종별집계표 > 공종별내역서 > active sheet
        SUMMARY_KEYWORDS = ["공종별집계표", "집계표"]
        DETAIL_KEYWORDS  = ["공종별내역서", "내역서"]

        def find_sheet(keywords):
            for kw in keywords:
                for sn in sheet_names:
                    if kw in sn:
                        return wb[sn]
            return None

        summary_ws = find_sheet(SUMMARY_KEYWORDS)
        detail_ws  = find_sheet(DETAIL_KEYWORDS) if detail else None

        if summary_ws:
            names = _extract_from_sheet(summary_ws)
            if detail and detail_ws:
                detail_names = _extract_from_sheet(detail_ws)
                # 세부 공종 추가 (중복 제외)
                existing = set(names)
                for n in detail_names:
                    if n not in existing:
                        names.append(n)
                        existing.add(n)
            return names

        # 집계표 시트를 못 찾은 경우 active sheet 사용
        return _extract_from_sheet(wb.active)

    except Exception:
        return []


def get_excel_sheets(file_path: str) -> list:
    """엑셀 파일의 시트 목록 반환"""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(file_path, read_only=True)
        return wb.sheetnames
    except Exception:
        return []


def extract_from_text(text: str):
    text = re.sub(r"\d+\.\s*", "", text)
    items = re.split(r"[,\n;]+", text)
    return [i.strip() for i in items if i.strip() and len(i.strip()) > 1]


if __name__ == "__main__":
    import sys, os
    db_path = sys.argv[1] if len(sys.argv) > 1 else "output/specs.db"
    if not os.path.exists(db_path):
        print(f"DB 없음: {db_path}")
        sys.exit(1)
    mapper = ProcessMapper(db_path)
    tests = ["철골공사", "콘크리트", "외단열", "도장", "커튼월",
             "타일", "방수공사", "창호", "거울설치", "프로젝터설치",
             "블라인드", "에폭시바닥", "내화충전", "해체공사"]
    print(f"{'입력':<20} {'상태':<14} {'코드':<16} {'매핑명'}")
    print("-" * 80)
    for name in tests:
        r = mapper.map(name)
        code = r.detail_code or "-"
        mname = r.detail_name if r.detail_name else r.note[:30]
        print(f"{name:<20} {r.status:<14} {code:<16} {mname}")
