#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
건축 표준시방서 JSON → SQLite DB 적재 스크립트
================================================

사용법:
    python3 build_db.py <output_폴더> [db_경로]

    예) python3 build_db.py ./output ./specs.db

테이블 구조:
    specs       - 파일 단위 메타데이터 + 섹션 텍스트
    sections    - 섹션 단위 (공정코드, 세부코드, 섹션명, 본문)
    tables      - 표 데이터 (hwp에서 추출된 경우)
    failed      - FAILED 파일 목록 (별도 추적용)
"""

import os
import sys
import json
import sqlite3
from pathlib import Path


def build_db(output_dir: str, db_path: str):
    index_path = os.path.join(output_dir, "index.json")
    by_process_dir = os.path.join(output_dir, "by_process")

    if not os.path.exists(index_path):
        print(f"오류: {index_path} 가 없습니다. parse_specs.py를 먼저 실행하세요.")
        sys.exit(1)

    with open(index_path, encoding="utf-8") as f:
        index = json.load(f)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS specs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            process_code    TEXT,
            process_name    TEXT,
            detail_code     TEXT,
            detail_name     TEXT,
            revision_status TEXT,
            source_file     TEXT UNIQUE,
            status          TEXT,
            error           TEXT,
            raw_text_length INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS sections (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            spec_id         INTEGER REFERENCES specs(id),
            process_code    TEXT,
            detail_code     TEXT,
            section_key     TEXT,   -- "1_일반사항" 형태
            section_no      TEXT,   -- "1"
            section_title   TEXT,   -- "일반사항"
            body            TEXT
        );

        CREATE TABLE IF NOT EXISTS spec_tables (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            spec_id         INTEGER REFERENCES specs(id),
            detail_code     TEXT,
            table_index     INTEGER,
            table_json      TEXT    -- JSON 직렬화된 표 데이터
        );

        CREATE TABLE IF NOT EXISTS failed (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            process_code    TEXT,
            process_name    TEXT,
            detail_code     TEXT,
            detail_name     TEXT,
            source_file     TEXT,
            error           TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_specs_detail_code ON specs(detail_code);
        CREATE INDEX IF NOT EXISTS idx_specs_process_code ON specs(process_code);
        CREATE INDEX IF NOT EXISTS idx_sections_detail_code ON sections(detail_code);
        CREATE INDEX IF NOT EXISTS idx_sections_section_key ON sections(section_key);
    """)

    # 이미 처리된 파일 목록
    cur.execute("SELECT source_file FROM specs")
    already = {r[0] for r in cur.fetchall()}

    ok_count = 0
    fail_count = 0
    skip_count = 0

    # by_process JSON에서 섹션/표 데이터까지 읽기
    for json_file in sorted(Path(by_process_dir).glob("*.json")):
        with open(json_file, encoding="utf-8") as f:
            records = json.load(f)

        for rec in records:
            src = rec.get("source_file", "")
            if src in already:
                skip_count += 1
                continue

            if rec["status"] == "FAILED":
                cur.execute("""
                    INSERT OR IGNORE INTO failed
                        (process_code, process_name, detail_code, detail_name, source_file, error)
                    VALUES (?,?,?,?,?,?)
                """, (
                    rec.get("process_code"), rec.get("process_name"),
                    rec.get("detail_code"), rec.get("detail_name"),
                    src, rec.get("error"),
                ))
                fail_count += 1
                continue

            cur.execute("""
                INSERT OR IGNORE INTO specs
                    (process_code, process_name, detail_code, detail_name,
                     revision_status, source_file, status, error, raw_text_length)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                rec.get("process_code"), rec.get("process_name"),
                rec.get("detail_code"), rec.get("detail_name"),
                rec.get("revision_status"), src,
                rec.get("status"), rec.get("error"),
                rec.get("raw_text_length", 0),
            ))
            spec_id = cur.lastrowid
            if spec_id is None:  # OR IGNORE가 충돌한 경우
                cur.execute("SELECT id FROM specs WHERE source_file=?", (src,))
                spec_id = cur.fetchone()[0]

            # 섹션 적재
            for sec_key, body in (rec.get("sections") or {}).items():
                parts = sec_key.split("_", 1)
                sec_no = parts[0] if parts else ""
                sec_title = parts[1] if len(parts) > 1 else sec_key
                cur.execute("""
                    INSERT INTO sections
                        (spec_id, process_code, detail_code, section_key,
                         section_no, section_title, body)
                    VALUES (?,?,?,?,?,?,?)
                """, (
                    spec_id,
                    rec.get("process_code"), rec.get("detail_code"),
                    sec_key, sec_no, sec_title, body,
                ))

            # 표 적재 (hwp에서 추출된 경우)
            for idx, tbl in enumerate(rec.get("tables") or []):
                cur.execute("""
                    INSERT INTO spec_tables
                        (spec_id, detail_code, table_index, table_json)
                    VALUES (?,?,?,?)
                """, (
                    spec_id, rec.get("detail_code"),
                    idx, json.dumps(tbl, ensure_ascii=False),
                ))

            ok_count += 1

    conn.commit()
    conn.close()

    total = ok_count + fail_count + skip_count
    print(f"DB 적재 완료: {db_path}")
    print(f"  적재 성공: {ok_count}건")
    print(f"  실패 기록: {fail_count}건 (failed 테이블)")
    print(f"  스킵(중복): {skip_count}건")
    print(f"  합계: {total}건")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python3 build_db.py <output_폴더> [db_경로]")
        sys.exit(1)
    out_dir = sys.argv[1]
    db_file = sys.argv[2] if len(sys.argv) > 2 else os.path.join(out_dir, "specs.db")
    build_db(out_dir, db_file)
