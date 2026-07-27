from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import traceback
import zipfile
from datetime import UTC, date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

APP_DIR = Path(os.environ.get("XUEJI_APP_DIR") or "/opt/xueji-loop")
DB_PATH = APP_DIR / "xueji_loop.db"
UPLOAD_DIR = APP_DIR / "uploads"
TZ_OFFSET = timedelta(hours=8)


def now_text() -> str:
    return (datetime.now(UTC) + TZ_OFFSET).strftime("%Y-%m-%d %H:%M:%S")


def next_monday(today: date | None = None) -> date:
    current = today or (datetime.now(UTC) + TZ_OFFSET).date()
    days_ahead = (7 - current.weekday()) % 7
    return current + timedelta(days=days_ahead or 7)


def normalize_question(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").lower())


def word_name(value: Any) -> str:
    return str(value or "").split(" /", 1)[0].strip().lower()


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def previous_plans(student_code: str, before: date, limit: int = 6) -> list[dict[str, Any]]:
    student_dir = UPLOAD_DIR / student_code
    plans = []
    for path in sorted(student_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        data = load_json(path)
        if not data or not isinstance(data.get("days"), list):
            continue
        try:
            start = date.fromisoformat(str(data.get("start_date")))
        except ValueError:
            start = date.min
        if start >= before:
            continue
        data["_file_name"] = path.name
        plans.append(data)
        if len(plans) >= limit:
            break
    return plans


def card_subject(card: dict[str, Any]) -> str:
    tag = str(card.get("tag") or "").strip()
    for subject in ("物理", "英语", "数学", "语文", "生物", "地理", "历史", "道法"):
        if tag.startswith(subject):
            return subject
    return ""


def quality_issues(plan: dict[str, Any], start_date: date, prior: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    if str(plan.get("start_date")) != start_date.isoformat():
        issues.append("start_date不等于下周一")
    days = plan.get("days")
    if not isinstance(days, list) or len(days) != 7:
        return [*issues, "必须正好7天"]

    prior_questions = [
        normalize_question(card.get("q"))
        for old in prior
        for day in old.get("days") or []
        for card in day.get("cards") or []
        if normalize_question(card.get("q"))
    ]
    prior_words = {
        word_name(item)
        for old in prior
        for day in old.get("days") or []
        for item in day.get("new_words") or []
        if word_name(item)
    }
    seen_questions: list[str] = []
    seen_words: set[str] = set()

    for day_index, day in enumerate(days, start=1):
        cards = day.get("cards") if isinstance(day, dict) else None
        if not isinstance(cards, list) or len(cards) != 8:
            issues.append(f"Day{day_index}必须正好8格")
            continue
        subjects = [card_subject(card) for card in cards]
        expected = {
            "物理": 2,
            "英语": 2,
            "数学": 1,
            "语文": 1,
        }
        for subject, count in expected.items():
            if subjects.count(subject) != count:
                issues.append(f"Day{day_index}{subject}必须{count}格")
        other_count = sum(subject in {"生物", "地理", "历史", "道法"} for subject in subjects)
        if other_count != 2:
            issues.append(f"Day{day_index}其它学科必须2格")
        if any(str(card.get("tag") or "").startswith("随手实验") for card in cards):
            issues.append(f"Day{day_index}不能安排实验格")

        targets = day.get("english_targets") or []
        if not isinstance(targets, list) or len(targets) != 4:
            issues.append(f"Day{day_index}英语点读目标必须4个")
        words = day.get("new_words") or []
        if not isinstance(words, list) or len(words) != 3:
            issues.append(f"Day{day_index}英语新词必须3个")
        for item in words if isinstance(words, list) else []:
            name = word_name(item)
            if not name:
                issues.append(f"Day{day_index}存在空英语词")
            elif name in prior_words or name in seen_words:
                issues.append(f"英语词重复：{name}")
            seen_words.add(name)

        for card in cards:
            question = normalize_question(card.get("q"))
            if not question:
                issues.append(f"Day{day_index}存在空问题")
                continue
            if question in seen_questions:
                issues.append(f"本周问题重复：{str(card.get('q'))[:28]}")
            elif any(SequenceMatcher(None, question, old).ratio() >= 0.92 for old in prior_questions):
                issues.append(f"与历史问题高度重复：{str(card.get('q'))[:28]}")
            seen_questions.append(question)
            if len(str(card.get("a") or "").strip()) < 8:
                issues.append(f"Day{day_index}答案过短：{str(card.get('tag'))[:20]}")
    return list(dict.fromkeys(issues))


def parse_model_plan(text: str) -> dict[str, Any] | None:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def compact_history(plans: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "plans": [
            {
                "title": plan.get("title"),
                "start_date": plan.get("start_date"),
                "daily_titles": [day.get("title") for day in plan.get("days") or []],
            }
            for plan in plans
        ],
        "used_questions": [
            card.get("q")
            for plan in plans
            for day in plan.get("days") or []
            for card in day.get("cards") or []
        ],
        "used_english_words": [
            item
            for plan in plans
            for day in plan.get("days") or []
            for item in day.get("new_words") or []
        ],
    }


def build_messages(
    server: Any,
    student: dict[str, Any],
    start_date: date,
    prior: list[dict[str, Any]],
    feedback: list[dict[str, Any]],
    voice: list[dict[str, Any]],
) -> list[dict[str, str]]:
    evidence = server.collect_learning_evidence_context(
        student,
        "八上暑假下一周：物理2格、英语2格、数学1格、语文1格、其它2格；无实验；连续故事；按考纲教材和知识库递进",
    )
    context = {
        "student": {
            "student_code": student.get("student_code"),
            "display_name": student.get("display_name"),
            "grade_region": student.get("grade_region"),
            "goal": student.get("goal"),
            "main_difficulties": student.get("main_difficulties"),
            "current_level": student.get("current_level"),
        },
        "next_week_start": start_date.isoformat(),
        "recent_feedback": feedback,
        "valid_voice_scores": voice,
        "history": compact_history(prior),
        "learning_evidence": evidence,
    }
    system = (
        "你是学记教育的周计划主编。为即将升八年级、基础需要稳步加强的学生制作暑假知识卡。"
        "内容必须基于教材递进、课程要求和已有知识库，使用有悬念的连续科普故事激发好奇，但事实必须准确。"
        "无反馈时不得虚构学生已经掌握或不会；语音0分且未识别到声音不算错题。"
        "只输出一个JSON对象，不要Markdown、解释或代码围栏。"
    )
    user = (
        "生成从下周一开始的7天知识卡。硬性结构：每天8格，物理2格、英语2格、数学1格、语文1格，"
        "生物/地理/历史/道法合计2格；不安排实验。物理和英语共享一条连续7章故事，知识点相对上周继续向八上教材下一台阶推进。"
        "英语每天恰好3个新词，格式为word /IPA/ 中文义；每天恰好4个english_targets，包含2个单词和2个短句，"
        "每项字段为target_type、target_text、target_meaning、scenario。不得重复history中的英语词或问题。"
        "数学只推进一个连续能力主题；语文练阅读、表达与证据；其它学科要与当天故事自然关联。"
        "8格是理解路径，不是8个全新知识点。每张卡只问一个点，答案使用准确短句，避免口号。"
        "JSON结构：{title,start_date,card_load,note,days:[{title,module,new_words,english_targets,cards:[{tag,q,a}]}]}。"
        "start_date必须原样使用next_week_start。以下是学生、历史内容与知识库上下文：\n"
        + json.dumps(context, ensure_ascii=False)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_run_table() -> None:
    with connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS weekly_generation_runs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL, student_code TEXT NOT NULL, "
            "week_start TEXT NOT NULL, status TEXT NOT NULL, version_code TEXT, file_url TEXT, "
            "quality_issues TEXT, error_text TEXT, created_at TEXT NOT NULL, finished_at TEXT, "
            "UNIQUE(student_id, week_start))"
        )
        conn.commit()


def record_run_start(student: dict[str, Any], week_start: date, force: bool) -> int | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, status FROM weekly_generation_runs WHERE student_id=? AND week_start=?",
            (student["id"], week_start.isoformat()),
        ).fetchone()
        if row and row["status"] == "success" and not force:
            return None
        if row:
            conn.execute(
                "UPDATE weekly_generation_runs SET status='running', quality_issues='', error_text='', "
                "created_at=?, finished_at=NULL WHERE id=?",
                (now_text(), row["id"]),
            )
            conn.commit()
            return int(row["id"])
        cur = conn.execute(
            "INSERT INTO weekly_generation_runs (student_id, student_code, week_start, status, created_at) "
            "VALUES (?, ?, ?, 'running', ?)",
            (student["id"], student["student_code"], week_start.isoformat(), now_text()),
        )
        conn.commit()
        return int(cur.lastrowid)


def record_run_end(run_id: int, status: str, **values: Any) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE weekly_generation_runs SET status=?, version_code=?, file_url=?, quality_issues=?, "
            "error_text=?, finished_at=? WHERE id=?",
            (
                status,
                values.get("version_code") or "",
                values.get("file_url") or "",
                json.dumps(values.get("quality_issues") or [], ensure_ascii=False),
                str(values.get("error_text") or "")[:2000],
                now_text(),
                run_id,
            ),
        )
        conn.commit()


def generate_docx(student_code: str, json_path: Path, docx_path: Path) -> None:
    generator = APP_DIR / "generate_cards_docx.py"
    result = subprocess.run(
        [sys.executable, str(generator), str(json_path), str(docx_path)],
        cwd=str(APP_DIR),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "DOCX generation failed")[:1000])
    with zipfile.ZipFile(docx_path) as archive:
        names = set(archive.namelist())
    if "[Content_Types].xml" not in names or "word/document.xml" not in names:
        raise RuntimeError("DOCX structure is incomplete")


def publish_plan(server: Any, student: dict[str, Any], plan: dict[str, Any], start_date: date) -> tuple[str, str]:
    student_code = str(student["student_code"])
    stamp = start_date.strftime("%Y%m%d")
    version_code = f"AUTO_{stamp}_每周智能续卡"
    json_name = f"{student_code}_{version_code}.json"
    docx_name = f"{student_code}_{version_code}.docx"
    student_dir = UPLOAD_DIR / student_code
    student_dir.mkdir(parents=True, exist_ok=True)
    json_path = student_dir / json_name
    docx_path = student_dir / docx_name
    json_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        generate_docx(student_code, json_path, docx_path)
    except Exception:
        json_path.unlink(missing_ok=True)
        docx_path.unlink(missing_ok=True)
        raise

    file_url = f"/xueji/uploads/{student_code}/{docx_name}"
    current = server.get_row("SELECT * FROM plan_versions WHERE id=?", (student.get("current_plan_version_id"),)) or {}
    now = now_text()
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM plan_versions WHERE student_id=? AND version_code=?",
            (student["id"], version_code),
        ).fetchone()
        values = (
            "weekly_auto",
            "每周智能续卡",
            "每天8格：物理2+英语2+数学1+语文1+其它2；无实验任务",
            "每周日自动生成 + 周一按日期切换 + 方舟英语慢速点读 + 有效低分回炉",
            f"系统依据近期反馈、有效跟读成绩、历史卡片和知识库自动生成{start_date.isoformat()}起7天计划。",
            str(plan.get("note") or "自动续卡计划"),
            "每天只问三句：今天哪里最奇怪？依据是什么？下一章会怎样？不抽背整页。",
            "published",
            file_url,
            now,
            now,
        )
        if existing:
            plan_id = int(existing["id"])
            conn.execute(
                "UPDATE plan_versions SET plan_type=?, level_name=?, card_load=?, execution_mode=?, reason=?, "
                "plan_summary=?, parent_instruction=?, status=?, file_url=?, published_at=?, updated_at=? WHERE id=?",
                (*values, plan_id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO plan_versions (student_id, version_code, plan_type, level_name, card_load, "
                "execution_mode, reason, plan_summary, parent_instruction, status, file_url, published_at, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (student["id"], version_code, *values[:-1], now, now),
            )
            plan_id = int(cur.lastrowid)
        conn.execute(
            "UPDATE students SET current_plan_version_id=?, current_level=?, current_card_load=?, "
            "execution_mode=?, updated_at=? WHERE id=?",
            (
                plan_id,
                "每周智能续卡",
                "每天8格：物理2+英语2+数学1+语文1+其它2；无实验任务",
                "每周日自动生成 + 周一按日期切换 + 方舟英语慢速点读 + 有效低分回炉",
                now,
                student["id"],
            ),
        )
        conn.commit()

    fresh = server.get_row("SELECT * FROM students WHERE id=?", (student["id"],))
    note = f"自动周计划 {start_date.isoformat()} 教材递进 历史去重 近期反馈 有效跟读 知识库证据"
    server.record_plan_evidence_refs(
        int(student["id"]),
        plan_id,
        server.collect_learning_evidence_context(fresh, note),
        note,
    )
    return version_code, file_url


def selected_students(codes: list[str]) -> list[dict[str, Any]]:
    with connect() as conn:
        if codes:
            placeholders = ",".join("?" for _ in codes)
            rows = conn.execute(
                f"SELECT * FROM students WHERE student_code IN ({placeholders}) ORDER BY id",
                codes,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM students WHERE current_plan_version_id IS NOT NULL ORDER BY id"
            ).fetchall()
    return [dict(row) for row in rows]


def student_context(student_id: int, week_start: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cutoff = (week_start - timedelta(days=14)).isoformat()
    with connect() as conn:
        feedback = [
            dict(row)
            for row in conn.execute(
                "SELECT feedback_date, planned_cards, completed_cards, known_cards, completion_rate, "
                "known_rate, star_card_codes, mood, parent_note FROM daily_feedback "
                "WHERE student_id=? AND feedback_date>=? ORDER BY feedback_date DESC",
                (student_id, cutoff),
            )
        ]
        voice = [
            dict(row)
            for row in conn.execute(
                "SELECT practice_date, target_text, transcript, score, status, feedback_text "
                "FROM voice_practice_sessions WHERE student_id=? AND practice_date>=? "
                "AND score BETWEEN 1 AND 100 AND TRIM(COALESCE(transcript, ''))<>'' "
                "ORDER BY id DESC LIMIT 30",
                (student_id, cutoff),
            )
        ]
    return feedback, voice


def generate_for_student(server: Any, student: dict[str, Any], week_start: date, force: bool) -> dict[str, Any]:
    run_id = record_run_start(student, week_start, force)
    if run_id is None:
        return {"student_code": student["student_code"], "status": "skipped", "reason": "already generated"}
    try:
        prior = previous_plans(str(student["student_code"]), week_start)
        feedback, voice = student_context(int(student["id"]), week_start)
        config = server.find_model_config("card_generation")
        api_key_name = str(config.get("api_key_env") or "")
        if not api_key_name or not os.environ.get(api_key_name):
            raise RuntimeError(f"model API key is missing: {api_key_name}")
        messages = build_messages(server, student, week_start, prior, feedback, voice)
        result = server.call_openai_compatible(config, messages, max_completion_tokens=9000)
        plan = parse_model_plan(result.get("content") or "")
        if not plan:
            raise RuntimeError("model did not return valid JSON")
        plan["start_date"] = week_start.isoformat()
        plan["card_load"] = "每天8格：物理2+英语2+数学1+语文1+其它2；无实验任务"
        issues = quality_issues(plan, week_start, prior)
        if issues:
            record_run_end(run_id, "quality_failed", quality_issues=issues)
            return {"student_code": student["student_code"], "status": "quality_failed", "issues": issues}
        version_code, file_url = publish_plan(server, student, plan, week_start)
        record_run_end(run_id, "success", version_code=version_code, file_url=file_url)
        return {
            "student_code": student["student_code"],
            "status": "success",
            "week_start": week_start.isoformat(),
            "version_code": version_code,
            "file_url": file_url,
        }
    except Exception as exc:
        record_run_end(run_id, "error", error_text=f"{exc}\n{traceback.format_exc()}")
        return {"student_code": student["student_code"], "status": "error", "error": str(exc)[:300]}


def preflight(server: Any, students: list[dict[str, Any]], week_start: date) -> dict[str, Any]:
    config = server.find_model_config("card_generation")
    key_name = str(config.get("api_key_env") or "")
    return {
        "status": "ok" if students and key_name and os.environ.get(key_name) else "not_ready",
        "week_start": week_start.isoformat(),
        "students": [student["student_code"] for student in students],
        "model_provider": config.get("provider_name"),
        "model_name": config.get("model_name"),
        "api_key_env": key_name,
        "api_key_configured": bool(os.environ.get(key_name)),
        "generator_exists": (APP_DIR / "generate_cards_docx.py").exists(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--week-start", default="")
    parser.add_argument("--student-code", action="append", default=[])
    args = parser.parse_args()

    sys.path.insert(0, str(APP_DIR))
    import server

    server.init_db()
    ensure_run_table()
    configured = [item.strip() for item in os.environ.get("XUEJI_WEEKLY_STUDENT_CODES", "").split(",") if item.strip()]
    codes = args.student_code or configured
    students = selected_students(codes)
    week_start = date.fromisoformat(args.week_start) if args.week_start else next_monday()
    if args.preflight:
        print(json.dumps(preflight(server, students, week_start), ensure_ascii=False))
        return 0

    results = [generate_for_student(server, student, week_start, args.force) for student in students]
    print(json.dumps({"week_start": week_start.isoformat(), "results": results}, ensure_ascii=False))
    return 0 if results and all(item["status"] in {"success", "skipped"} for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
