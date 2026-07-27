from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
import email.utils
import hashlib
import hmac
import io
import mimetypes
import os
import json
import re
import sqlite3
import secrets
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse
import urllib.error
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "xueji_loop.db"
UPLOAD_DIR = ROOT / "uploads"
HOST = "127.0.0.1"
PORT = 8765
TZ = timezone(timedelta(hours=8))
BASE_PREFIX = "/xueji"
ADMIN_KEY = os.environ.get("XUEJI_ADMIN_KEY", "")
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
DEFAULT_TRIAL_DAYS = int(os.environ.get("XUEJI_DEFAULT_TRIAL_DAYS", "7"))
DEFAULT_AI_CALL_LIMIT = int(os.environ.get("XUEJI_DEFAULT_AI_CALL_LIMIT", "20"))
TRIAL_STUDENT_LIMIT = int(os.environ.get("XUEJI_TRIAL_STUDENT_LIMIT", "1"))
TRIAL_INCLUDED_PLAN_COUNT = int(os.environ.get("XUEJI_TRIAL_INCLUDED_PLAN_COUNT", "2"))
DB_BUSY_TIMEOUT_MS = int(os.environ.get("XUEJI_DB_BUSY_TIMEOUT_MS", "8000"))
AI_RATE_WINDOW_SECONDS = int(os.environ.get("XUEJI_AI_RATE_WINDOW_SECONDS", "60"))
AI_RATE_LIMIT_PER_WINDOW = int(os.environ.get("XUEJI_AI_RATE_LIMIT_PER_WINDOW", "2"))
AI_MAX_CONCURRENT_JOBS = int(os.environ.get("XUEJI_AI_MAX_CONCURRENT_JOBS", "4"))
AI_EXECUTOR = ThreadPoolExecutor(max_workers=AI_MAX_CONCURRENT_JOBS)
AI_SUBMIT_LOCK = threading.Lock()
TTS_CACHE_RETENTION_DAYS = max(1, int(os.environ.get("XUEJI_TTS_CACHE_RETENTION_DAYS", "7")))
TTS_CLEANUP_INTERVAL_SECONDS = max(3600, int(os.environ.get("XUEJI_TTS_CLEANUP_INTERVAL_SECONDS", "86400")))
TTS_CLEANUP_LOCK = threading.Lock()
TTS_CLEANUP_STATE = {"last_at": 0.0}
XFYUN_ISE_DAILY_LIMIT_PER_STUDENT = max(0, int(os.environ.get("XFYUN_ISE_DAILY_LIMIT_PER_STUDENT", "5")))
XFYUN_ISE_DAILY_LIMIT_MAX_PER_STUDENT = max(
    XFYUN_ISE_DAILY_LIMIT_PER_STUDENT,
    int(os.environ.get("XFYUN_ISE_DAILY_LIMIT_MAX_PER_STUDENT", "12")),
)
REFERRAL_INVITER_REWARD = max(0, int(os.environ.get("XUEJI_REFERRAL_INVITER_REWARD", "5")))
REFERRAL_INVITEE_REWARD = max(0, int(os.environ.get("XUEJI_REFERRAL_INVITEE_REWARD", "3")))
REFERRAL_DAILY_REWARD_LIMIT = max(1, int(os.environ.get("XUEJI_REFERRAL_DAILY_REWARD_LIMIT", "3")))
REFERRAL_CODE_RE = re.compile(r"^[A-Z0-9]{6,16}$")
MAINLAND_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
WEAK_PASSWORDS = {
    "12345678",
    "123456789",
    "1234567890",
    "password1",
    "password123",
    "abc12345",
    "abcdefg1",
    "qwerty123",
    "88888888a",
}
TASK_LABELS = {
    "initial_plan": "初始学习规划",
    "daily_feedback": "每日反馈分析",
    "followup": "跟进建议",
    "weekly_adjustment": "周调整方案",
    "card_generation": "知识卡片生成",
}


def now_text() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def today_text() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def day_offset_from_timestamp(value: Any) -> int:
    text = compact_text(value)
    if not text:
        return 0
    for raw, fmt in ((text[:19], "%Y-%m-%d %H:%M:%S"), (text[:10], "%Y-%m-%d")):
        try:
            started = datetime.strptime(raw, fmt).date()
            return max((datetime.now(TZ).date() - started).days, 0)
        except ValueError:
            continue
    return 0


def parse_date_text(value: Any) -> date | None:
    text = compact_text(value)
    if not text:
        return None
    for raw, fmt in ((text[:10], "%Y-%m-%d"),):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def days_from_now_text(days: int) -> str:
    return (datetime.now(TZ) + timedelta(days=days)).strftime("%Y-%m-%d")


ENTITLEMENT_PRESETS = {
    "trial": {"label": "7天试用", "days": DEFAULT_TRIAL_DAYS, "usage_limit": DEFAULT_AI_CALL_LIMIT},
    "monthly": {"label": "月卡", "days": 30, "usage_limit": 120},
    "quarterly": {"label": "季卡", "days": 90, "usage_limit": 360},
    "annual": {"label": "年卡", "days": 365, "usage_limit": 1200},
}


def entitlement_presets_payload() -> list[dict[str, Any]]:
    return [{"plan_name": key, **value} for key, value in ENTITLEMENT_PRESETS.items()]


def apply_entitlement_preset(payload: dict[str, Any], parent: dict[str, Any]) -> tuple[str, str, str, int | None, int]:
    plan_name = compact_text(payload.get("plan_name")) or parent.get("plan_name") or "trial"
    status = compact_text(payload.get("status")) or parent.get("status") or "active"
    usage_used_raw = payload.get("usage_used")
    usage_used = int(parent.get("usage_used") or 0) if usage_used_raw in (None, "") else int(usage_used_raw)
    preset = ENTITLEMENT_PRESETS.get(plan_name)
    service_expires_at = compact_text(payload.get("service_expires_at")) or ""
    usage_limit_raw = payload.get("usage_limit")

    if preset and truthy(payload.get("apply_preset")):
        service_expires_at = days_from_now_text(int(preset["days"]))
        usage_limit = int(preset["usage_limit"])
        if truthy(payload.get("reset_usage_used")):
            usage_used = 0
    else:
        if service_expires_at:
            parse_date_time(service_expires_at)
        usage_limit = None if usage_limit_raw in (None, "") else int(usage_limit_raw)

    if service_expires_at:
        parse_date_time(service_expires_at)
    return plan_name, status, service_expires_at, usage_limit, max(usage_used, 0)


def parse_date_time(value: str | None) -> datetime | None:
    value = compact_text(value)
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=TZ)
        except ValueError:
            continue
    raise ValueError("日期格式应为 YYYY-MM-DD")


def json_dumps(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def compact_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return compact_text(value).lower() in {"1", "true", "yes", "y", "on", "是", "确认", "继续"}


def make_code(student_id: int) -> str:
    suffix = f"{student_id:04d}"
    return f"XJ-{datetime.now(TZ).strftime('%Y%m%d')}-{suffix}"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=DB_BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    UPLOAD_DIR.mkdir(exist_ok=True)
    with connect() as conn:
        conn.isolation_level = None
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS parents (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              phone TEXT,
              wechat_openid TEXT UNIQUE,
              nickname TEXT,
              password_hash TEXT,
              status TEXT NOT NULL DEFAULT 'active',
              plan_name TEXT DEFAULT 'trial',
              service_expires_at TEXT,
              usage_limit INTEGER DEFAULT 20,
              usage_used INTEGER DEFAULT 0,
              referral_code TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS parent_sessions (
              token TEXT PRIMARY KEY,
              parent_id INTEGER NOT NULL,
              created_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS parent_referrals (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              inviter_parent_id INTEGER NOT NULL,
              invitee_parent_id INTEGER NOT NULL UNIQUE,
              referral_code TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              qualified_at TEXT,
              reward_granted_at TEXT,
              reward_inviter_delta INTEGER DEFAULT 0,
              reward_invitee_delta INTEGER DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ai_quota_transactions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              parent_id INTEGER NOT NULL,
              delta INTEGER NOT NULL,
              balance_limit_after INTEGER,
              source_type TEXT NOT NULL,
              source_id INTEGER,
              reason TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS students (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              student_code TEXT UNIQUE,
              parent_id INTEGER,
              display_name TEXT,
              grade_region TEXT,
              goal TEXT,
              exam_date_text TEXT,
              current_level TEXT,
              current_card_load TEXT,
              execution_mode TEXT,
              main_difficulties TEXT,
              current_plan_version_id INTEGER,
              status TEXT NOT NULL DEFAULT 'active',
              notes TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS intake_submissions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              student_id INTEGER NOT NULL,
              source TEXT NOT NULL,
              grade_region TEXT,
              goal TEXT,
              exam_date_text TEXT,
              time_budget TEXT,
              cooperation TEXT,
              priority_subjects TEXT,
              difficulties TEXT,
              parent_support TEXT,
              evidence_summary TEXT,
              extra_notes TEXT,
              initial_level TEXT,
              initial_execution_mode TEXT,
              raw_payload TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS plan_versions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              student_id INTEGER NOT NULL,
              version_code TEXT NOT NULL,
              plan_type TEXT NOT NULL,
              level_name TEXT,
              card_load TEXT,
              execution_mode TEXT,
              reason TEXT,
              plan_summary TEXT,
              parent_instruction TEXT,
              status TEXT NOT NULL DEFAULT 'draft',
              file_url TEXT,
              published_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(student_id, version_code)
            );

            CREATE TABLE IF NOT EXISTS weekly_reports (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              student_id INTEGER NOT NULL,
              week_start TEXT NOT NULL,
              week_end TEXT NOT NULL,
              report_text TEXT,
              plan_version_id INTEGER,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS evidence_files (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              student_id INTEGER NOT NULL,
              feedback_id INTEGER,
              file_type TEXT NOT NULL,
              file_url TEXT NOT NULL,
              storage_path TEXT NOT NULL,
              original_name TEXT,
              description TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_feedback (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              student_id INTEGER NOT NULL,
              plan_version_id INTEGER,
              feedback_date TEXT NOT NULL,
              day_mode TEXT NOT NULL,
              planned_cards INTEGER NOT NULL DEFAULT 0,
              completed_cards INTEGER NOT NULL DEFAULT 0,
              known_cards INTEGER NOT NULL DEFAULT 0,
              completion_rate REAL,
              known_rate REAL,
              star_card_codes TEXT,
              mood TEXT,
              evidence_type TEXT,
              parent_note TEXT,
              raw_payload TEXT,
              created_at TEXT NOT NULL,
              UNIQUE(student_id, feedback_date)
            );

            CREATE TABLE IF NOT EXISTS adjustment_actions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              student_id INTEGER NOT NULL,
              feedback_id INTEGER,
              action_type TEXT NOT NULL,
              trigger_reason TEXT,
              action_text TEXT,
              priority TEXT DEFAULT 'normal',
              status TEXT NOT NULL DEFAULT 'pending',
              handled_at TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS model_configs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              task_type TEXT NOT NULL UNIQUE,
              provider_name TEXT NOT NULL,
              model_name TEXT NOT NULL,
              endpoint_url TEXT NOT NULL,
              api_key_env TEXT NOT NULL,
              temperature REAL DEFAULT 0.2,
              enabled INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ai_usage_logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              parent_id INTEGER,
              student_id INTEGER,
              task_type TEXT NOT NULL,
              model_config_id INTEGER,
              status TEXT NOT NULL,
              prompt_chars INTEGER DEFAULT 0,
              response_chars INTEGER DEFAULT 0,
              input_tokens INTEGER,
              output_tokens INTEGER,
              error_text TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ai_jobs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              parent_id INTEGER NOT NULL,
              student_id INTEGER NOT NULL,
              task_type TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'queued',
              note TEXT,
              result_text TEXT,
              usage_log_id INTEGER,
              error_text TEXT,
              created_at TEXT NOT NULL,
              started_at TEXT,
              finished_at TEXT
            );

            CREATE TABLE IF NOT EXISTS voice_practice_sessions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              student_id INTEGER NOT NULL,
              parent_id INTEGER,
              practice_date TEXT NOT NULL,
              target_type TEXT NOT NULL,
              target_text TEXT NOT NULL,
              target_meaning TEXT,
              scenario TEXT,
              transcript TEXT,
              score INTEGER DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'submitted',
              feedback_text TEXT,
              audio_url TEXT,
              raw_payload TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS student_access_tokens (
              token TEXT PRIMARY KEY,
              student_id INTEGER NOT NULL,
              parent_id INTEGER,
              label TEXT,
              enabled INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              last_seen_at TEXT
            );

            CREATE TABLE IF NOT EXISTS student_accounts (
              student_id INTEGER PRIMARY KEY,
              phone TEXT NOT NULL UNIQUE,
              password_hash TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_login_at TEXT
            );

            CREATE TABLE IF NOT EXISTS student_sessions (
              token TEXT PRIMARY KEY,
              student_id INTEGER NOT NULL,
              created_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS learning_resources (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL,
              source_type TEXT NOT NULL DEFAULT 'resource',
              subject TEXT,
              grade TEXT,
              region TEXT,
              textbook_version TEXT,
              authority_level TEXT DEFAULT 'reference',
              url TEXT,
              summary TEXT,
              tags TEXT,
              status TEXT NOT NULL DEFAULT 'active',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS learning_scope_index (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              stage TEXT NOT NULL,
              grade TEXT NOT NULL,
              grade_aliases TEXT,
              subject TEXT NOT NULL,
              subject_aliases TEXT,
              default_textbook_versions TEXT,
              resource_types TEXT,
              exam_focus TEXT,
              intervention_modes TEXT,
              status TEXT NOT NULL DEFAULT 'active',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(stage, grade, subject)
            );

            CREATE TABLE IF NOT EXISTS knowledge_points (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              resource_id INTEGER,
              subject TEXT NOT NULL,
              grade TEXT,
              textbook_version TEXT,
              unit_name TEXT,
              point_name TEXT NOT NULL,
              requirement_level TEXT,
              importance TEXT DEFAULT 'normal',
              common_errors TEXT,
              memory_hint TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS exam_patterns (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              knowledge_point_id INTEGER,
              resource_id INTEGER,
              subject TEXT NOT NULL,
              grade TEXT,
              region TEXT,
              exam_scope TEXT,
              year_text TEXT,
              question_type TEXT,
              frequency_level TEXT DEFAULT 'unknown',
              pattern_text TEXT NOT NULL,
              answer_points TEXT,
              source_url TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS plan_evidence_refs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              student_id INTEGER NOT NULL,
              plan_version_id INTEGER,
              evidence_type TEXT NOT NULL,
              evidence_id INTEGER NOT NULL,
              reason TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS learning_collection_tasks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              scope_index_id INTEGER,
              stage TEXT,
              grade TEXT,
              subject TEXT,
              coverage_level TEXT,
              task_type TEXT NOT NULL,
              task_text TEXT NOT NULL,
              priority TEXT NOT NULL DEFAULT 'normal',
              status TEXT NOT NULL DEFAULT 'todo',
              source_url TEXT,
              assignee TEXT,
              review_note TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              completed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_students_code ON students(student_code);
            CREATE INDEX IF NOT EXISTS idx_evidence_student ON evidence_files(student_id);
            CREATE INDEX IF NOT EXISTS idx_feedback_student_date ON daily_feedback(student_id, feedback_date);
            CREATE INDEX IF NOT EXISTS idx_adjust_status ON adjustment_actions(status);
            CREATE INDEX IF NOT EXISTS idx_weekly_reports_student ON weekly_reports(student_id);
            CREATE UNIQUE INDEX IF NOT EXISTS uk_parents_phone ON parents(phone) WHERE phone IS NOT NULL AND phone != '';
            CREATE INDEX IF NOT EXISTS idx_parent_sessions_parent ON parent_sessions(parent_id);
            CREATE INDEX IF NOT EXISTS idx_parent_referrals_inviter ON parent_referrals(inviter_parent_id);
            CREATE INDEX IF NOT EXISTS idx_parent_referrals_status ON parent_referrals(status);
            CREATE INDEX IF NOT EXISTS idx_ai_quota_transactions_parent ON ai_quota_transactions(parent_id);
            CREATE INDEX IF NOT EXISTS idx_ai_usage_parent ON ai_usage_logs(parent_id);
            CREATE INDEX IF NOT EXISTS idx_ai_usage_student ON ai_usage_logs(student_id);
            CREATE INDEX IF NOT EXISTS idx_ai_jobs_parent ON ai_jobs(parent_id);
            CREATE INDEX IF NOT EXISTS idx_ai_jobs_status ON ai_jobs(status);
            CREATE INDEX IF NOT EXISTS idx_voice_practice_student_date ON voice_practice_sessions(student_id, practice_date);
            CREATE INDEX IF NOT EXISTS idx_student_access_student ON student_access_tokens(student_id);
            CREATE INDEX IF NOT EXISTS idx_student_accounts_phone ON student_accounts(phone);
            CREATE INDEX IF NOT EXISTS idx_student_sessions_student ON student_sessions(student_id);
            CREATE INDEX IF NOT EXISTS idx_learning_resources_lookup ON learning_resources(subject, grade, region, textbook_version, status);
            CREATE INDEX IF NOT EXISTS idx_learning_scope_lookup ON learning_scope_index(stage, grade, subject, status);
            CREATE INDEX IF NOT EXISTS idx_knowledge_points_lookup ON knowledge_points(subject, grade, textbook_version, importance);
            CREATE INDEX IF NOT EXISTS idx_exam_patterns_lookup ON exam_patterns(subject, grade, region, frequency_level);
            CREATE INDEX IF NOT EXISTS idx_plan_evidence_plan ON plan_evidence_refs(plan_version_id);
            CREATE INDEX IF NOT EXISTS idx_collection_tasks_status ON learning_collection_tasks(status, priority);
            """
        )
        ensure_column(conn, "parents", "password_hash", "password_hash TEXT")
        ensure_column(conn, "parents", "status", "status TEXT NOT NULL DEFAULT 'active'")
        ensure_column(conn, "parents", "plan_name", "plan_name TEXT DEFAULT 'trial'")
        ensure_column(conn, "parents", "service_expires_at", "service_expires_at TEXT")
        ensure_column(conn, "parents", "usage_limit", "usage_limit INTEGER DEFAULT 20")
        ensure_column(conn, "parents", "usage_used", "usage_used INTEGER DEFAULT 0")
        ensure_column(conn, "parents", "referral_code", "referral_code TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uk_parents_referral_code ON parents(referral_code) WHERE referral_code IS NOT NULL AND referral_code != ''"
        )
        ensure_column(conn, "student_accounts", "status", "status TEXT NOT NULL DEFAULT 'active'")
        ensure_column(conn, "student_accounts", "created_at", "created_at TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "student_accounts", "updated_at", "updated_at TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "student_accounts", "last_login_at", "last_login_at TEXT")
        ensure_column(conn, "plan_versions", "published_at", "published_at TEXT")
        ensure_column(conn, "voice_practice_sessions", "assessment_provider", "assessment_provider TEXT")
        ensure_column(conn, "voice_practice_sessions", "assessment_available", "assessment_available INTEGER DEFAULT 0")
        ensure_column(conn, "learning_resources", "status", "status TEXT NOT NULL DEFAULT 'active'")
        ensure_column(conn, "learning_collection_tasks", "source_url", "source_url TEXT")
        ensure_column(conn, "learning_collection_tasks", "assignee", "assignee TEXT")
        ensure_column(conn, "learning_collection_tasks", "review_note", "review_note TEXT")
        ensure_column(conn, "learning_collection_tasks", "completed_at", "completed_at TEXT")
        seed_learning_resource_sources(conn)
        seed_k12_learning_scope_index(conn)
        conn.execute(
            """
            UPDATE ai_jobs
            SET status='error', error_text='服务重启，任务未完成，请重新生成。', finished_at=?
            WHERE status IN ('queued', 'running')
            """,
            (now_text(),),
        )
        conn.commit()


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def seed_learning_resource_sources(conn: sqlite3.Connection) -> None:
    now = now_text()
    seeds = [
        {
            "title": "国家中小学智慧教育平台",
            "source_type": "official_platform",
            "subject": "全科",
            "grade": "小学/初中/高中",
            "region": "全国",
            "textbook_version": "",
            "authority_level": "official",
            "url": "https://basic.smartedu.cn/",
            "summary": "官方中小学课程教学、专题教育和学习资源入口；用于确认课程范围和推荐学习资源，不直接复制整站内容。",
            "tags": "官方,中小学,课程,资源,云课堂",
        },
        {
            "title": "国家智慧教育公共服务平台",
            "source_type": "official_platform",
            "subject": "全科",
            "grade": "小学/初中/高中",
            "region": "全国",
            "textbook_version": "",
            "authority_level": "official",
            "url": "https://www.smartedu.cn/",
            "summary": "国家智慧教育总入口；用于追踪官方教育资源体系和公共服务入口。",
            "tags": "官方,智慧教育,公共服务",
        },
        {
            "title": "教育部义务教育课程方案和课程标准（2022年版）",
            "source_type": "curriculum_standard",
            "subject": "全科",
            "grade": "义务教育",
            "region": "全国",
            "textbook_version": "",
            "authority_level": "official",
            "url": "http://www.moe.gov.cn/srcsite/A26/s8001/202204/t20220420_619921.html",
            "summary": "义务教育阶段课程方案和各学科课程标准来源；用于判断知识点是否属于课标要求和学习目标层级。",
            "tags": "官方,课标,义务教育,2022",
        },
        {
            "title": "人民教育出版社教材电子版",
            "source_type": "textbook_reference",
            "subject": "语文/数学/英语/物理/化学/生物/历史/地理",
            "grade": "小学/初中/高中",
            "region": "全国",
            "textbook_version": "人教版",
            "authority_level": "publisher",
            "url": "https://jc.pep.com.cn/",
            "summary": "人教版教材电子版官方入口；用于核对章节、单元和教材版本，不把教材全文公开给学生端。",
            "tags": "教材,人教版,电子教材,出版社",
        },
    ]
    for item in seeds:
        existing = conn.execute(
            "SELECT id FROM learning_resources WHERE title=? AND url=?",
            (item["title"], item["url"]),
        ).fetchone()
        if existing:
            continue
        conn.execute(
            """
            INSERT INTO learning_resources (
              title, source_type, subject, grade, region, textbook_version,
              authority_level, url, summary, tags, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                item["title"], item["source_type"], item["subject"], item["grade"], item["region"],
                item["textbook_version"], item["authority_level"], item["url"], item["summary"],
                item["tags"], now, now,
            ),
        )


def seed_k12_learning_scope_index(conn: sqlite3.Connection) -> None:
    now = now_text()
    primary_subjects = ["语文", "数学", "英语", "科学", "道德与法治", "信息科技", "劳动", "体育", "艺术"]
    junior_base = ["语文", "数学", "英语", "道德与法治", "历史", "地理", "生物", "信息科技", "体育", "艺术"]
    senior_subjects = ["语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史", "地理", "信息技术", "通用技术", "体育", "艺术"]
    rows: list[dict[str, str]] = []

    def add(stage: str, grade: str, subject: str, aliases: str, exam_focus: str, modes: str) -> None:
        rows.append(
            {
                "stage": stage,
                "grade": grade,
                "grade_aliases": aliases,
                "subject": subject,
                "subject_aliases": {
                    "道德与法治": "道法,政治",
                    "政治": "思想政治,道法",
                    "信息科技": "信息技术,信息",
                    "信息技术": "信息科技,信息",
                    "艺术": "音乐,美术",
                    "科学": "自然,实验",
                }.get(subject, subject),
                "default_textbook_versions": "版本待确认,人教版优先核对",
                "resource_types": "课标,教材目录,官方课程,地区真题,老师整理考频,学生错题",
                "exam_focus": exam_focus,
                "intervention_modes": modes,
            }
        )

    primary_grades = ["一年级", "二年级", "三年级", "四年级", "五年级", "六年级"]
    for grade in primary_grades:
        aliases = grade + "," + grade.replace("年级", "") + "年级上," + grade.replace("年级", "") + "年级下"
        for subject in primary_subjects:
            focus = "基础概念、课内核心、阅读表达、计算与实践；以低负担复习和习惯建立为主"
            modes = "短卡片,口头复述,错题回炉,亲子抽问,打印练习"
            add("小学", grade, subject, aliases, focus, modes)

    junior_terms = ["七上", "七下", "八上", "八下", "九上", "九下"]
    for grade in junior_terms:
        subjects = list(junior_base)
        if grade.startswith("八") or grade.startswith("九"):
            subjects.append("物理")
        if grade.startswith("九"):
            subjects.append("化学")
        aliases = grade + "," + grade[0] + "年级" + ("上" if grade.endswith("上") else "下") + "," + grade[0] + "年级"
        for subject in subjects:
            focus = "期中/期末/中考衔接高频考点、题型模板、易错点、教材章节定位"
            modes = "知识卡,题型卡,错因卡,D1/D3/D7复背,英语听说,考前冲刺"
            add("初中", grade, subject, aliases, focus, modes)

    senior_terms = ["高一上", "高一下", "高二上", "高二下", "高三上", "高三下"]
    for grade in senior_terms:
        aliases = grade + "," + grade[:2] + "," + grade[:2] + "年级"
        for subject in senior_subjects:
            focus = "学业水平/高考衔接核心考点、模块化专题、题型模型、错题归因"
            modes = "专题卡,题型步骤卡,错题变式,阶段诊断,周计划调整"
            add("高中", grade, subject, aliases, focus, modes)

    for item in rows:
        conn.execute(
            """
            INSERT INTO learning_scope_index (
              stage, grade, grade_aliases, subject, subject_aliases,
              default_textbook_versions, resource_types, exam_focus,
              intervention_modes, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(stage, grade, subject) DO UPDATE SET
              grade_aliases=excluded.grade_aliases,
              subject_aliases=excluded.subject_aliases,
              default_textbook_versions=excluded.default_textbook_versions,
              resource_types=excluded.resource_types,
              exam_focus=excluded.exam_focus,
              intervention_modes=excluded.intervention_modes,
              status='active',
              updated_at=excluded.updated_at
            """,
            (
                item["stage"], item["grade"], item["grade_aliases"], item["subject"], item["subject_aliases"],
                item["default_textbook_versions"], item["resource_types"], item["exam_focus"],
                item["intervention_modes"], now, now,
            ),
        )


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def list_rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [row_to_dict(row) for row in conn.execute(sql, params).fetchall()]  # type: ignore[list-item]


def get_row(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with connect() as conn:
        return row_to_dict(conn.execute(sql, params).fetchone())


def normalize_phone(phone: Any) -> str:
    phone = compact_text(phone)
    phone = re.sub(r"[\s\-()（）]", "", phone)
    if phone.startswith("+86"):
        phone = phone[3:]
    elif phone.startswith("0086"):
        phone = phone[4:]
    elif phone.startswith("86") and len(phone) == 13 and phone[2] == "1":
        phone = phone[2:]
    return phone


def validate_mainland_mobile(phone: str, label: str = "手机号") -> str:
    phone = normalize_phone(phone)
    if not phone:
        raise ValueError(f"请输入{label}")
    if not MAINLAND_MOBILE_RE.fullmatch(phone):
        raise ValueError(f"请输入有效的中国大陆11位{label}")
    return phone


def validate_password_strength(password: str, phone: str = "", label: str = "密码") -> str:
    password = compact_text(password)
    if not password:
        raise ValueError(f"请输入{label}")
    if len(password) < 8:
        raise ValueError(f"{label}至少8位")
    if len(password) > 64:
        raise ValueError(f"{label}不能超过64位")
    if re.search(r"\s", password):
        raise ValueError(f"{label}不能包含空格")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValueError(f"{label}需同时包含字母和数字")
    if len(set(password.lower())) <= 2 or password.lower() in WEAK_PASSWORDS:
        raise ValueError(f"{label}过于简单，请换一个")
    if phone and password == phone:
        raise ValueError(f"{label}不能和手机号相同")
    return password


def make_password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), 180_000)
    return f"pbkdf2_sha256$180000${salt}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, rounds_text, salt, digest = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        rounds = int(rounds_text)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), rounds).hex()
        return secrets.compare_digest(candidate, digest)
    except ValueError:
        return False


def normalize_referral_code(value: Any) -> str:
    code = re.sub(r"[^0-9A-Za-z]", "", compact_text(value)).upper()
    return code[:16]


def ensure_referral_code_in_conn(conn: sqlite3.Connection, parent_id: int) -> str:
    row = conn.execute("SELECT referral_code FROM parents WHERE id=?", (parent_id,)).fetchone()
    if not row:
        raise ValueError("家长账号不存在")
    current = normalize_referral_code(row["referral_code"])
    if current:
        return current
    for _ in range(12):
        code = secrets.token_urlsafe(8).replace("-", "").replace("_", "").upper()[:8]
        if not REFERRAL_CODE_RE.fullmatch(code):
            continue
        exists = conn.execute("SELECT id FROM parents WHERE referral_code=?", (code,)).fetchone()
        if exists:
            continue
        conn.execute("UPDATE parents SET referral_code=?, updated_at=? WHERE id=?", (code, now_text(), parent_id))
        return code
    raise RuntimeError("无法生成唯一邀请码")


def ensure_parent_referral_code(parent_id: int) -> str:
    with connect() as conn:
        code = ensure_referral_code_in_conn(conn, parent_id)
        conn.commit()
        return code


def referral_link_path(code: str) -> str:
    return f"{BASE_PREFIX}/?ref={quote(code)}"


def record_parent_referral(conn: sqlite3.Connection, invitee_parent_id: int, raw_code: Any) -> None:
    code = normalize_referral_code(raw_code)
    if not code or not REFERRAL_CODE_RE.fullmatch(code):
        return
    inviter = conn.execute("SELECT id, phone FROM parents WHERE referral_code=?", (code,)).fetchone()
    invitee = conn.execute("SELECT id, phone FROM parents WHERE id=?", (invitee_parent_id,)).fetchone()
    if not inviter or not invitee:
        return
    inviter_id = int(inviter["id"])
    if inviter_id == invitee_parent_id:
        return
    if normalize_phone(inviter["phone"]) and normalize_phone(inviter["phone"]) == normalize_phone(invitee["phone"]):
        return
    existing = conn.execute("SELECT id FROM parent_referrals WHERE invitee_parent_id=?", (invitee_parent_id,)).fetchone()
    if existing:
        return
    created = now_text()
    conn.execute(
        """
        INSERT INTO parent_referrals (
          inviter_parent_id, invitee_parent_id, referral_code, status, created_at, updated_at
        )
        VALUES (?, ?, ?, 'pending', ?, ?)
        """,
        (inviter_id, invitee_parent_id, code, created, created),
    )


def add_ai_quota_transaction(
    conn: sqlite3.Connection,
    parent_id: int,
    delta: int,
    source_type: str,
    source_id: int | None,
    reason: str,
) -> int | None:
    if delta == 0:
        return None
    parent = conn.execute("SELECT usage_limit FROM parents WHERE id=?", (parent_id,)).fetchone()
    if not parent:
        raise ValueError("家长账号不存在")
    if parent["usage_limit"] is None:
        balance = None
    else:
        balance = max(int(parent["usage_limit"] or 0) + delta, 0)
        conn.execute("UPDATE parents SET usage_limit=?, updated_at=? WHERE id=?", (balance, now_text(), parent_id))
    cur = conn.execute(
        """
        INSERT INTO ai_quota_transactions (
          parent_id, delta, balance_limit_after, source_type, source_id, reason, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (parent_id, delta, balance, source_type, source_id, reason, now_text()),
    )
    return int(cur.lastrowid)


def grant_referral_reward_if_qualified(conn: sqlite3.Connection, invitee_parent_id: int) -> dict[str, Any] | None:
    referral = conn.execute(
        """
        SELECT *
        FROM parent_referrals
        WHERE invitee_parent_id=? AND status IN ('pending', 'qualified')
        ORDER BY id
        LIMIT 1
        """,
        (invitee_parent_id,),
    ).fetchone()
    if not referral:
        return None
    referral_id = int(referral["id"])
    inviter_id = int(referral["inviter_parent_id"])
    daily_count = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM parent_referrals
            WHERE inviter_parent_id=? AND status='granted' AND substr(reward_granted_at, 1, 10)=?
            """,
            (inviter_id, today_text()),
        ).fetchone()[0]
    )
    now = now_text()
    if daily_count >= REFERRAL_DAILY_REWARD_LIMIT:
        conn.execute(
            """
            UPDATE parent_referrals
            SET status='qualified', qualified_at=COALESCE(qualified_at, ?), updated_at=?
            WHERE id=?
            """,
            (now, now, referral_id),
        )
        return {"status": "qualified", "reason": "daily_limit", "referral_id": referral_id}

    inviter_delta = REFERRAL_INVITER_REWARD
    invitee_delta = REFERRAL_INVITEE_REWARD
    add_ai_quota_transaction(conn, inviter_id, inviter_delta, "referral_inviter", referral_id, "邀请家长完成建档奖励")
    add_ai_quota_transaction(conn, invitee_parent_id, invitee_delta, "referral_invitee", referral_id, "通过邀请注册并完成建档奖励")
    conn.execute(
        """
        UPDATE parent_referrals
        SET status='granted',
            qualified_at=COALESCE(qualified_at, ?),
            reward_granted_at=?,
            reward_inviter_delta=?,
            reward_invitee_delta=?,
            updated_at=?
        WHERE id=?
        """,
        (now, now, inviter_delta, invitee_delta, now, referral_id),
    )
    return {
        "status": "granted",
        "referral_id": referral_id,
        "inviter_delta": inviter_delta,
        "invitee_delta": invitee_delta,
    }


def parent_referral_summary(parent_id: int) -> dict[str, Any]:
    code = ensure_parent_referral_code(parent_id)
    rows = list_rows(
        """
        SELECT r.id, r.status, r.reward_inviter_delta, r.reward_invitee_delta,
               r.created_at, r.qualified_at, r.reward_granted_at,
               p.phone AS invitee_phone, p.nickname AS invitee_nickname
        FROM parent_referrals r
        LEFT JOIN parents p ON p.id = r.invitee_parent_id
        WHERE r.inviter_parent_id=?
        ORDER BY r.id DESC
        LIMIT 20
        """,
        (parent_id,),
    )
    stats = get_row(
        """
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
          SUM(CASE WHEN status='qualified' THEN 1 ELSE 0 END) AS qualified,
          SUM(CASE WHEN status='granted' THEN 1 ELSE 0 END) AS granted,
          COALESCE(SUM(reward_inviter_delta), 0) AS earned
        FROM parent_referrals
        WHERE inviter_parent_id=?
        """,
        (parent_id,),
    ) or {}
    return {
        "referral_code": code,
        "invite_path": referral_link_path(code),
        "reward": {
            "inviter": REFERRAL_INVITER_REWARD,
            "invitee": REFERRAL_INVITEE_REWARD,
            "daily_limit": REFERRAL_DAILY_REWARD_LIMIT,
        },
        "stats": {
            "total": int(stats.get("total") or 0),
            "pending": int(stats.get("pending") or 0),
            "qualified": int(stats.get("qualified") or 0),
            "granted": int(stats.get("granted") or 0),
            "earned": int(stats.get("earned") or 0),
        },
        "items": rows,
    }


def public_parent(parent: dict[str, Any]) -> dict[str, Any]:
    usage_limit = parent.get("usage_limit")
    usage_used = parent.get("usage_used") or 0
    expires_at = parent.get("service_expires_at") or ""
    trial = is_trial_parent(parent)
    referral_code = normalize_referral_code(parent.get("referral_code")) or ensure_parent_referral_code(int(parent["id"]))
    return {
        "parent_id": parent["id"],
        "phone": parent.get("phone") or "",
        "nickname": parent.get("nickname") or "",
        "status": parent.get("status") or "active",
        "plan_name": parent.get("plan_name") or "trial",
        "service_expires_at": expires_at,
        "usage_limit": usage_limit,
        "usage_used": usage_used,
        "usage_remaining": None if usage_limit is None else max(int(usage_limit) - int(usage_used), 0),
        "is_expired": is_parent_expired(parent),
        "is_trial": trial,
        "referral_code": referral_code,
        "referral_invite_path": referral_link_path(referral_code),
        "trial_student_limit": TRIAL_STUDENT_LIMIT,
        "trial_included_plan_count": TRIAL_INCLUDED_PLAN_COUNT,
        "trial_note": "7天试用包含1个学生建档、V1试运行方案、每日反馈和1次V2调整报告。",
    }


def is_trial_parent(parent: dict[str, Any] | None) -> bool:
    if not parent:
        return False
    return (parent.get("plan_name") or "trial") == "trial"


def is_paid_parent(parent: dict[str, Any] | None) -> bool:
    return bool(parent) and not is_trial_parent(parent)


def is_parent_expired(parent: dict[str, Any]) -> bool:
    expires_at = parse_date_time(parent.get("service_expires_at"))
    if not expires_at:
        return False
    return datetime.now(TZ) > expires_at.replace(hour=23, minute=59, second=59)


def ensure_parent_service(parent: dict[str, Any]) -> None:
    if parent.get("status") != "active":
        raise ValueError("帐号不可用，请联系老师")
    if is_parent_expired(parent):
        raise ValueError("帐号已到期，请联系老师续费")


def ensure_can_create_student(parent_id: int) -> None:
    parent = get_row("SELECT * FROM parents WHERE id=?", (parent_id,))
    if not parent or not is_trial_parent(parent):
        return
    count = int(get_row("SELECT COUNT(*) AS count FROM students WHERE parent_id=?", (parent_id,))["count"])  # type: ignore[index]
    if count >= TRIAL_STUDENT_LIMIT:
        raise ValueError("试用期可建档1个学生。添加第二个学生需要开通正式会员。")


def plan_sequence_number(version_code: Any) -> int | None:
    match = re.match(r"V(\d+)", compact_text(version_code))
    return int(match.group(1)) if match else None


def is_plan_included_for_trial(plan: dict[str, Any] | None) -> bool:
    if not plan:
        return True
    seq = plan_sequence_number(plan.get("version_code"))
    if seq is not None:
        return seq <= TRIAL_INCLUDED_PLAN_COUNT
    return int(plan.get("id") or 0) <= TRIAL_INCLUDED_PLAN_COUNT


def trial_upgrade_message() -> str:
    return "试用已包含V1试运行和1次V2调整报告。继续查看V3及后续周计划、长期跟踪或新增学生，请开通正式会员。"


def apply_plan_entitlement(plan: dict[str, Any] | None, parent: dict[str, Any] | None) -> dict[str, Any] | None:
    if not plan:
        return plan
    result = dict(plan)
    locked = bool(parent and is_trial_parent(parent) and not is_plan_included_for_trial(result))
    result["is_locked"] = locked
    result["upgrade_required"] = locked
    result["upgrade_message"] = trial_upgrade_message() if locked else ""
    if locked:
        result["file_url"] = ""
        result["plan_summary"] = "这份报告属于持续跟踪服务，开通正式会员后可查看完整内容。"
        result["parent_instruction"] = trial_upgrade_message()
    return result


def ensure_can_create_plan_for_parent(student_id: int, parent_id: int | None) -> None:
    if parent_id is None:
        return
    parent = get_row("SELECT * FROM parents WHERE id=?", (parent_id,))
    if not parent or not is_trial_parent(parent):
        return
    count = int(get_row("SELECT COUNT(*) AS count FROM plan_versions WHERE student_id=?", (student_id,))["count"])  # type: ignore[index]
    if count >= TRIAL_INCLUDED_PLAN_COUNT:
        raise ValueError("试用期已包含V1和1次V2调整报告。继续生成后续报告需要开通正式会员。")


def ensure_ai_quota(parent: dict[str, Any]) -> None:
    ensure_parent_service(parent)
    usage_limit = parent.get("usage_limit")
    if usage_limit is not None and int(usage_limit) >= 0 and int(parent.get("usage_used") or 0) >= int(usage_limit):
        raise ValueError("AI调用额度已用完，请联系老师续费")


def consume_ai_quota(parent_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE parents SET usage_used=COALESCE(usage_used, 0) + 1, updated_at=? WHERE id=?", (now_text(), parent_id))
        conn.commit()


def create_parent_session(parent_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = now_text()
    with connect() as conn:
        conn.execute(
            "INSERT INTO parent_sessions (token, parent_id, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
            (token, parent_id, now, now),
        )
        conn.commit()
    return token


def register_parent(payload: dict[str, Any]) -> dict[str, Any]:
    phone = validate_mainland_mobile(payload.get("phone"))
    password = validate_password_strength(payload.get("password"), phone)
    incoming_referral_code = payload.get("ref") or payload.get("referral_code") or payload.get("invite_code")
    nickname = compact_text(payload.get("nickname")) or "家长"

    created = now_text()
    with connect() as conn:
        existing = conn.execute("SELECT * FROM parents WHERE phone=?", (phone,)).fetchone()
        if existing and existing["password_hash"]:
            raise ValueError("这个手机号已经注册，请直接登录")
        password_hash = make_password_hash(password)
        if existing:
            parent_id = int(existing["id"])
            conn.execute(
                """
                UPDATE parents
                SET nickname=?, password_hash=?, status='active',
                    plan_name=COALESCE(plan_name, 'trial'),
                    service_expires_at=COALESCE(service_expires_at, ?),
                    usage_limit=COALESCE(usage_limit, ?),
                    usage_used=COALESCE(usage_used, 0),
                    updated_at=?
                WHERE id=?
                """,
                (nickname, password_hash, days_from_now_text(DEFAULT_TRIAL_DAYS), DEFAULT_AI_CALL_LIMIT, created, parent_id),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO parents (
                  phone, nickname, password_hash, status, plan_name,
                  service_expires_at, usage_limit, usage_used,
                  created_at, updated_at
                )
                VALUES (?, ?, ?, 'active', 'trial', ?, ?, 0, ?, ?)
                """,
                (phone, nickname, password_hash, days_from_now_text(DEFAULT_TRIAL_DAYS), DEFAULT_AI_CALL_LIMIT, created, created),
            )
            parent_id = int(cur.lastrowid)
        ensure_referral_code_in_conn(conn, parent_id)
        record_parent_referral(conn, parent_id, incoming_referral_code)
        conn.commit()

    parent = get_row("SELECT * FROM parents WHERE id=?", (parent_id,))
    token = create_parent_session(parent_id)
    return {"token": token, "parent": public_parent(parent), "is_new": existing is None}  # type: ignore[arg-type]


def login_parent(payload: dict[str, Any]) -> dict[str, Any]:
    phone = normalize_phone(payload.get("phone"))
    password = compact_text(payload.get("password"))
    parent = get_row("SELECT * FROM parents WHERE phone=?", (phone,))
    if not parent or not verify_password(password, parent.get("password_hash")):
        raise ValueError("手机号或密码不正确")
    if parent.get("status") != "active":
        raise ValueError("帐号不可用")
    token = create_parent_session(int(parent["id"]))
    return {"token": token, "parent": public_parent(parent)}


def token_from_headers(headers: Any) -> str:
    auth = headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return compact_text(headers.get("X-Parent-Token"))


def parent_from_token(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    with connect() as conn:
        row = conn.execute(
            """
            SELECT p.*
            FROM parent_sessions s
            JOIN parents p ON p.id = s.parent_id
            WHERE s.token=? AND p.status='active'
            """,
            (token,),
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE parent_sessions SET last_seen_at=? WHERE token=?", (now_text(), token))
        conn.commit()
        return row_to_dict(row)


def logout_parent(token: str) -> dict[str, str]:
    if token:
        with connect() as conn:
            conn.execute("DELETE FROM parent_sessions WHERE token=?", (token,))
            conn.commit()
    return {"status": "logged_out"}


def create_student_session(student_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = now_text()
    with connect() as conn:
        conn.execute(
            "INSERT INTO student_sessions (token, student_id, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
            (token, student_id, now, now),
        )
        conn.commit()
    return token


def student_token_from_headers(headers: Any) -> str:
    auth = headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return compact_text(headers.get("X-Student-Token"))


def student_account_public(account: dict[str, Any] | None) -> dict[str, Any]:
    if not account:
        return {}
    return {
        "student_id": account.get("student_id"),
        "phone": account.get("phone") or "",
        "status": account.get("status") or "active",
        "last_login_at": account.get("last_login_at") or "",
    }


def student_account_for_student(student_id: int) -> dict[str, Any] | None:
    return get_row("SELECT * FROM student_accounts WHERE student_id=?", (student_id,))


def upsert_student_account(student_id: int, phone: str, password: str) -> dict[str, Any]:
    phone = validate_mainland_mobile(phone, "学生手机号")
    password = validate_password_strength(password, phone, "学生密码")

    student = get_student_for_access(student_id, None)
    created = now_text()
    password_hash = make_password_hash(password)
    with connect() as conn:
        existing_phone = conn.execute("SELECT student_id FROM student_accounts WHERE phone=?", (phone,)).fetchone()
        if existing_phone and int(existing_phone["student_id"]) != student_id:
            raise ValueError("这个手机号已经绑定到其他学生")
        existing = conn.execute("SELECT * FROM student_accounts WHERE student_id=?", (student_id,)).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE student_accounts
                SET phone=?, password_hash=?, status='active', updated_at=?
                WHERE student_id=?
                """,
                (phone, password_hash, created, student_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO student_accounts (
                  student_id, phone, password_hash, status, created_at, updated_at
                )
                VALUES (?, ?, ?, 'active', ?, ?)
                """,
                (student_id, phone, password_hash, created, created),
            )
        conn.commit()
    return {
        "student": {
            "id": student["id"],
            "student_code": student.get("student_code"),
            "display_name": student.get("display_name") or "",
        },
        "account": student_account_public(get_row("SELECT * FROM student_accounts WHERE student_id=?", (student_id,))),
    }


def reset_student_password_admin(student_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    student = get_student_for_access(student_id, None)
    account = student_account_for_student(student_id)
    phone = normalize_phone(payload.get("phone") or (account or {}).get("phone"))
    password = validate_password_strength(
        payload.get("password") or payload.get("new_password"),
        phone,
        "学生新密码",
    )
    if account:
        phone = validate_mainland_mobile(phone, "学生手机号")
        now = now_text()
        with connect() as conn:
            existing_phone = conn.execute("SELECT student_id FROM student_accounts WHERE phone=?", (phone,)).fetchone()
            if existing_phone and int(existing_phone["student_id"]) != student_id:
                raise ValueError("这个手机号已经绑定到其他学生")
            conn.execute(
                """
                UPDATE student_accounts
                SET phone=?, password_hash=?, status='active', updated_at=?
                WHERE student_id=?
                """,
                (phone, make_password_hash(password), now, student_id),
            )
            conn.execute("DELETE FROM student_sessions WHERE student_id=?", (student_id,))
            conn.commit()
    else:
        if not phone:
            raise ValueError("学生还没有登录账号，请同时填写学生手机号")
        upsert_student_account(student_id, phone, password)
        with connect() as conn:
            conn.execute("DELETE FROM student_sessions WHERE student_id=?", (student_id,))
            conn.commit()
    return {
        "status": "password_reset",
        "student": {
            "id": student["id"],
            "student_code": student.get("student_code"),
            "display_name": student.get("display_name") or "",
        },
        "account": student_account_public(student_account_for_student(student_id)),
    }


def resolve_student_portal_token(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    token = compact_text(token)
    if not token:
        raise ValueError("缺少学生登录信息")
    access_row = get_row("SELECT * FROM student_access_tokens WHERE token=? AND enabled=1", (token,))
    if access_row:
        student = get_student_for_access(int(access_row["student_id"]), None)
        with connect() as conn:
            conn.execute("UPDATE student_access_tokens SET last_seen_at=? WHERE token=?", (now_text(), token))
            conn.commit()
        return student, access_row
    session_row = get_row(
        """
        SELECT s.*, a.phone AS account_phone
        FROM student_sessions s
        JOIN students st ON st.id = s.student_id
        LEFT JOIN student_accounts a ON a.student_id = st.id
        WHERE s.token=?
        """,
        (token,),
    )
    if not session_row:
        raise ValueError("学生链接无效或已关闭")
    student = get_student_for_access(int(session_row["student_id"]), None)
    with connect() as conn:
        conn.execute("UPDATE student_sessions SET last_seen_at=? WHERE token=?", (now_text(), token))
        conn.commit()
    return student, session_row


def login_student(payload: dict[str, Any]) -> dict[str, Any]:
    phone = normalize_phone(payload.get("phone"))
    password = compact_text(payload.get("password"))
    if not phone:
        raise ValueError("请输入学生手机号")
    if not password:
        raise ValueError("请输入学生密码")
    account = get_row(
        """
        SELECT a.*, s.student_code, s.display_name, s.grade_region, s.goal
        FROM student_accounts a
        JOIN students s ON s.id = a.student_id
        WHERE a.phone=?
        """,
        (phone,),
    )
    if not account or not verify_password(password, account.get("password_hash")):
        raise ValueError("手机号或密码不正确")
    if account.get("status") != "active":
        raise ValueError("学生账号不可用")
    token = create_student_session(int(account["student_id"]))
    with connect() as conn:
        conn.execute(
            "UPDATE student_accounts SET last_login_at=?, updated_at=? WHERE student_id=?",
            (now_text(), now_text(), int(account["student_id"])),
        )
        conn.commit()
    student = get_student_for_access(int(account["student_id"]), None)
    return {
        "token": token,
        "student": {
            "id": student["id"],
            "student_code": student.get("student_code"),
            "display_name": student.get("display_name") or "同学",
            "grade_region": student.get("grade_region") or "",
            "goal": student.get("goal") or "",
        },
        "account": student_account_public(account),
    }


def logout_student(token: str) -> dict[str, str]:
    if token:
        with connect() as conn:
            conn.execute("DELETE FROM student_sessions WHERE token=?", (token,))
            conn.commit()
    return {"status": "logged_out"}


def require_parent(headers: Any) -> dict[str, Any]:
    parent = parent_from_token(token_from_headers(headers))
    if not parent:
        raise ValueError("请先登录家长帐号")
    return parent


def require_student(headers: Any) -> dict[str, Any]:
    student, _ = resolve_student_portal_token(student_token_from_headers(headers))
    return student


def list_parent_students(parent_id: int) -> list[dict[str, Any]]:
    return list_rows(
        """
        SELECT id, student_code, display_name, grade_region, goal,
               current_level, current_card_load, execution_mode,
               current_plan_version_id, status, created_at,
               (
                 SELECT phone
                 FROM student_accounts a
                 WHERE a.student_id = students.id
                 LIMIT 1
               ) AS student_account_phone
        FROM students
        WHERE parent_id=?
        ORDER BY id DESC
        LIMIT 100
        """,
        (parent_id,),
    )


def get_student_for_access(student_id: int, parent_id: int | None = None) -> dict[str, Any]:
    student = get_row("SELECT * FROM students WHERE id=?", (student_id,))
    if not student:
        raise ValueError("学生不存在")
    if parent_id is not None and student.get("parent_id") != parent_id:
        raise ValueError("无权访问该学生档案")
    return student


def list_parents_admin() -> list[dict[str, Any]]:
    rows = list_rows(
        """
        SELECT p.id, p.phone, p.nickname, p.status, p.plan_name,
               p.service_expires_at, p.usage_limit, p.usage_used,
               COUNT(s.id) AS student_count, p.created_at, p.updated_at
        FROM parents p
        LEFT JOIN students s ON s.parent_id = p.id
        GROUP BY p.id
        ORDER BY p.id DESC
        LIMIT 200
        """
    )
    for row in rows:
        row["usage_remaining"] = None if row.get("usage_limit") is None else max(int(row.get("usage_limit") or 0) - int(row.get("usage_used") or 0), 0)
        row["is_expired"] = is_parent_expired(row)
    return rows


def update_parent_entitlement(parent_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    parent = get_row("SELECT * FROM parents WHERE id=?", (parent_id,))
    if not parent:
        raise ValueError("家长帐号不存在")

    plan_name, status, service_expires_at, usage_limit, usage_used = apply_entitlement_preset(payload, parent)

    with connect() as conn:
        conn.execute(
            """
            UPDATE parents
            SET plan_name=?, status=?, service_expires_at=?, usage_limit=?, usage_used=?, updated_at=?
            WHERE id=?
            """,
            (plan_name, status, service_expires_at, usage_limit, max(usage_used, 0), now_text(), parent_id),
        )
        conn.commit()
    updated = get_row("SELECT * FROM parents WHERE id=?", (parent_id,))
    return {"parent": public_parent(updated)}  # type: ignore[arg-type]


def reset_parent_password_admin(parent_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    parent = get_row("SELECT * FROM parents WHERE id=?", (parent_id,))
    if not parent:
        raise ValueError("家长帐号不存在")
    phone = normalize_phone(parent.get("phone"))
    password = validate_password_strength(
        payload.get("password") or payload.get("new_password"),
        phone,
        "家长新密码",
    )
    now = now_text()
    with connect() as conn:
        conn.execute(
            "UPDATE parents SET password_hash=?, updated_at=? WHERE id=?",
            (make_password_hash(password), now, parent_id),
        )
        conn.execute("DELETE FROM parent_sessions WHERE parent_id=?", (parent_id,))
        conn.commit()
    updated = get_row("SELECT * FROM parents WHERE id=?", (parent_id,))
    return {"status": "password_reset", "parent": public_parent(updated)}  # type: ignore[arg-type]


def list_referrals_admin() -> list[dict[str, Any]]:
    return list_rows(
        """
        SELECT r.id, r.status, r.referral_code, r.reward_inviter_delta,
               r.reward_invitee_delta, r.created_at, r.qualified_at, r.reward_granted_at,
               inviter.phone AS inviter_phone, inviter.nickname AS inviter_nickname,
               invitee.phone AS invitee_phone, invitee.nickname AS invitee_nickname
        FROM parent_referrals r
        JOIN parents inviter ON inviter.id = r.inviter_parent_id
        JOIN parents invitee ON invitee.id = r.invitee_parent_id
        ORDER BY r.id DESC
        LIMIT 200
        """
    )


def list_ai_jobs_admin(status: str = "all") -> list[dict[str, Any]]:
    params: tuple[Any, ...] = ()
    where = ""
    if status and status != "all":
        where = "WHERE j.status=?"
        params = (status,)
    rows = list_rows(
        f"""
        SELECT j.*, p.phone, p.nickname, s.student_code, s.display_name
        FROM ai_jobs j
        LEFT JOIN parents p ON p.id = j.parent_id
        LEFT JOIN students s ON s.id = j.student_id
        {where}
        ORDER BY j.id DESC
        LIMIT 200
        """,
        params,
    )
    for row in rows:
        row["task_label"] = TASK_LABELS.get(row.get("task_type"), row.get("task_type"))
        row["parent_label"] = row.get("nickname") or row.get("phone") or f"家长{row.get('parent_id')}"
        row["student_label"] = row.get("display_name") or row.get("student_code") or f"学生{row.get('student_id')}"
    return rows


def update_ai_job_admin(job_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    action = compact_text(payload.get("action"))
    job = get_row("SELECT * FROM ai_jobs WHERE id=?", (job_id,))
    if not job:
        raise ValueError("AI任务不存在")
    status = compact_text(job.get("status"))
    if action == "cancel":
        if status == "running":
            raise ValueError("任务正在运行，不能强制中断；请等待结果或稍后重试。")
        if status == "success":
            raise ValueError("已成功的任务不需要取消。")
        with connect() as conn:
            conn.execute(
                "UPDATE ai_jobs SET status='error', error_text=?, finished_at=? WHERE id=?",
                ("老师在后台手动取消。", now_text(), job_id),
            )
            conn.commit()
        return get_ai_job(job_id, None)
    if action == "retry":
        if status == "running":
            raise ValueError("任务正在运行，不要重复排队。")
        if status == "queued":
            return get_ai_job(job_id, None)
        with connect() as conn:
            conn.execute(
                """
                UPDATE ai_jobs
                SET status='queued', result_text='', usage_log_id=NULL, error_text='',
                    started_at=NULL, finished_at=NULL
                WHERE id=?
                """,
                (job_id,),
            )
            conn.commit()
        AI_EXECUTOR.submit(run_ai_job, job_id)
        return get_ai_job(job_id, None)
    raise ValueError("请选择正确的队列操作")


def list_model_configs() -> list[dict[str, Any]]:
    rows = list_rows("SELECT * FROM model_configs ORDER BY task_type")
    for row in rows:
        row["task_label"] = TASK_LABELS.get(row.get("task_type"), row.get("task_type"))
        row["api_key_available"] = bool(os.environ.get(row.get("api_key_env") or ""))
    return rows


def upsert_model_config(payload: dict[str, Any]) -> dict[str, Any]:
    task_type = compact_text(payload.get("task_type"))
    if task_type not in TASK_LABELS:
        raise ValueError("任务类型不正确")
    provider_name = compact_text(payload.get("provider_name")) or "OpenAI-Compatible"
    model_name = compact_text(payload.get("model_name"))
    endpoint_url = compact_text(payload.get("endpoint_url"))
    api_key_env = compact_text(payload.get("api_key_env"))
    temperature = float(payload.get("temperature") if payload.get("temperature") not in (None, "") else 0.2)
    enabled = 1 if payload.get("enabled", True) in (True, 1, "1", "true", "on", "是") else 0
    if not model_name:
        raise ValueError("请填写模型名")
    if not endpoint_url.startswith("https://") and not endpoint_url.startswith("http://"):
        raise ValueError("请填写完整 endpoint_url")
    if not api_key_env:
        raise ValueError("请填写 API Key 环境变量名")

    now = now_text()
    with connect() as conn:
        existing = conn.execute("SELECT id FROM model_configs WHERE task_type=?", (task_type,)).fetchone()
        if existing:
            config_id = int(existing["id"])
            conn.execute(
                """
                UPDATE model_configs
                SET provider_name=?, model_name=?, endpoint_url=?, api_key_env=?,
                    temperature=?, enabled=?, updated_at=?
                WHERE id=?
                """,
                (provider_name, model_name, endpoint_url, api_key_env, temperature, enabled, now, config_id),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO model_configs (
                  task_type, provider_name, model_name, endpoint_url, api_key_env,
                  temperature, enabled, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (task_type, provider_name, model_name, endpoint_url, api_key_env, temperature, enabled, now, now),
            )
            config_id = int(cur.lastrowid)
        conn.commit()
    config = get_row("SELECT * FROM model_configs WHERE id=?", (config_id,))
    result = dict(config)
    result["api_key_available"] = bool(os.environ.get(result.get("api_key_env") or ""))
    result["task_label"] = TASK_LABELS.get(result.get("task_type"), result.get("task_type"))
    return {"config": result}


def configure_xiaomi_models(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    endpoint_url = compact_text(payload.get("endpoint_url")) or os.environ.get("XIAOMI_BASE_URL") or "https://token-plan-cn.xiaomimimo.com/v1"
    model_name = compact_text(payload.get("model_name")) or "mimo-v2.5-pro"
    temperature = float(payload.get("temperature") if payload.get("temperature") not in (None, "") else 0.2)
    configured = []
    for task_type in TASK_LABELS:
        result = upsert_model_config(
            {
                "task_type": task_type,
                "provider_name": "Xiaomi MiMo",
                "model_name": model_name,
                "endpoint_url": endpoint_url,
                "api_key_env": "XIAOMI_API_KEY",
                "temperature": temperature,
                "enabled": True,
            }
        )
        configured.append(result["config"])
    return {"items": configured, "endpoint_url": endpoint_url, "model_name": model_name}


def find_model_config(task_type: str) -> dict[str, Any]:
    config = get_row("SELECT * FROM model_configs WHERE task_type=? AND enabled=1", (task_type,))
    if not config:
        raise ValueError(f"未配置任务模型：{TASK_LABELS.get(task_type, task_type)}")
    if not os.environ.get(config.get("api_key_env") or ""):
        raise ValueError(f"服务器未设置 API Key 环境变量：{config.get('api_key_env')}")
    return config


def _query_value(query: dict[str, list[str]] | dict[str, Any], key: str, fallback: str = "") -> str:
    value = query.get(key, fallback)
    if isinstance(value, list):
        return compact_text(value[0] if value else fallback)
    return compact_text(value)


def normalize_resource_payload(payload: dict[str, Any]) -> dict[str, str]:
    title = compact_text(payload.get("title"))
    if not title:
        raise ValueError("请填写资源标题")
    source_type = compact_text(payload.get("source_type")) or "resource"
    if not re.fullmatch(r"[a-zA-Z0-9_\-]+", source_type):
        raise ValueError("资源类型只能使用字母、数字、下划线或短横线")
    status = compact_text(payload.get("status")) or "active"
    if status not in {"active", "archived"}:
        raise ValueError("资源状态不正确")
    url = compact_text(payload.get("url"))
    if url and not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("资源链接必须以 http:// 或 https:// 开头")
    return {
        "title": title[:160],
        "source_type": source_type[:40],
        "subject": compact_text(payload.get("subject"))[:80],
        "grade": compact_text(payload.get("grade"))[:80],
        "region": compact_text(payload.get("region"))[:80],
        "textbook_version": compact_text(payload.get("textbook_version"))[:80],
        "authority_level": (compact_text(payload.get("authority_level")) or "reference")[:40],
        "url": url[:500],
        "summary": compact_text(payload.get("summary"))[:1200],
        "tags": compact_text(payload.get("tags"))[:500],
        "status": status,
    }


def upsert_learning_resource_admin(payload: dict[str, Any]) -> dict[str, Any]:
    item = normalize_resource_payload(payload)
    resource_id = int(payload.get("id") or 0)
    now = now_text()
    with connect() as conn:
        if resource_id:
            existing = conn.execute("SELECT id FROM learning_resources WHERE id=?", (resource_id,)).fetchone()
            if not existing:
                raise ValueError("资源不存在")
            conn.execute(
                """
                UPDATE learning_resources
                SET title=?, source_type=?, subject=?, grade=?, region=?, textbook_version=?,
                    authority_level=?, url=?, summary=?, tags=?, status=?, updated_at=?
                WHERE id=?
                """,
                (
                    item["title"], item["source_type"], item["subject"], item["grade"], item["region"],
                    item["textbook_version"], item["authority_level"], item["url"], item["summary"],
                    item["tags"], item["status"], now, resource_id,
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO learning_resources (
                  title, source_type, subject, grade, region, textbook_version,
                  authority_level, url, summary, tags, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["title"], item["source_type"], item["subject"], item["grade"], item["region"],
                    item["textbook_version"], item["authority_level"], item["url"], item["summary"],
                    item["tags"], item["status"], now, now,
                ),
            )
            resource_id = int(cur.lastrowid)
        conn.commit()
    return {"resource": get_row("SELECT * FROM learning_resources WHERE id=?", (resource_id,))}


def list_learning_resources_admin(query: dict[str, list[str]] | dict[str, Any] | None = None) -> dict[str, Any]:
    query = query or {}
    q = _query_value(query, "q")
    subject = _query_value(query, "subject")
    grade = _query_value(query, "grade")
    status = _query_value(query, "status", "active") or "active"
    params: list[Any] = []
    where = []
    if status != "all":
        where.append("status=?")
        params.append(status)
    if subject:
        where.append("(subject LIKE ? OR tags LIKE ?)")
        params.extend([f"%{subject}%", f"%{subject}%"])
    if grade:
        where.append("(grade LIKE ? OR tags LIKE ?)")
        params.extend([f"%{grade}%", f"%{grade}%"])
    if q:
        like = f"%{q}%"
        where.append(
            "(title LIKE ? OR summary LIKE ? OR tags LIKE ? OR subject LIKE ? OR grade LIKE ? OR region LIKE ? OR textbook_version LIKE ?)"
        )
        params.extend([like, like, like, like, like, like, like])
    sql = "SELECT * FROM learning_resources"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE authority_level WHEN 'official' THEN 0 WHEN 'publisher' THEN 1 WHEN 'teacher_curated' THEN 2 ELSE 3 END, id DESC LIMIT 200"
    items = list_rows(sql, tuple(params))
    return {"items": items, "total": len(items)}


def extract_learning_search_terms(student: dict[str, Any], user_note: str = "") -> list[str]:
    text = " ".join(
        compact_text(value)
        for value in (
            student.get("grade_region"),
            student.get("goal"),
            student.get("current_level"),
            student.get("main_difficulties"),
            user_note,
        )
        if compact_text(value)
    )
    terms: list[str] = []
    for token in re.split(r"[\s,，、/|;；:：。()\[\]【】]+", text):
        token = compact_text(token)
        if len(token) >= 2 and token not in terms:
            terms.append(token)
    for subject in ("语文", "数学", "英语", "历史", "道法", "生物", "地理", "物理", "化学"):
        if subject in text and subject not in terms:
            terms.append(subject)
    return terms[:16]


def search_learning_evidence_for_student(
    student: dict[str, Any],
    user_note: str = "",
    limit: int = 8,
) -> list[dict[str, Any]]:
    terms = extract_learning_search_terms(student, user_note)
    resources = list_rows("SELECT * FROM learning_resources WHERE status='active' ORDER BY id DESC LIMIT 500")
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in resources:
        haystack = " ".join(
            compact_text(row.get(key))
            for key in ("title", "source_type", "subject", "grade", "region", "textbook_version", "summary", "tags", "authority_level")
        )
        score = 0
        for term in terms:
            if term and term in haystack:
                score += 4 if term in compact_text(row.get("title")) else 2
        if row.get("authority_level") == "official":
            score += 2
        elif row.get("authority_level") == "publisher":
            score += 1
        if score > 0:
            scored.append((score, row))
    if not scored:
        return list_rows(
            """
            SELECT * FROM learning_resources
            WHERE status='active' AND authority_level IN ('official', 'publisher')
            ORDER BY CASE authority_level WHEN 'official' THEN 0 ELSE 1 END, id
            LIMIT ?
            """,
            (limit,),
        )
    scored.sort(key=lambda item: (-item[0], item[1].get("id") or 0))
    return [row for _, row in scored[:limit]]


def list_learning_scope_index_admin(query: dict[str, list[str]] | dict[str, Any] | None = None) -> dict[str, Any]:
    query = query or {}
    q = _query_value(query, "q")
    stage = _query_value(query, "stage")
    grade = _query_value(query, "grade")
    subject = _query_value(query, "subject")
    status = _query_value(query, "status", "active") or "active"
    params: list[Any] = []
    where = []
    if status != "all":
        where.append("status=?")
        params.append(status)
    if stage:
        where.append("stage LIKE ?")
        params.append(f"%{stage}%")
    if grade:
        where.append("(grade LIKE ? OR grade_aliases LIKE ?)")
        params.extend([f"%{grade}%", f"%{grade}%"])
    if subject:
        where.append("(subject LIKE ? OR subject_aliases LIKE ?)")
        params.extend([f"%{subject}%", f"%{subject}%"])
    if q:
        like = f"%{q}%"
        where.append(
            "(stage LIKE ? OR grade LIKE ? OR grade_aliases LIKE ? OR subject LIKE ? OR subject_aliases LIKE ? OR exam_focus LIKE ? OR intervention_modes LIKE ?)"
        )
        params.extend([like, like, like, like, like, like, like])
    sql = "SELECT * FROM learning_scope_index"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE stage WHEN '小学' THEN 0 WHEN '初中' THEN 1 WHEN '高中' THEN 2 ELSE 3 END, id LIMIT 300"
    items = list_rows(sql, tuple(params))
    return {"items": items, "total": len(items)}


def list_learning_coverage_admin(query: dict[str, list[str]] | dict[str, Any] | None = None) -> dict[str, Any]:
    scope = list_learning_scope_index_admin(query)
    items: list[dict[str, Any]] = []
    summary = {"ready": 0, "partial": 0, "seed": 0, "empty": 0}
    for row in scope["items"]:
        subject = compact_text(row.get("subject"))
        grade = compact_text(row.get("grade"))
        resource_count = int(
            get_row(
                """
                SELECT COUNT(*) AS count
                FROM learning_resources
                WHERE status='active'
                  AND (subject LIKE ? OR subject='全科' OR tags LIKE ?)
                  AND (grade LIKE ? OR grade='义务教育' OR grade='高中' OR tags LIKE ?)
                """,
                (f"%{subject}%", f"%{subject}%", f"%{grade}%", f"%{grade}%"),
            )["count"]  # type: ignore[index]
        )
        knowledge_point_count = int(
            get_row(
                """
                SELECT COUNT(*) AS count
                FROM knowledge_points
                WHERE subject LIKE ? AND grade LIKE ?
                """,
                (f"%{subject}%", f"%{grade}%"),
            )["count"]  # type: ignore[index]
        )
        exam_pattern_count = int(
            get_row(
                """
                SELECT COUNT(*) AS count
                FROM exam_patterns
                WHERE subject LIKE ? AND grade LIKE ?
                """,
                (f"%{subject}%", f"%{grade}%"),
            )["count"]  # type: ignore[index]
        )
        if knowledge_point_count and exam_pattern_count:
            coverage_level = "ready"
        elif knowledge_point_count or exam_pattern_count:
            coverage_level = "partial"
        elif resource_count:
            coverage_level = "seed"
        else:
            coverage_level = "empty"
        summary[coverage_level] += 1
        item = dict(row)
        item.update(
            {
                "resource_count": resource_count,
                "knowledge_point_count": knowledge_point_count,
                "exam_pattern_count": exam_pattern_count,
                "coverage_level": coverage_level,
            }
        )
        items.append(item)
    return {"items": items, "total": len(items), "summary": summary}


def list_learning_collection_tasks_admin(query: dict[str, list[str]] | dict[str, Any] | None = None) -> dict[str, Any]:
    coverage = list_learning_coverage_admin(query)
    tasks: list[dict[str, Any]] = []
    for item in coverage["items"]:
        level = compact_text(item.get("coverage_level"))
        if level == "ready":
            continue
        if level == "partial":
            if int(item.get("knowledge_point_count") or 0) and not int(item.get("exam_pattern_count") or 0):
                task_type = "collect_exam_patterns"
                task_text = "补历年真题题型、设问方式、答题要点和考频。"
            else:
                task_type = "collect_knowledge_points"
                task_text = "补教材单元知识点、课标要求、常见错法和记忆提示。"
            priority = "high"
        elif level == "empty":
            task_type = "collect_source"
            task_text = "先补官方平台、课标、教材目录或可信来源入口。"
            priority = "high"
        else:
            task_type = "collect_foundation"
            task_text = "从通用入口拆出本年级本学科的教材目录、核心考点和高频题型。"
            priority = "normal"
        task = dict(item)
        task.update(
            {
                "task_type": task_type,
                "task_text": task_text,
                "priority": priority,
            }
        )
        tasks.append(task)
    tasks.sort(
        key=lambda row: (
            0 if row.get("priority") == "high" else 1,
            {"empty": 0, "partial": 1, "seed": 2}.get(row.get("coverage_level"), 3),
            row.get("id") or 0,
        )
    )
    return {"items": tasks[:200], "total": len(tasks)}


COLLECTION_TASK_STATUSES = {"todo", "doing", "review", "done", "blocked"}
COLLECTION_TASK_TYPES = {"collect_source", "collect_foundation", "collect_knowledge_points", "collect_exam_patterns"}


def normalize_collection_task_payload(payload: dict[str, Any]) -> dict[str, Any]:
    task_type = compact_text(payload.get("task_type"))
    if task_type not in COLLECTION_TASK_TYPES:
        raise ValueError("采集任务类型不正确")
    status = compact_text(payload.get("status")) or "todo"
    if status not in COLLECTION_TASK_STATUSES:
        raise ValueError("采集任务状态不正确")
    priority = compact_text(payload.get("priority")) or "normal"
    if priority not in {"high", "normal", "low"}:
        raise ValueError("采集任务优先级不正确")
    task_text = compact_text(payload.get("task_text"))
    if not task_text:
        raise ValueError("请填写采集任务内容")
    return {
        "scope_index_id": int(payload.get("scope_index_id") or 0) or None,
        "stage": compact_text(payload.get("stage"))[:40],
        "grade": compact_text(payload.get("grade"))[:80],
        "subject": compact_text(payload.get("subject"))[:80],
        "coverage_level": compact_text(payload.get("coverage_level"))[:40],
        "task_type": task_type,
        "task_text": task_text[:600],
        "priority": priority,
        "status": status,
        "source_url": compact_text(payload.get("source_url"))[:500],
        "assignee": compact_text(payload.get("assignee"))[:80],
        "review_note": compact_text(payload.get("review_note"))[:1000],
    }


def upsert_learning_collection_task_record_admin(payload: dict[str, Any]) -> dict[str, Any]:
    task_id = int(payload.get("id") or 0)
    now = now_text()
    existing = get_row("SELECT * FROM learning_collection_tasks WHERE id=?", (task_id,)) if task_id else None
    if existing:
        merged = dict(existing)
        for key, value in payload.items():
            if key != "id":
                merged[key] = value
        item = normalize_collection_task_payload(merged)
    else:
        item = normalize_collection_task_payload(payload)
    completed_at = now if item["status"] == "done" else None
    if existing and existing.get("completed_at") and item["status"] == "done":
        completed_at = existing.get("completed_at")
    with connect() as conn:
        if existing:
            conn.execute(
                """
                UPDATE learning_collection_tasks
                SET scope_index_id=?, stage=?, grade=?, subject=?, coverage_level=?,
                    task_type=?, task_text=?, priority=?, status=?, source_url=?,
                    assignee=?, review_note=?, updated_at=?, completed_at=?
                WHERE id=?
                """,
                (
                    item["scope_index_id"], item["stage"], item["grade"], item["subject"], item["coverage_level"],
                    item["task_type"], item["task_text"], item["priority"], item["status"], item["source_url"],
                    item["assignee"], item["review_note"], now, completed_at, task_id,
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO learning_collection_tasks (
                  scope_index_id, stage, grade, subject, coverage_level,
                  task_type, task_text, priority, status, source_url,
                  assignee, review_note, created_at, updated_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["scope_index_id"], item["stage"], item["grade"], item["subject"], item["coverage_level"],
                    item["task_type"], item["task_text"], item["priority"], item["status"], item["source_url"],
                    item["assignee"], item["review_note"], now, now, completed_at,
                ),
            )
            task_id = int(cur.lastrowid)
        conn.commit()
    return {"task": get_row("SELECT * FROM learning_collection_tasks WHERE id=?", (task_id,))}


def list_learning_collection_task_records_admin(query: dict[str, list[str]] | dict[str, Any] | None = None) -> dict[str, Any]:
    query = query or {}
    status = _query_value(query, "status", "todo") or "todo"
    stage = _query_value(query, "stage")
    grade = _query_value(query, "grade")
    subject = _query_value(query, "subject")
    q = _query_value(query, "q")
    params: list[Any] = []
    where = []
    if status != "all":
        where.append("status=?")
        params.append(status)
    if stage:
        where.append("stage LIKE ?")
        params.append(f"%{stage}%")
    if grade:
        where.append("grade LIKE ?")
        params.append(f"%{grade}%")
    if subject:
        where.append("subject LIKE ?")
        params.append(f"%{subject}%")
    if q:
        like = f"%{q}%"
        where.append("(task_text LIKE ? OR source_url LIKE ? OR review_note LIKE ? OR assignee LIKE ?)")
        params.extend([like, like, like, like])
    sql = "SELECT * FROM learning_collection_tasks"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, id DESC LIMIT 300"
    items = list_rows(sql, tuple(params))
    return {"items": items, "total": len(items)}


def sync_learning_collection_tasks_admin(query: dict[str, list[str]] | dict[str, Any] | None = None) -> dict[str, Any]:
    derived = list_learning_collection_tasks_admin(query)
    created = 0
    skipped = 0
    tasks: list[dict[str, Any]] = []
    with connect() as conn:
        for item in derived["items"]:
            existing = conn.execute(
                """
                SELECT id FROM learning_collection_tasks
                WHERE COALESCE(scope_index_id, 0)=?
                  AND task_type=?
                  AND status IN ('todo', 'doing', 'review', 'blocked')
                LIMIT 1
                """,
                (int(item.get("id") or 0), item.get("task_type")),
            ).fetchone()
            if existing:
                skipped += 1
                continue
            now = now_text()
            conn.execute(
                """
                INSERT INTO learning_collection_tasks (
                  scope_index_id, stage, grade, subject, coverage_level,
                  task_type, task_text, priority, status, source_url,
                  assignee, review_note, created_at, updated_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'todo', '', '', '', ?, ?, NULL)
                """,
                (
                    int(item.get("id") or 0), item.get("stage"), item.get("grade"), item.get("subject"),
                    item.get("coverage_level"), item.get("task_type"), item.get("task_text"),
                    item.get("priority") or "normal", now, now,
                ),
            )
            created += 1
        conn.commit()
    tasks = list_learning_collection_task_records_admin({"status": ["todo"]})["items"]
    return {"created": created, "skipped": skipped, "items": tasks, "total": len(tasks)}


def search_learning_scope_for_student(
    student: dict[str, Any],
    user_note: str = "",
    limit: int = 12,
) -> list[dict[str, Any]]:
    terms = extract_learning_search_terms(student, user_note)
    rows = list_rows("SELECT * FROM learning_scope_index WHERE status='active' ORDER BY id")
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        haystack = " ".join(
            compact_text(row.get(key))
            for key in ("stage", "grade", "grade_aliases", "subject", "subject_aliases", "exam_focus", "intervention_modes")
        )
        score = 0
        for term in terms:
            if term and term in haystack:
                score += 5 if term in compact_text(row.get("subject")) else 3 if term in compact_text(row.get("grade")) else 1
        if score:
            scored.append((score, row))
    scored.sort(key=lambda item: (-item[0], item[1].get("id") or 0))
    return [row for _, row in scored[:limit]]


def normalize_knowledge_point_payload(payload: dict[str, Any]) -> dict[str, Any]:
    subject = compact_text(payload.get("subject"))
    grade = compact_text(payload.get("grade"))
    point_name = compact_text(payload.get("point_name"))
    if not subject:
        raise ValueError("请填写学科")
    if not grade:
        raise ValueError("请填写年级")
    if not point_name:
        raise ValueError("请填写知识点")
    return {
        "resource_id": int(payload.get("resource_id") or 0) or None,
        "subject": subject[:80],
        "grade": grade[:80],
        "textbook_version": compact_text(payload.get("textbook_version"))[:80],
        "unit_name": compact_text(payload.get("unit_name"))[:160],
        "point_name": point_name[:160],
        "requirement_level": compact_text(payload.get("requirement_level"))[:80],
        "importance": (compact_text(payload.get("importance")) or "normal")[:40],
        "common_errors": compact_text(payload.get("common_errors"))[:1000],
        "memory_hint": compact_text(payload.get("memory_hint"))[:500],
    }


def upsert_knowledge_point_admin(payload: dict[str, Any]) -> dict[str, Any]:
    item = normalize_knowledge_point_payload(payload)
    point_id = int(payload.get("id") or 0)
    now = now_text()
    with connect() as conn:
        if point_id:
            existing = conn.execute("SELECT id FROM knowledge_points WHERE id=?", (point_id,)).fetchone()
            if not existing:
                raise ValueError("知识点不存在")
            conn.execute(
                """
                UPDATE knowledge_points
                SET resource_id=?, subject=?, grade=?, textbook_version=?, unit_name=?,
                    point_name=?, requirement_level=?, importance=?, common_errors=?,
                    memory_hint=?, updated_at=?
                WHERE id=?
                """,
                (
                    item["resource_id"], item["subject"], item["grade"], item["textbook_version"],
                    item["unit_name"], item["point_name"], item["requirement_level"], item["importance"],
                    item["common_errors"], item["memory_hint"], now, point_id,
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO knowledge_points (
                  resource_id, subject, grade, textbook_version, unit_name,
                  point_name, requirement_level, importance, common_errors,
                  memory_hint, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["resource_id"], item["subject"], item["grade"], item["textbook_version"],
                    item["unit_name"], item["point_name"], item["requirement_level"], item["importance"],
                    item["common_errors"], item["memory_hint"], now, now,
                ),
            )
            point_id = int(cur.lastrowid)
        conn.commit()
    return {"point": get_row("SELECT * FROM knowledge_points WHERE id=?", (point_id,))}


def list_knowledge_points_admin(query: dict[str, list[str]] | dict[str, Any] | None = None) -> dict[str, Any]:
    query = query or {}
    q = _query_value(query, "q")
    subject = _query_value(query, "subject")
    grade = _query_value(query, "grade")
    importance = _query_value(query, "importance")
    params: list[Any] = []
    where = []
    if subject:
        where.append("subject LIKE ?")
        params.append(f"%{subject}%")
    if grade:
        where.append("grade LIKE ?")
        params.append(f"%{grade}%")
    if importance:
        where.append("importance=?")
        params.append(importance)
    if q:
        like = f"%{q}%"
        where.append("(point_name LIKE ? OR unit_name LIKE ? OR common_errors LIKE ? OR memory_hint LIKE ? OR textbook_version LIKE ?)")
        params.extend([like, like, like, like, like])
    sql = "SELECT * FROM knowledge_points"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE importance WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, id DESC LIMIT 300"
    items = list_rows(sql, tuple(params))
    return {"items": items, "total": len(items)}


def normalize_exam_pattern_payload(payload: dict[str, Any]) -> dict[str, Any]:
    subject = compact_text(payload.get("subject"))
    grade = compact_text(payload.get("grade"))
    pattern_text = compact_text(payload.get("pattern_text"))
    if not subject:
        raise ValueError("请填写学科")
    if not grade:
        raise ValueError("请填写年级")
    if not pattern_text:
        raise ValueError("请填写题型/例题模式")
    frequency_level = compact_text(payload.get("frequency_level")) or "unknown"
    if frequency_level not in {"must", "high", "medium", "low", "unknown"}:
        raise ValueError("考频级别不正确")
    return {
        "knowledge_point_id": int(payload.get("knowledge_point_id") or 0) or None,
        "resource_id": int(payload.get("resource_id") or 0) or None,
        "subject": subject[:80],
        "grade": grade[:80],
        "region": compact_text(payload.get("region"))[:80],
        "exam_scope": compact_text(payload.get("exam_scope"))[:120],
        "year_text": compact_text(payload.get("year_text"))[:120],
        "question_type": compact_text(payload.get("question_type"))[:120],
        "frequency_level": frequency_level,
        "pattern_text": pattern_text[:1200],
        "answer_points": compact_text(payload.get("answer_points"))[:1200],
        "source_url": compact_text(payload.get("source_url"))[:500],
    }


def upsert_exam_pattern_admin(payload: dict[str, Any]) -> dict[str, Any]:
    item = normalize_exam_pattern_payload(payload)
    pattern_id = int(payload.get("id") or 0)
    now = now_text()
    with connect() as conn:
        if pattern_id:
            existing = conn.execute("SELECT id FROM exam_patterns WHERE id=?", (pattern_id,)).fetchone()
            if not existing:
                raise ValueError("题型不存在")
            conn.execute(
                """
                UPDATE exam_patterns
                SET knowledge_point_id=?, resource_id=?, subject=?, grade=?, region=?,
                    exam_scope=?, year_text=?, question_type=?, frequency_level=?,
                    pattern_text=?, answer_points=?, source_url=?, updated_at=?
                WHERE id=?
                """,
                (
                    item["knowledge_point_id"], item["resource_id"], item["subject"], item["grade"],
                    item["region"], item["exam_scope"], item["year_text"], item["question_type"],
                    item["frequency_level"], item["pattern_text"], item["answer_points"],
                    item["source_url"], now, pattern_id,
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO exam_patterns (
                  knowledge_point_id, resource_id, subject, grade, region,
                  exam_scope, year_text, question_type, frequency_level,
                  pattern_text, answer_points, source_url, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["knowledge_point_id"], item["resource_id"], item["subject"], item["grade"],
                    item["region"], item["exam_scope"], item["year_text"], item["question_type"],
                    item["frequency_level"], item["pattern_text"], item["answer_points"],
                    item["source_url"], now, now,
                ),
            )
            pattern_id = int(cur.lastrowid)
        conn.commit()
    return {"pattern": get_row("SELECT * FROM exam_patterns WHERE id=?", (pattern_id,))}


def list_exam_patterns_admin(query: dict[str, list[str]] | dict[str, Any] | None = None) -> dict[str, Any]:
    query = query or {}
    q = _query_value(query, "q")
    subject = _query_value(query, "subject")
    grade = _query_value(query, "grade")
    frequency = _query_value(query, "frequency_level")
    params: list[Any] = []
    where = []
    if subject:
        where.append("subject LIKE ?")
        params.append(f"%{subject}%")
    if grade:
        where.append("grade LIKE ?")
        params.append(f"%{grade}%")
    if frequency:
        where.append("frequency_level=?")
        params.append(frequency)
    if q:
        like = f"%{q}%"
        where.append("(pattern_text LIKE ? OR answer_points LIKE ? OR question_type LIKE ? OR exam_scope LIKE ? OR region LIKE ?)")
        params.extend([like, like, like, like, like])
    sql = "SELECT * FROM exam_patterns"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE frequency_level WHEN 'must' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END, id DESC LIMIT 300"
    items = list_rows(sql, tuple(params))
    return {"items": items, "total": len(items)}


def _score_learning_row(row: dict[str, Any], terms: list[str], fields: tuple[str, ...]) -> int:
    haystack = " ".join(compact_text(row.get(key)) for key in fields)
    score = 0
    for term in terms:
        if term and term in haystack:
            score += 4 if term in compact_text(row.get("point_name") or row.get("pattern_text") or row.get("subject")) else 2
    if row.get("importance") == "high" or row.get("frequency_level") in {"must", "high"}:
        score += 3
    return score


def search_knowledge_points_for_student(
    student: dict[str, Any],
    user_note: str = "",
    limit: int = 10,
) -> list[dict[str, Any]]:
    terms = extract_learning_search_terms(student, user_note)
    rows = list_rows("SELECT * FROM knowledge_points ORDER BY id DESC LIMIT 1000")
    scored = [
        (score, row)
        for row in rows
        if (score := _score_learning_row(row, terms, ("subject", "grade", "textbook_version", "unit_name", "point_name", "common_errors", "memory_hint")))
    ]
    scored.sort(key=lambda item: (-item[0], item[1].get("id") or 0))
    return [row for _, row in scored[:limit]]


def search_exam_patterns_for_student(
    student: dict[str, Any],
    user_note: str = "",
    limit: int = 10,
) -> list[dict[str, Any]]:
    terms = extract_learning_search_terms(student, user_note)
    rows = list_rows("SELECT * FROM exam_patterns ORDER BY id DESC LIMIT 1000")
    scored = [
        (score, row)
        for row in rows
        if (score := _score_learning_row(row, terms, ("subject", "grade", "region", "exam_scope", "year_text", "question_type", "frequency_level", "pattern_text", "answer_points")))
    ]
    scored.sort(key=lambda item: (-item[0], item[1].get("id") or 0))
    return [row for _, row in scored[:limit]]


def collect_learning_evidence_context(student: dict[str, Any], user_note: str = "") -> dict[str, list[dict[str, Any]]]:
    return {
        "learning_evidence": search_learning_evidence_for_student(student, user_note),
        "learning_scope_index": search_learning_scope_for_student(student, user_note),
        "knowledge_points": search_knowledge_points_for_student(student, user_note),
        "exam_patterns": search_exam_patterns_for_student(student, user_note),
    }


def _evidence_title(evidence_type: str, row: dict[str, Any]) -> str:
    if evidence_type == "learning_resource":
        return compact_text(row.get("title"))
    if evidence_type == "learning_scope":
        return " / ".join(compact_text(row.get(key)) for key in ("stage", "grade", "subject") if compact_text(row.get(key)))
    if evidence_type == "knowledge_point":
        return compact_text(row.get("point_name"))
    if evidence_type == "exam_pattern":
        return compact_text(row.get("question_type")) or compact_text(row.get("pattern_text"))[:60]
    return ""


def record_plan_evidence_refs(
    student_id: int,
    plan_version_id: int,
    evidence_context: dict[str, list[dict[str, Any]]] | None,
    reason: str = "",
) -> list[dict[str, Any]]:
    evidence_context = evidence_context or {}
    mapping = (
        ("learning_resource", evidence_context.get("learning_evidence") or []),
        ("learning_scope", evidence_context.get("learning_scope_index") or []),
        ("knowledge_point", evidence_context.get("knowledge_points") or []),
        ("exam_pattern", evidence_context.get("exam_patterns") or []),
    )
    created = now_text()
    rows: list[tuple[int, str, int, str, str]] = []
    seen: set[tuple[str, int]] = set()
    for evidence_type, items in mapping:
        for row in items[:12]:
            evidence_id = int(row.get("id") or 0)
            if not evidence_id or (evidence_type, evidence_id) in seen:
                continue
            seen.add((evidence_type, evidence_id))
            title = _evidence_title(evidence_type, row)
            ref_reason = compact_text(reason) or title or "计划生成时自动匹配。"
            if title and title not in ref_reason:
                ref_reason = f"{title}｜{ref_reason}"
            rows.append((student_id, evidence_type, evidence_id, ref_reason[:500], created))
    with connect() as conn:
        conn.execute("DELETE FROM plan_evidence_refs WHERE plan_version_id=?", (plan_version_id,))
        for row in rows:
            conn.execute(
                """
                INSERT INTO plan_evidence_refs (
                  student_id, plan_version_id, evidence_type, evidence_id, reason, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (row[0], plan_version_id, row[1], row[2], row[3], row[4]),
            )
        conn.commit()
    return list_plan_evidence_refs(plan_version_id)


def list_plan_evidence_refs(plan_version_id: int | None) -> list[dict[str, Any]]:
    if not plan_version_id:
        return []
    refs = list_rows(
        """
        SELECT *
        FROM plan_evidence_refs
        WHERE plan_version_id=?
        ORDER BY CASE evidence_type
          WHEN 'learning_scope' THEN 0
          WHEN 'learning_resource' THEN 1
          WHEN 'knowledge_point' THEN 2
          WHEN 'exam_pattern' THEN 3
          ELSE 4
        END, id
        """,
        (plan_version_id,),
    )
    enriched: list[dict[str, Any]] = []
    for ref in refs:
        evidence_type = compact_text(ref.get("evidence_type"))
        evidence_id = int(ref.get("evidence_id") or 0)
        item = dict(ref)
        detail: dict[str, Any] | None = None
        if evidence_type == "learning_resource":
            detail = get_row("SELECT * FROM learning_resources WHERE id=?", (evidence_id,))
        elif evidence_type == "learning_scope":
            detail = get_row("SELECT * FROM learning_scope_index WHERE id=?", (evidence_id,))
        elif evidence_type == "knowledge_point":
            detail = get_row("SELECT * FROM knowledge_points WHERE id=?", (evidence_id,))
        elif evidence_type == "exam_pattern":
            detail = get_row("SELECT * FROM exam_patterns WHERE id=?", (evidence_id,))
        if detail:
            item.update(detail)
            item["evidence_ref_id"] = ref["id"]
            item["evidence_type"] = evidence_type
            item["evidence_id"] = evidence_id
            item["reason"] = ref.get("reason") or ""
            item["ref_created_at"] = ref.get("created_at")
            item["display_title"] = _evidence_title(evidence_type, detail)
        else:
            item["display_title"] = f"{evidence_type} #{evidence_id}"
        enriched.append(item)
    return enriched


def chat_endpoint(endpoint_url: str) -> str:
    endpoint_url = endpoint_url.rstrip("/")
    if endpoint_url.endswith("/chat/completions"):
        return endpoint_url
    if endpoint_url.endswith("/v1"):
        return endpoint_url + "/chat/completions"
    return endpoint_url


def build_ai_messages(task_type: str, student: dict[str, Any], detail: dict[str, Any], user_note: str) -> list[dict[str, str]]:
    task_label = TASK_LABELS.get(task_type, task_type)
    evidence_context = collect_learning_evidence_context(student, user_note)
    context = {
        "task_type": task_type,
        "task_label": task_label,
        "student": {
            "student_code": student.get("student_code"),
            "display_name": student.get("display_name"),
            "grade_region": student.get("grade_region"),
            "goal": student.get("goal"),
            "current_level": student.get("current_level"),
            "current_card_load": student.get("current_card_load"),
            "execution_mode": student.get("execution_mode"),
            "main_difficulties": student.get("main_difficulties"),
        },
        "current_plan": detail.get("current_plan"),
        "latest_intake": detail.get("latest_intake"),
        "recent_feedback": detail.get("recent_feedback"),
        "adjustment_actions": detail.get("adjustment_actions"),
        "learning_evidence": evidence_context["learning_evidence"],
        "learning_scope_index": evidence_context["learning_scope_index"],
        "knowledge_points": evidence_context["knowledge_points"],
        "exam_patterns": evidence_context["exam_patterns"],
        "teacher_note": user_note,
    }
    system = (
        "你是学记教育的学习干预助手。目标是帮助基础薄弱、抗拒学习或临近期末的学生先启动、再稳住、再提分。"
        "输出要可执行、短句化、适合家长照做。不要编造试卷内容；证据不足时要明确写“待确认”。"
        "方法上采用小步重复训练：低门槛起步、同一核心点多次变式、D0/D1/D3/D7复问，根据反馈再加量或降难度。"
        "可使用AI线索记忆：给核心考点配一个短画面、地点、动作或口诀，但不能替代课本关键词答案。"
    )
    if task_type == "card_generation":
        user = (
            "请生成一周可打印知识卡片内容，必须只输出 JSON，不要输出 Markdown、解释或代码块。\n"
            "JSON 结构必须是：\n"
            "{\"title\":\"...\",\"days\":[{\"title\":\"Day 01...\",\"module\":\"...\",\"cards\":[{\"tag\":\"...\",\"q\":\"...\",\"a\":\"...\"}]}]}\n"
            "硬性要求：7天；每天最多8张卡；每张卡只问一个点；q 必须是常见考试题/样题问法，不要只写学习方法。\n"
            "a 必须包含可采分的关键词短句；能写方法时用“答：... 法：...”的结构，法只写一句答题抓手。\n"
            "8格是版面容量，不等于8个全新知识点；同一核心点要用概念卡、认读卡、模板卡、易错卡、自测卡做重复变式。\n"
            "每张卡的答案可在最后加入很短的“线索：...”帮助提取记忆，线索必须服务考点，不要写成故事作文。\n"
            "如果家长备注写明“语文2格、历史2格、地理2格、生物1格、道法1格”等格数，必须严格按备注分配。\n"
            "如果是初一期末且家长只笼统要求8格知识卡，默认按语文、历史、生物、地理、道法安排；英语放在手机听读，不占知识卡格。\n"
            "如果证据不足，可在 module 写“范围待确认”，但仍要给出具体考点卡，不要输出内部版本号。\n"
            "请基于以下 JSON 上下文生成：\n"
            f"{json.dumps(context, ensure_ascii=False, indent=2)}\n"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    user = (
        f"请完成任务：{task_label}。\n"
        "请基于以下 JSON 上下文输出：\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        "输出格式：\n"
        "1. 当前判断\n2. 下一步怎么做\n3. 家长怎么说\n4. 是否需要减量、错卡回炉或恢复新卡\n5. 本周重复与记忆线索安排\n"
        "请用短段落和短列表，避免 Markdown 表格、代码块和复杂符号，方便家长在手机上阅读。\n"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def call_openai_compatible(config: dict[str, Any], messages: list[dict[str, str]], max_completion_tokens: int = 1200) -> dict[str, Any]:
    api_key = os.environ.get(config.get("api_key_env") or "")
    payload = {
        "model": config["model_name"],
        "messages": messages,
        "max_completion_tokens": max_completion_tokens,
        "temperature": float(config.get("temperature") or 0.2),
        "stream": False,
        "thinking": {"type": "disabled"},
    }
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        chat_endpoint(config["endpoint_url"]),
        data=raw,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "api-key": api_key,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    message = body.get("choices", [{}])[0].get("message", {}) or {}
    content = message.get("content") or message.get("reasoning_content") or ""
    usage = body.get("usage", {}) or {}
    return {
        "content": content,
        "raw": body,
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
    }


def generate_ai_for_student(student_id: int, parent: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    task_type = compact_text(payload.get("task_type")) or "daily_feedback"
    if task_type not in TASK_LABELS:
        raise ValueError("任务类型不正确")
    ensure_ai_quota(parent)
    detail = get_student_detail(student_id, int(parent["id"]))
    student = detail["student"]
    config = find_model_config(task_type)
    user_note = compact_text(payload.get("note"))
    messages = build_ai_messages(task_type, student, detail, user_note)
    prompt_chars = sum(len(item["content"]) for item in messages)
    created = now_text()

    try:
        max_tokens = 6000 if task_type == "card_generation" else 1600 if task_type == "initial_plan" else 1200
        result = call_openai_compatible(config, messages, max_tokens)
        consume_ai_quota(int(parent["id"]))
        response_text = compact_text(result.get("content"))
        with connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO ai_usage_logs (
                  parent_id, student_id, task_type, model_config_id, status,
                  prompt_chars, response_chars, input_tokens, output_tokens,
                  error_text, created_at
                )
                VALUES (?, ?, ?, ?, 'success', ?, ?, ?, ?, '', ?)
                """,
                (
                    parent["id"], student_id, task_type, config["id"],
                    prompt_chars, len(response_text),
                    result.get("input_tokens"), result.get("output_tokens"), created,
                ),
            )
            conn.commit()
        fresh_parent = get_row("SELECT * FROM parents WHERE id=?", (parent["id"],))
        return {
            "usage_log_id": int(cur.lastrowid),
            "task_type": task_type,
            "task_label": TASK_LABELS[task_type],
            "model": config["model_name"],
            "provider": config["provider_name"],
            "content": response_text,
            "parent": public_parent(fresh_parent),  # type: ignore[arg-type]
        }
    except urllib.error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="ignore")[:1000]
    except Exception as exc:
        error_text = str(exc)[:1000]

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO ai_usage_logs (
              parent_id, student_id, task_type, model_config_id, status,
              prompt_chars, response_chars, error_text, created_at
            )
            VALUES (?, ?, ?, ?, 'error', ?, 0, ?, ?)
            """,
            (parent["id"], student_id, task_type, config["id"], prompt_chars, error_text, created),
        )
        conn.commit()
    raise ValueError(f"模型调用失败：{error_text}")


def ensure_ai_rate_limit(parent_id: int) -> None:
    cutoff = (datetime.now(TZ) - timedelta(seconds=AI_RATE_WINDOW_SECONDS)).strftime("%Y-%m-%d %H:%M:%S")
    with connect() as conn:
        active_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM ai_jobs WHERE parent_id=? AND status IN ('queued', 'running')",
                (parent_id,),
            ).fetchone()[0]
        )
        recent_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM ai_jobs WHERE parent_id=? AND created_at>=?",
                (parent_id, cutoff),
            ).fetchone()[0]
        )
    if active_count >= 1:
        raise ValueError("已有AI任务正在生成，请稍后查看结果")
    if recent_count >= AI_RATE_LIMIT_PER_WINDOW:
        raise ValueError(f"AI生成太频繁，请{AI_RATE_WINDOW_SECONDS}秒后再试")


def ai_regeneration_warning(student_id: int, task_type: str) -> str:
    student = get_row("SELECT * FROM students WHERE id=?", (student_id,))
    if not student:
        return ""
    plan = get_row("SELECT * FROM plan_versions WHERE id=?", (student.get("current_plan_version_id"),))
    if task_type == "initial_plan" and plan and compact_text(plan.get("plan_summary")):
        return "已经有初步规划。若学生档案没有变化，重新生成可能内容接近；继续会消耗1次AI额度。"
    if task_type == "card_generation" and plan and compact_text(plan.get("file_url")):
        return "已经有知识卡片DOCX。重新生成会覆盖当前文件，并消耗1次AI额度。"
    latest = get_row(
        """
        SELECT * FROM ai_jobs
        WHERE student_id=? AND task_type=? AND status='success'
        ORDER BY id DESC
        LIMIT 1
        """,
        (student_id, task_type),
    )
    if latest and compact_text(latest.get("created_at")).startswith(today_text()):
        return f"今天已经生成过{TASK_LABELS.get(task_type, task_type)}。如果输入内容没有变化，结果可能接近；继续会消耗1次AI额度。"
    return ""


def public_ai_job(job: dict[str, Any], parent: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {
        "job_id": job["id"],
        "parent_id": job["parent_id"],
        "student_id": job["student_id"],
        "task_type": job["task_type"],
        "task_label": TASK_LABELS.get(job.get("task_type"), job.get("task_type")),
        "status": job["status"],
        "note": job.get("note") or "",
        "content": job.get("result_text") or "",
        "error": job.get("error_text") or "",
        "usage_log_id": job.get("usage_log_id"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
    }
    if parent:
        result["parent"] = public_parent(parent)
    return result


def get_ai_job(job_id: int, parent_id: int | None = None) -> dict[str, Any]:
    job = get_row("SELECT * FROM ai_jobs WHERE id=?", (job_id,))
    if not job:
        raise ValueError("AI任务不存在")
    if parent_id is not None and int(job["parent_id"]) != parent_id:
        raise ValueError("无权查看该AI任务")
    parent = get_row("SELECT * FROM parents WHERE id=?", (job["parent_id"],))
    return public_ai_job(job, parent)


def save_initial_plan_ai_result(student_id: int, result_text: str, adjustment_note: str = "") -> None:
    student = get_student_for_access(student_id)
    now = now_text()
    plan = get_row("SELECT * FROM plan_versions WHERE id=?", (student.get("current_plan_version_id"),))
    parent_instruction = (
        "请先阅读这份初步规划。如果方向没问题，再进入第3步生成一周知识卡片；"
        "如果要调整，请补充孩子情况后重新生成。"
    )
    reason = "AI重新生成初步规划。"
    if compact_text(adjustment_note):
        reason += f" 家长调整建议：{compact_text(adjustment_note)[:120]}"
    if plan:
        with connect() as conn:
            conn.execute(
                """
                UPDATE plan_versions
                SET status='draft', reason=?, plan_summary=?, parent_instruction=?, updated_at=?
                WHERE id=?
                """,
                (reason, result_text, parent_instruction, now, plan["id"]),
            )
            conn.commit()
        record_plan_evidence_refs(
            student_id,
            int(plan["id"]),
            collect_learning_evidence_context(student, f"{reason} {adjustment_note} {result_text[:500]}"),
            reason,
        )
    else:
        create_plan_version(
            student_id,
            {
                "version_code": "V1_试运行",
                "plan_type": "trial",
                "reason": reason,
                "plan_summary": result_text,
                "parent_instruction": parent_instruction,
                "status": "draft",
                "generate_files": False,
                "evidence_note": f"{reason} {adjustment_note} {result_text[:500]}",
            },
            None,
        )


def parse_card_plan_json(text: str) -> dict[str, Any] | None:
    text = compact_text(text)
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("days"), list):
        return None
    normalized_days = []
    for idx, day in enumerate(data.get("days")[:7], start=1):
        if not isinstance(day, dict):
            continue
        cards = []
        for card in (day.get("cards") or [])[:8]:
            if not isinstance(card, dict):
                continue
            tag = compact_text(card.get("tag"))
            q = compact_text(card.get("q"))
            a = compact_text(card.get("a"))
            if tag and q and a:
                cards.append({"tag": tag[:40], "q": q[:80], "a": a[:120]})
        if cards:
            cards = enrich_memory_cues(cards, idx)
            normalized_days.append(
                {
                    "title": compact_text(day.get("title")) or f"Day {idx:02d}｜学记教育知识卡",
                    "module": compact_text(day.get("module"))[:120],
                    "cards": cards,
                }
            )
    if not normalized_days:
        return None
    return {
        "title": compact_text(data.get("title")) or "学记教育7天知识卡",
        "days": normalized_days,
    }


def run_ai_job(job_id: int) -> None:
    job = get_row("SELECT * FROM ai_jobs WHERE id=?", (job_id,))
    if not job or job.get("status") != "queued":
        return
    started = now_text()
    with connect() as conn:
        conn.execute("UPDATE ai_jobs SET status='running', started_at=? WHERE id=? AND status='queued'", (started, job_id))
        conn.commit()
    job = get_row("SELECT * FROM ai_jobs WHERE id=?", (job_id,))
    if not job or job.get("status") != "running":
        return

    parent = get_row("SELECT * FROM parents WHERE id=?", (job["parent_id"],))
    if not parent:
        error_text = "家长帐号不存在"
        with connect() as conn:
            conn.execute(
                "UPDATE ai_jobs SET status='error', error_text=?, finished_at=? WHERE id=?",
                (error_text, now_text(), job_id),
            )
            conn.commit()
        return

    try:
        if job["task_type"] == "card_generation":
            result: dict[str, Any] = {"content": "", "usage_log_id": None}
            model_error = ""
            try:
                result = generate_ai_for_student(
                    int(job["student_id"]),
                    parent,
                    {"task_type": job["task_type"], "note": job.get("note") or ""},
                )
            except Exception as exc:
                model_error = str(exc)[:500]
            result_text = result.get("content") or ""
            model_card_plan = parse_card_plan_json(result_text)
            cards = generate_cards_docx_for_student(
                int(job["student_id"]),
                int(job["parent_id"]),
                job.get("note") or "",
                model_card_plan,
            )
            if model_error:
                fallback_note = f"\n\n模型调用失败或超时：{model_error}\n系统已使用内置考点模板生成可下载DOCX。"
            elif model_card_plan:
                fallback_note = "\n\n模型已返回结构化卡片，系统已生成可下载DOCX。"
            else:
                fallback_note = "\n\n模型未返回合格卡片JSON，系统已使用内置考点模板生成可下载DOCX。"
            result_text = (
                result_text
                + fallback_note
                + "\n\n已生成7天知识卡片DOCX："
                + cards["file_url"]
            )
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE ai_jobs
                    SET status='success', result_text=?, usage_log_id=?, error_text='', finished_at=?
                    WHERE id=?
                    """,
                    (result_text, result.get("usage_log_id"), now_text(), job_id),
                )
                conn.commit()
            return

        result = generate_ai_for_student(
            int(job["student_id"]),
            parent,
            {"task_type": job["task_type"], "note": job.get("note") or ""},
        )
        result_text = result.get("content") or ""
        if job["task_type"] == "initial_plan":
            save_initial_plan_ai_result(int(job["student_id"]), result_text, job.get("note") or "")
        with connect() as conn:
            conn.execute(
                """
                UPDATE ai_jobs
                SET status='success', result_text=?, usage_log_id=?, error_text='', finished_at=?
                WHERE id=?
                """,
                (result_text, result.get("usage_log_id"), now_text(), job_id),
            )
            conn.commit()
    except Exception as exc:
        with connect() as conn:
            conn.execute(
                "UPDATE ai_jobs SET status='error', error_text=?, finished_at=? WHERE id=?",
                (str(exc)[:1000], now_text(), job_id),
            )
            conn.commit()


def enqueue_ai_job(student_id: int, parent: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    task_type = compact_text(payload.get("task_type")) or "daily_feedback"
    if task_type not in TASK_LABELS:
        raise ValueError("任务类型不正确")
    ensure_ai_quota(parent)
    get_student_for_access(student_id, int(parent["id"]))
    if task_type == "card_generation":
        ensure_card_generation_ready(student_id, int(parent["id"]), payload.get("note") or "")
    warning = ai_regeneration_warning(student_id, task_type)
    if warning and not truthy(payload.get("confirm_regenerate")):
        raise ValueError(warning + " 请确认后再重新生成。")

    created = now_text()
    with AI_SUBMIT_LOCK:
        ensure_ai_rate_limit(int(parent["id"]))
        with connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO ai_jobs (parent_id, student_id, task_type, status, note, created_at)
                VALUES (?, ?, ?, 'queued', ?, ?)
                """,
                (parent["id"], student_id, task_type, compact_text(payload.get("note")), created),
            )
            conn.commit()
            job_id = int(cur.lastrowid)
        AI_EXECUTOR.submit(run_ai_job, job_id)

    return get_ai_job(job_id, int(parent["id"]))


def classify_intake(payload: dict[str, Any]) -> dict[str, Any]:
    time_budget = compact_text(payload.get("time_budget"))
    cooperation = compact_text(payload.get("cooperation"))
    parent_support = compact_text(payload.get("parent_support"))
    difficulties = compact_text(payload.get("difficulties"))
    notes = " ".join(
        compact_text(payload.get(key))
        for key in ("goal", "exam_date_text", "exam_scope", "priority_subjects", "card_layout", "material_status", "extra_notes", "evidence_summary")
    )
    text = " ".join([time_budget, cooperation, parent_support, difficulties, notes])
    card_layout = compact_text(payload.get("card_layout"))

    low_time = any(key in text for key in ["5-10", "5分钟", "10分钟", "很少", "研学", "路上"])
    medium_time = any(key in text for key in ["15-25", "20分钟", "25分钟"])
    high_resistance = any(key in text for key in ["抗拒", "一问就慌", "烦", "崩", "不愿意", "习得性无助"])
    can_wechat = any(key in text for key in ["微信", "语音", "拍照", "3张", "三张"])
    explicit_8_grid = any(key in text for key in ["8格", "八格", "2列4行", "四科"]) or "8" in card_layout

    if explicit_8_grid:
        level = "8格路径"
        card_load = "每天8格：语文1、历史2、生物1、地理2、道法2"
        execution_mode = "A4纸面卡 + 家长抽问"
        plan_days = 7
    elif low_time or high_resistance:
        level = "微型档"
        card_load = "3张核心卡 + 1张信心卡"
        execution_mode = "微信3卡" if can_wechat else "纸面3卡"
        plan_days = 2
    elif medium_time or cooperation in ["一般", "能接受", "配合"]:
        level = "标准档"
        card_load = "4张核心卡 + 1张复背卡"
        execution_mode = "纸面卡 + 家长10分钟抽查"
        plan_days = 3
    else:
        level = "完整档"
        card_load = "6张核心卡 + 2张错卡/自测卡"
        execution_mode = "纸面卡 + 每日反馈"
        plan_days = 3

    reasons = []
    if low_time:
        reasons.append("可用时间短，先保证启动。")
    if high_resistance:
        reasons.append("有抗拒或一问就慌，先做小成功。")
    if can_wechat:
        reasons.append("家长可通过微信检查，适合研学或碎片时间。")
    if explicit_8_grid:
        reasons.append("家长明确需要8格路径，按语文1、历史2、生物1、地理2、道法2生成知识卡。")
    if not reasons:
        reasons.append("信息足够，先用试运行方案校准真实难度。")

    return {
        "initial_level": level,
        "card_load": card_load,
        "execution_mode": execution_mode,
        "trial_days": plan_days,
        "reasons": reasons,
        "next_step": "生成V1试运行方案，先跑2-3天再根据反馈调整。",
    }


def build_plan_summary(analysis: dict[str, Any], payload: dict[str, Any]) -> tuple[str, str]:
    level = analysis["initial_level"]
    load = analysis["card_load"]
    mode = analysis["execution_mode"]
    days = analysis["trial_days"]
    subjects = compact_text(payload.get("priority_subjects")) or "优先科目"
    scope = compact_text(payload.get("exam_scope"))
    material_status = compact_text(payload.get("material_status"))
    goal = compact_text(payload.get("goal")) or "阶段目标"
    parent_instruction = (
        f"先执行{days}天，不追求一次背熟。每天只问卡片关键词，孩子答出大意就算过；"
        "不会的卡标★，第二天优先回炉。遇到研学、疲劳或明显抗拒时，改为微信3卡保温，不加新卡。"
    )
    scope_text = f"｜范围：{scope}" if scope else "｜范围：版本假设版，待家长补充教材/考试范围"
    material_text = f"｜资料：{material_status}" if material_status else ""
    summary = (
        f"{goal}｜{subjects}{scope_text}{material_text}｜{level}。"
        f"建议每天{load}，执行方式：{mode}。"
        "第一版只验证孩子能不能启动、哪些卡最卡、家长监督是否顺手。"
    )
    return summary, parent_instruction


def calculate_feedback_actions(student: dict[str, Any], payload: dict[str, Any]) -> tuple[float, float, list[dict[str, str]]]:
    planned = max(int(payload.get("planned_cards") or 0), 0)
    completed = max(int(payload.get("completed_cards") or 0), 0)
    known = max(int(payload.get("known_cards") or 0), 0)
    day_mode = compact_text(payload.get("day_mode")) or "正常"
    mood = compact_text(payload.get("mood"))
    star_cards = compact_text(payload.get("star_card_codes"))

    completion_rate = round(completed / planned, 2) if planned else 0.0
    known_rate = round(known / completed, 2) if completed else 0.0

    actions: list[dict[str, str]] = []

    if day_mode in ["研学", "疲劳"] or "研学" in day_mode or "疲劳" in day_mode:
        actions.append(
            {
                "action_type": "wechat_3_cards",
                "trigger_reason": f"今天模式={day_mode}",
                "action_text": "明天不加新卡，只做微信3卡保温：先问★卡，再问昨天会的1张，最后让孩子自选1张。",
                "priority": "high",
            }
        )

    if completion_rate < 0.5:
        actions.append(
            {
                "action_type": "reduce_load",
                "trigger_reason": f"完成率={completion_rate:.0%}",
                "action_text": "下一次减到3张核心卡，答案只要求关键词，不追问细节。",
                "priority": "high",
            }
        )

    if completed > 0 and known_rate < 0.3:
        actions.append(
            {
                "action_type": "lower_difficulty",
                "trigger_reason": f"答对率={known_rate:.0%}",
                "action_text": "降难度：把长答案改成2-3个关键词，先让孩子能说出一个点。",
                "priority": "high",
            }
        )

    if star_cards:
        actions.append(
            {
                "action_type": "star_card_recycle",
                "trigger_reason": f"出现★卡={star_cards}",
                "action_text": f"明天先回炉★卡 {star_cards}，答不完整也只补关键词，不做批评。",
                "priority": "normal",
            }
        )

    if mood in ["明显抗拒", "有点烦"] or "抗拒" in mood or "烦" in mood:
        actions.append(
            {
                "action_type": "confidence_first",
                "trigger_reason": f"情绪={mood}",
                "action_text": "改成孩子自选1张开始，家长只确认“今天启动了”，先稳住信心。",
                "priority": "high",
            }
        )

    if completion_rate >= 0.8 and known_rate >= 0.8 and completed >= 3:
        actions.append(
            {
                "action_type": "keep_or_add",
                "trigger_reason": "完成率和答对率都较好",
                "action_text": "保持当前节奏；连续2天稳定后，下周再略加1张新卡。",
                "priority": "normal",
            }
        )

    if not actions:
        actions.append(
            {
                "action_type": "keep_trial",
                "trigger_reason": "反馈未触发风险规则",
                "action_text": "明天保持原计划，先复问昨天★卡，再做今日卡。",
                "priority": "normal",
            }
        )

    return completion_rate, known_rate, actions


def create_student(payload: dict[str, Any], parent_id: int | None = None) -> dict[str, Any]:
    if parent_id is not None:
        ensure_can_create_student(parent_id)
    analysis = classify_intake(payload)
    summary, parent_instruction = build_plan_summary(analysis, payload)
    created = now_text()
    raw = json.dumps(payload, ensure_ascii=False)
    difficulties = compact_text(payload.get("difficulties"))
    owner_parent_id = parent_id if parent_id is not None else payload.get("parent_id")
    student_phone = normalize_phone(payload.get("student_phone") or payload.get("student_login_phone"))
    student_password = compact_text(payload.get("student_password") or payload.get("student_login_password"))
    if student_phone or student_password:
        student_phone = validate_mainland_mobile(student_phone, "学生手机号")
        student_password = validate_password_strength(student_password, student_phone, "学生初始密码")
        existing_account = get_row("SELECT student_id FROM student_accounts WHERE phone=?", (student_phone,))
        if existing_account:
            raise ValueError("这个手机号已经绑定到其他学生")

    referral_reward = None
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO students (
              student_code, parent_id, display_name, grade_region, goal, exam_date_text,
              current_level, current_card_load, execution_mode, main_difficulties,
              status, notes, created_at, updated_at
            )
            VALUES ('TEMP', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                owner_parent_id,
                compact_text(payload.get("student_name")) or "未命名学生",
                compact_text(payload.get("grade_region")),
                compact_text(payload.get("goal")),
                compact_text(payload.get("exam_date_text")),
                analysis["initial_level"],
                analysis["card_load"],
                analysis["execution_mode"],
                difficulties,
                compact_text(payload.get("extra_notes")),
                created,
                created,
            ),
        )
        student_id = int(cur.lastrowid)
        code = make_code(student_id)
        conn.execute("UPDATE students SET student_code=? WHERE id=?", (code, student_id))
        conn.execute(
            """
            INSERT INTO intake_submissions (
              student_id, source, grade_region, goal, exam_date_text, time_budget,
              cooperation, priority_subjects, difficulties, parent_support,
              evidence_summary, extra_notes, initial_level, initial_execution_mode,
              raw_payload, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                student_id,
                compact_text(payload.get("source")) or "local_tool",
                compact_text(payload.get("grade_region")),
                compact_text(payload.get("goal")),
                compact_text(payload.get("exam_date_text")),
                compact_text(payload.get("time_budget")),
                compact_text(payload.get("cooperation")),
                compact_text(payload.get("priority_subjects")),
                difficulties,
                compact_text(payload.get("parent_support")),
                compact_text(payload.get("evidence_summary")),
                compact_text(payload.get("extra_notes")),
                analysis["initial_level"],
                analysis["execution_mode"],
                raw,
                created,
            ),
        )
        plan_cur = conn.execute(
            """
            INSERT INTO plan_versions (
              student_id, version_code, plan_type, level_name, card_load,
              execution_mode, reason, plan_summary, parent_instruction,
              status, created_at, updated_at
            )
            VALUES (?, 'V1_试运行', 'trial', ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
            """,
            (
                student_id,
                analysis["initial_level"],
                analysis["card_load"],
                analysis["execution_mode"],
                "初始建档后先做2-3天试运行。",
                summary,
                parent_instruction,
                created,
                created,
            ),
        )
        plan_id = int(plan_cur.lastrowid)
        conn.execute("UPDATE students SET current_plan_version_id=? WHERE id=?", (plan_id, student_id))
        if parent_id is not None:
            referral_reward = grant_referral_reward_if_qualified(conn, parent_id)
        conn.commit()

    student_account = {}
    if student_phone or student_password:
        student_account = upsert_student_account(student_id, student_phone, student_password)["account"]
    student_row = get_row("SELECT * FROM students WHERE id=?", (student_id,))
    evidence_note = " ".join(
        part
        for part in (
            compact_text(payload.get("grade_region")),
            compact_text(payload.get("goal")),
            compact_text(payload.get("priority_subjects")),
            difficulties,
            summary,
        )
        if part
    )
    evidence_refs = record_plan_evidence_refs(
        student_id,
        plan_id,
        collect_learning_evidence_context(student_row or {"id": student_id}, evidence_note),
        evidence_note,
    )

    return {
        "student_id": student_id,
        "student_code": code,
        "student_account": student_account,
        "initial_level": analysis["initial_level"],
        "card_load": analysis["card_load"],
        "execution_mode": analysis["execution_mode"],
        "trial_days": analysis["trial_days"],
        "reasons": analysis["reasons"],
        "next_step": analysis["next_step"],
        "referral_reward": referral_reward,
        "current_plan": {
            "plan_version_id": plan_id,
            "version_code": "V1_试运行",
            "status": "draft",
            "plan_summary": summary,
            "parent_instruction": parent_instruction,
            "evidence_ref_count": len(evidence_refs),
        },
        "evidence_refs": evidence_refs,
    }


def save_feedback(student_id: int, payload: dict[str, Any], parent_id: int | None = None) -> dict[str, Any]:
    student = get_student_for_access(student_id, parent_id)

    feedback_date = compact_text(payload.get("feedback_date")) or today_text()
    try:
        date.fromisoformat(feedback_date)
    except ValueError as exc:
        raise ValueError("feedback_date 必须是 YYYY-MM-DD") from exc

    completion_rate, known_rate, actions = calculate_feedback_actions(student, payload)
    created = now_text()
    raw = json.dumps(payload, ensure_ascii=False)
    plan_id = payload.get("plan_version_id") or student.get("current_plan_version_id")

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO daily_feedback (
              student_id, plan_version_id, feedback_date, day_mode, planned_cards,
              completed_cards, known_cards, completion_rate, known_rate,
              star_card_codes, mood, evidence_type, parent_note, raw_payload, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(student_id, feedback_date) DO UPDATE SET
              plan_version_id=excluded.plan_version_id,
              day_mode=excluded.day_mode,
              planned_cards=excluded.planned_cards,
              completed_cards=excluded.completed_cards,
              known_cards=excluded.known_cards,
              completion_rate=excluded.completion_rate,
              known_rate=excluded.known_rate,
              star_card_codes=excluded.star_card_codes,
              mood=excluded.mood,
              evidence_type=excluded.evidence_type,
              parent_note=excluded.parent_note,
              raw_payload=excluded.raw_payload,
              created_at=excluded.created_at
            """,
            (
                student_id,
                plan_id,
                feedback_date,
                compact_text(payload.get("day_mode")) or "正常",
                int(payload.get("planned_cards") or 0),
                int(payload.get("completed_cards") or 0),
                int(payload.get("known_cards") or 0),
                completion_rate,
                known_rate,
                compact_text(payload.get("star_card_codes")),
                compact_text(payload.get("mood")),
                compact_text(payload.get("evidence_type")),
                compact_text(payload.get("parent_note")),
                raw,
                created,
            ),
        )
        feedback = conn.execute(
            "SELECT * FROM daily_feedback WHERE student_id=? AND feedback_date=?",
            (student_id, feedback_date),
        ).fetchone()
        feedback_id = int(feedback["id"])
        conn.execute(
            "DELETE FROM adjustment_actions WHERE feedback_id=? AND status='pending'",
            (feedback_id,),
        )
        for action in actions:
            conn.execute(
                """
                INSERT INTO adjustment_actions (
                  student_id, feedback_id, action_type, trigger_reason,
                  action_text, priority, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    student_id,
                    feedback_id,
                    action["action_type"],
                    action["trigger_reason"],
                    action["action_text"],
                    action["priority"],
                    created,
                ),
            )
        conn.commit()

    return {
        "feedback_id": feedback_id,
        "student_id": student_id,
        "feedback_date": feedback_date,
        "completion_rate": completion_rate,
        "known_rate": known_rate,
        "actions": actions,
        "next_plan_hint": actions[0]["action_text"] if actions else "保持原计划。",
    }


def get_student_detail(student_id: int, parent_id: int | None = None) -> dict[str, Any]:
    student = get_student_for_access(student_id, parent_id)
    parent = get_row("SELECT * FROM parents WHERE id=?", (parent_id,)) if parent_id is not None else None
    intake = get_row(
        "SELECT * FROM intake_submissions WHERE student_id=? ORDER BY id DESC LIMIT 1",
        (student_id,),
    )
    plan = get_row(
        "SELECT * FROM plan_versions WHERE id=?",
        (student.get("current_plan_version_id"),),
    )
    feedback = list_rows(
        "SELECT * FROM daily_feedback WHERE student_id=? ORDER BY feedback_date DESC LIMIT 10",
        (student_id,),
    )
    actions = list_rows(
        """
        SELECT * FROM adjustment_actions
        WHERE student_id=?
        ORDER BY CASE priority WHEN 'high' THEN 0 ELSE 1 END, id DESC
        LIMIT 20
        """,
        (student_id,),
    )
    evidence = list_rows(
        """
        SELECT id, file_type, file_url, original_name, description, created_at
        FROM evidence_files
        WHERE student_id=?
        ORDER BY id DESC
        LIMIT 30
        """,
        (student_id,),
    )
    plan_evidence_refs = list_plan_evidence_refs(int(plan["id"])) if plan else []
    current_plan = apply_plan_entitlement(plan, parent)
    if current_plan:
        current_plan["evidence_ref_count"] = len(plan_evidence_refs)
    return {
        "student": student,
        "latest_intake": intake,
        "current_plan": current_plan,
        "recent_feedback": feedback,
        "adjustment_actions": actions,
        "evidence_files": evidence,
        "current_plan_evidence_refs": plan_evidence_refs,
        "student_account": student_account_public(student_account_for_student(student_id)),
        "delivery_readiness": card_generation_readiness(student, intake),
    }


def get_today(student_id: int, parent_id: int | None = None) -> dict[str, Any]:
    detail = get_student_detail(student_id, parent_id)
    student = detail["student"]
    plan = detail["current_plan"] or {}
    load = student.get("current_card_load") or "3张核心卡"
    mode = student.get("execution_mode") or "微信3卡"
    return {
        "student_id": student_id,
        "student_code": student["student_code"],
        "current_plan": {
            "plan_version_id": plan.get("id"),
            "version_code": plan.get("version_code") or "V1_试运行",
            "plan_type": plan.get("plan_type") or "trial",
            "status": plan.get("status") or "draft",
            "file_url": plan.get("file_url") or "",
            "is_locked": bool(plan.get("is_locked")),
            "upgrade_required": bool(plan.get("upgrade_required")),
            "upgrade_message": plan.get("upgrade_message") or "",
        },
        "today_task": {
            "mode": mode,
            "cards_text": f"今天按{mode}执行：{load}。先复问昨天★卡，再做今日卡；答出关键词就算过。",
            "feedback_required": True,
        },
        "parent_instruction": plan.get("parent_instruction") or "只问关键词，不追问长答案；不会的标★，第二天回炉。",
    }


def next_version_code(student_id: int, plan_type: str) -> str:
    rows = list_rows("SELECT version_code FROM plan_versions WHERE student_id=?", (student_id,))
    max_number = 0
    for row in rows:
        match = re.match(r"V(\d+)", compact_text(row.get("version_code")))
        if match:
            max_number = max(max_number, int(match.group(1)))
    suffix = {
        "trial": "试运行",
        "adjustment": "调整",
        "star_recycle": "错卡回炉",
        "wechat_3_cards": "微信3卡",
        "weekly": "周调整",
        "custom": "方案",
    }.get(plan_type, "方案")
    return f"V{max_number + 1}_{suffix}"


def build_printable_plan_html(student: dict[str, Any], plan: dict[str, Any], latest_feedback: list[dict[str, Any]]) -> str:
    feedback_lines = []
    for item in latest_feedback[:5]:
        feedback_lines.append(
            f"<li>{item.get('feedback_date')}：完成 {item.get('completed_cards')}/{item.get('planned_cards')}，"
            f"会了 {item.get('known_cards')}，情绪 {item.get('mood') or '未填'}，★卡 {item.get('star_card_codes') or '无'}</li>"
        )
    if not feedback_lines:
        feedback_lines.append("<li>暂无反馈，先按试运行方案执行并记录第一天数据。</li>")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{student.get('student_code')} {plan.get('version_code')}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Microsoft YaHei", sans-serif; margin: 28px; color: #17211f; }}
    h1 {{ font-size: 24px; margin: 0 0 8px; }}
    h2 {{ font-size: 16px; margin-top: 20px; border-bottom: 1px solid #d8e1df; padding-bottom: 6px; }}
    .meta {{ color: #52625e; line-height: 1.8; }}
    .box {{ border: 1px solid #d8e1df; padding: 14px; margin-top: 10px; }}
    li {{ margin: 8px 0; }}
    @media print {{ body {{ margin: 18mm; }} }}
  </style>
</head>
<body>
  <h1>学记教育 {plan.get('version_code')} 打印稿</h1>
  <div class="meta">
    学生：{student.get('display_name') or ''}｜编号：{student.get('student_code')}<br>
    年级/地区：{student.get('grade_region') or ''}｜目标：{student.get('goal') or ''}<br>
    档位：{plan.get('level_name') or student.get('current_level') or ''}｜卡量：{plan.get('card_load') or student.get('current_card_load') or ''}｜方式：{plan.get('execution_mode') or student.get('execution_mode') or ''}
  </div>
  <h2>本版判断</h2>
  <div class="box">{plan.get('plan_summary') or '先用小步试运行校准真实难度。'}</div>
  <h2>家长执行说明</h2>
  <div class="box">{plan.get('parent_instruction') or '只问关键词，不追问长答案；不会的标★，第二天回炉。'}</div>
  <h2>最近反馈</h2>
  <ul>{''.join(feedback_lines)}</ul>
  <h2>每日记录</h2>
  <div class="box">完成：____ / ____ 张　会了：____ 张　★卡：____________　情绪：轻松 / 能接受 / 有点烦 / 明显抗拒</div>
</body>
</html>"""


def write_plan_file(student: dict[str, Any], plan: dict[str, Any]) -> str:
    feedback = list_rows(
        "SELECT * FROM daily_feedback WHERE student_id=? ORDER BY feedback_date DESC LIMIT 5",
        (student["id"],),
    )
    student_dir = UPLOAD_DIR / str(student["student_code"])
    student_dir.mkdir(parents=True, exist_ok=True)
    safe_version = clean_filename(plan.get("version_code") or "plan")
    file_name = f"{student['student_code']}_{safe_version}.html"
    path = student_dir / file_name
    path.write_text(build_printable_plan_html(student, plan, feedback), encoding="utf-8")
    return upload_file_url(str(student["student_code"]), file_name)


def subject_bucket(text: str) -> list[str]:
    text = compact_text(text)
    subjects = []
    checks = [
        ("语文", ["语文", "古诗", "文言", "阅读", "作文"]),
        ("历史", ["历史", "隋", "唐", "宋", "元", "明", "清"]),
        ("地理", ["地理", "亚洲", "日本", "东南亚", "印度", "俄罗斯", "地图", "气候"]),
        ("生物", ["生物", "人体", "植物", "消化", "呼吸", "循环", "神经"]),
        ("道法", ["道法", "政治", "法治", "青春", "集体", "情绪"]),
        ("英语", ["英语", "单词", "语法", "句型"]),
    ]
    for subject, keys in checks:
        if any(key in text for key in keys):
            subjects.append(subject)
    if not subjects:
        subjects = ["语文", "历史", "生物", "地理", "道法"]
    return subjects[:5]


def parse_cn_count(value: str) -> int:
    value = compact_text(value)
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}
    if value.isdigit():
        return int(value)
    return digits.get(value, 0)


def explicit_subject_distribution(text: str) -> dict[str, int]:
    text = compact_text(text)
    if not text:
        return {}
    subjects = ["语文", "历史", "地理", "生物", "道法", "英语", "数学", "物理", "化学"]
    subject_re = "|".join(subjects)
    count_re = r"([1-8一二两三四五六七八])"
    distribution: dict[str, int] = {}

    for match in re.finditer(rf"((?:{subject_re})(?:[、和及与/]+(?:{subject_re}))*)\s*各\s*{count_re}\s*(?:格|张|卡)", text):
        count = parse_cn_count(match.group(2))
        if count <= 0:
            continue
        for subject in re.findall(subject_re, match.group(1)):
            distribution[subject] = count

    for subject in subjects:
        for pattern in (
            rf"{subject}\s*(?:要|占|安排|分配)?\s*{count_re}\s*(?:格|张|卡)",
            rf"{count_re}\s*(?:格|张|卡)\s*{subject}",
        ):
            match = re.search(pattern, text)
            if match:
                distribution[subject] = parse_cn_count(match.group(1))
                break

    total = sum(distribution.values())
    if 1 <= total <= 8:
        return {subject: distribution[subject] for subject in subjects if distribution.get(subject)}
    return {}


def intake_raw_payload(intake: dict[str, Any] | None) -> dict[str, Any]:
    if not intake:
        return {}
    raw = compact_text(intake.get("raw_payload"))
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def card_generation_readiness(student: dict[str, Any], intake: dict[str, Any] | None, note: str = "") -> dict[str, Any]:
    raw_payload = intake_raw_payload(intake)
    note = compact_text(note)
    grade_region = compact_text(student.get("grade_region"))
    goal = compact_text(student.get("goal"))
    note_subjects = "、".join(subject_bucket(note)) if note and any(key in note for key in ["语文", "英语", "历史", "道法", "数学", "地理", "生物", "物理", "化学", "GESP", "C++"]) else ""
    subjects = compact_text(raw_payload.get("priority_subjects")) or compact_text((intake or {}).get("priority_subjects")) or note_subjects
    scope = compact_text(raw_payload.get("exam_scope")) or compact_text(raw_payload.get("material_status")) or note
    layout = compact_text(raw_payload.get("card_layout")) or compact_text(student.get("current_card_load")) or note
    difficulties = compact_text(raw_payload.get("difficulties")) or compact_text(student.get("main_difficulties"))
    likely_junior_final = "初一" in grade_region and "期末" in goal
    if not subjects and likely_junior_final:
        subjects = "语文、历史、生物、地理、道法"
    if not layout and likely_junior_final:
        layout = "8格五科路径"
    hypothesis_ok = likely_junior_final or any(key in " ".join([scope, note, compact_text(raw_payload.get("material_status"))]) for key in ["版本假设", "暂时没有", "没有资料", "先做"])

    missing = []
    if not grade_region:
        missing.append("年级/地区")
    if not goal:
        missing.append("目标")
    if not subjects and not note:
        missing.append("必须覆盖科目/考试")
    if not scope and not hypothesis_ok:
        missing.append("教材/考试范围，或明确先做版本假设版")
    if not layout:
        missing.append("期望卡片版式")

    warnings = []
    if not difficulties:
        warnings.append("尚未填写最大困难，卡片难度只能按默认基础版处理。")
    if likely_junior_final and not compact_text(raw_payload.get("exam_scope")):
        warnings.append("旧档案缺少考试范围，已按初一七下期末四科版本假设版处理。")
    if hypothesis_ok:
        warnings.append("当前会按版本假设版生成，家长补充复习范围后建议再校准。")

    return {
        "ready": not missing,
        "missing": missing,
        "warnings": warnings,
        "subjects": subjects,
        "scope": scope,
        "layout": layout,
        "hypothesis_ok": hypothesis_ok,
    }


def ensure_card_generation_ready(student_id: int, parent_id: int | None, note: str = "") -> None:
    student = get_student_for_access(student_id, parent_id)
    intake = get_row("SELECT * FROM intake_submissions WHERE student_id=? ORDER BY id DESC LIMIT 1", (student_id,))
    readiness = card_generation_readiness(student, intake, note)
    if not readiness["ready"]:
        raise ValueError("生成知识卡片前请先补充：" + "、".join(readiness["missing"]))


def expected_card_profile(student: dict[str, Any], intake: dict[str, Any] | None, note: str = "") -> dict[str, Any]:
    raw_payload = intake_raw_payload(intake)
    text = " ".join(
        compact_text(item)
        for item in [
            raw_payload.get("priority_subjects"),
            raw_payload.get("exam_scope"),
            raw_payload.get("card_layout"),
            student.get("current_card_load"),
            student.get("goal"),
            note,
        ]
    )
    subjects = subject_bucket(text)
    explicit_distribution = explicit_subject_distribution(text)
    if explicit_distribution:
        return {
            "days": 7,
            "cards_per_day": sum(explicit_distribution.values()),
            "subjects": list(explicit_distribution.keys()),
            "distribution": explicit_distribution,
            "strict_distribution": True,
        }
    explicit_8 = any(key in text for key in ["8格", "八格", "2列4行", "四科"])
    if explicit_8:
        cards_per_day = 8
    elif any(key in text for key in ["6张", "6格", "完整档"]):
        cards_per_day = 6
    elif any(key in text for key in ["4-5", "5张", "5格", "标准档"]):
        cards_per_day = 5
    elif any(key in text for key in ["3张", "3格", "微信3卡", "纸面3卡", "微型档"]):
        cards_per_day = 4
    else:
        cards_per_day = None
    if explicit_8 and all(subject in subjects for subject in ["语文", "历史", "生物", "地理", "道法"]) and not any(key in text for key in ["偏重", "多一点", "多几个", "重点", "优先"]):
        return {
            "days": 7,
            "cards_per_day": cards_per_day,
            "subjects": ["语文", "历史", "地理", "生物", "道法"],
            "distribution": {"语文": 1, "历史": 2, "生物": 1, "地理": 2, "道法": 2},
            "strict_distribution": True,
        }
    if explicit_8:
        return {"days": 7, "cards_per_day": cards_per_day, "subjects": subjects, "distribution": {}, "strict_distribution": False}
    return {"days": 7, "cards_per_day": cards_per_day, "subjects": subjects, "distribution": {}, "strict_distribution": False}


def card_subject(card: dict[str, Any]) -> str:
    text = compact_text(card.get("tag")) + " " + compact_text(card.get("q")) + " " + compact_text(card.get("a"))
    for subject in ["语文", "英语", "历史", "道法", "数学", "地理", "生物", "物理", "化学"]:
        if subject in text:
            return subject
    if any(key in text for key in ["古诗", "文言", "阅读", "作文", "名著"]):
        return "语文"
    if any(key in text for key in ["English", "Can ", "How ", "What ", "There", "Did "]):
        return "英语"
    if any(key in text for key in ["隋", "唐", "宋", "元", "明", "清", "科举", "大运河"]):
        return "历史"
    if any(key in text for key in ["法律", "青春", "情绪", "集体", "未成年人", "规则"]):
        return "道法"
    return ""


def memory_cue_for_card(card: dict[str, Any], day: int) -> str:
    subject = card_subject(card)
    text = compact_text(card.get("tag")) + " " + compact_text(card.get("q")) + " " + compact_text(card.get("a"))
    if subject == "英语":
        return ["看图问一句", "先听再跟读", "句框不换骨架", "把时间词圈出来"][(day - 1) % 4]
    if subject == "历史":
        if any(key in text for key in ["隋", "唐", "宋", "元", "明", "清", "朝代"]):
            return "朝代楼梯：隋唐宋元明清"
        return "时间-人物-事件-影响四格"
    if subject == "语文":
        if any(key in text for key in ["默写", "古诗", "文言", "实词"]):
            return "易错字圈红再默一遍"
        return "题目-原文-关键词三步"
    if subject == "道法":
        return "是什么-为什么-怎么做三格"
    if subject in {"地理", "生物"}:
        return "图像位置先定位"
    return "先说关键词，再补一句"


def enrich_memory_cues(cards: list[dict[str, str]], day: int) -> list[dict[str, str]]:
    enriched = []
    for card in cards:
        answer = compact_text(card.get("a"))
        if "线索" not in answer and len(answer) <= 105:
            answer = f"{answer} 线索：{memory_cue_for_card(card, day)}。"
        enriched.append({**card, "a": answer[:130]})
    return enriched


def validate_card_plan(card_plan: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    issues = []
    days = card_plan.get("days") if isinstance(card_plan, dict) else None
    if not isinstance(days, list) or len(days) != int(profile.get("days") or 7):
        issues.append("必须生成7天卡片")
        return issues
    cards_per_day = profile.get("cards_per_day")
    generic_words = ["怎么用", "家长怎么", "最低完成标准", "先启动", "保温", "不批评", "信心卡"]
    generic_count = 0
    for idx, day in enumerate(days, start=1):
        cards = day.get("cards") if isinstance(day, dict) else None
        if not isinstance(cards, list) or not cards:
            issues.append(f"Day {idx:02d} 缺少卡片")
            continue
        if cards_per_day and len(cards) != int(cards_per_day):
            issues.append(f"Day {idx:02d} 需要{cards_per_day}张卡，实际{len(cards)}张")
        subject_counts: dict[str, int] = {}
        for card in cards:
            q = compact_text(card.get("q")) if isinstance(card, dict) else ""
            a = compact_text(card.get("a")) if isinstance(card, dict) else ""
            tag = compact_text(card.get("tag")) if isinstance(card, dict) else ""
            if not tag or not q or not a:
                issues.append(f"Day {idx:02d} 有卡片缺少编号、问题或答案")
                continue
            if len(q) > 90 or len(a) > 130:
                issues.append(f"Day {idx:02d}「{tag[:12]}」文字过长")
            if any(word in q + a + tag for word in generic_words):
                generic_count += 1
            subject = card_subject(card)
            if subject:
                subject_counts[subject] = subject_counts.get(subject, 0) + 1
        if profile.get("strict_distribution"):
            for subject, expected in profile.get("distribution", {}).items():
                if subject_counts.get(subject, 0) != expected:
                    issues.append(f"Day {idx:02d} {subject} 应为{expected}张，实际{subject_counts.get(subject, 0)}张")
    if generic_count > 2:
        issues.append("泛泛方法类卡片过多，缺少具体考点")
    return issues


def subject_priority_weights(subjects: list[str], text: str) -> dict[str, int]:
    weights = {subject: 1 for subject in subjects}
    text = compact_text(text)

    def near_marker(subject: str, marker: str, window: int = 10) -> bool:
        other_subjects = [item for item in subjects if item and item != subject]

        def clean_relation(segment: str) -> bool:
            return not any(other in segment for other in other_subjects)

        for match in re.finditer(re.escape(subject), text):
            left = max(0, match.start() - window)
            right = min(len(text), match.end() + window)
            segment = text[left:right]
            if marker in segment and clean_relation(segment):
                return True
        for match in re.finditer(re.escape(marker), text):
            left = max(0, match.start() - window)
            right = min(len(text), match.end() + window)
            segment = text[left:right]
            if subject in segment and clean_relation(segment):
                return True
        return False

    for subject in subjects:
        if not subject:
            continue
        if subject in text:
            weights[subject] += text.count(subject)
        for marker in ["偏重", "重点", "优先", "多一点", "多几个", "加强", "主攻", "先救"]:
            if near_marker(subject, marker):
                weights[subject] += 2
    return weights


def allocate_subject_counts(subjects: list[str], desired_count: int, preference_text: str = "") -> dict[str, int]:
    subjects = subjects or ["语文"]
    desired_count = max(1, min(int(desired_count), 8))
    if len(subjects) == 1:
        return {subjects[0]: desired_count}
    weights = subject_priority_weights(subjects, preference_text)
    counts = {subject: 0 for subject in subjects}
    for subject in subjects[:desired_count]:
        counts[subject] = 1
    remaining = desired_count - sum(counts.values())
    order = sorted(subjects, key=lambda item: (-weights.get(item, 1), subjects.index(item)))
    boosted = [subject for subject in order if weights.get(subject, 1) > min(weights.values())]
    for subject in boosted:
        if remaining <= 0:
            break
        counts[subject] += 1
        remaining -= 1
    idx = 0
    while remaining > 0:
        subject = order[idx % len(order)]
        counts[subject] += 1
        remaining -= 1
        idx += 1
    return counts


def extra_cards_for_subject(subject: str, day: int) -> list[tuple[str, str, str]]:
    extras = {
        "语文": [
            ("语文3｜名句默写", "今天最该防哪类默写错？", "先圈易错字，再完整写1遍。重点查：形近字、通假字、上下句衔接。"),
            ("语文4｜文言实词", "文言词不会时先背什么？", "只背课下注释核心义，再用1个短句复述。不要背长解释。"),
            ("语文5｜阅读标题", "标题作用题先答哪3点？", "概括内容、点明线索、表达主题。结合文章选2点，不要全套硬背。"),
            ("语文6｜人物形象", "人物形象题怎么不空泛？", "性格词 + 事件依据。例：勇敢，因为他在困难时主动承担。"),
            ("语文7｜作文审题", "作文题先圈哪3个词？", "对象、事件、情感。先确定写谁、写什么事、最后表达什么变化。"),
            ("语文8｜错字回炉", "默写错字当天怎么处理？", "错字单独写3遍，再把整句写1遍；第二天先问这句。"),
        ],
        "英语": [
            ("英语3｜关键词认读", "英语基础卡先做到什么？", "先会认关键词和句型，不强迫整段背。看到词能说中文就算过。"),
            ("英语4｜句型替换", "句型题怎么练？", "保留句子框架，只替换主语/时间/地点。先模仿，再独立写。"),
            ("英语5｜一般疑问句", "有助动词 did/can/do 时怎么回答？", "问什么助动词，就用什么答：Yes, I did/can/do. No, I didn't/can't/don't."),
            ("英语6｜时态提示词", "看到 yesterday/last week 用什么时态？", "一般过去时。规则动词加-ed，不规则动词单独记。"),
            ("英语7｜易混介词", "时间点和地点常用哪些介词？", "at + 时间点；in + 月/年/大地点；on + 星期/具体日期。"),
            ("英语8｜小作文句", "英语小作文先保哪3句？", "I went... / I saw... / I felt... 先写简单正确句，再加细节。"),
        ],
        "历史": [
            ("历史3｜时间线", "历史题先抓哪条线？", "朝代顺序：隋 -> 唐 -> 宋 -> 元 -> 明 -> 清。先定位朝代再答事件。"),
            ("历史4｜意义题", "意义题常用哪3个词？", "促进、加强、推动。答题要写对谁有什么影响。"),
            ("历史5｜制度题", "制度题怎么答作用？", "先写制度是什么，再写加强管理/选拔人才/巩固统一。"),
            ("历史6｜民族交往", "民族关系题不要只写什么？", "不要只写战争，还要写交流、交融、共同发展。"),
            ("历史7｜经济题", "经济重心南移看哪3点？", "农业、手工业、商业；南宋完成，财政收入主要来自南方。"),
            ("历史8｜易错回炉", "历史错卡怎么复背？", "只背4词：时间、人物、事件、意义；第二天先问意义。"),
        ],
        "道法": [
            ("道法3｜是什么", "道法概念题先答什么？", "先答关键词定义，再补一句表现。不要只写自己的感受。"),
            ("道法4｜为什么", "为什么题怎么组织？", "从个人成长、他人/集体、社会规则三个角度选2个答。"),
            ("道法5｜怎么做", "青少年怎么做题常用哪3步？", "树立意识、落实行动、宣传带动。每步一句短话。"),
            ("道法6｜易混概念", "自信、自强、独立、批判怎么区分？", "自信敢尝试；自强能坚持；独立有见解；批判有依据地质疑。"),
            ("道法7｜材料题", "材料题答案从哪里来？", "先找材料关键词，再对应课本概念，最后写做法或意义。"),
            ("道法8｜错卡回炉", "道法错卡怎么降难度？", "每题只留2-3个关键词，先能说出大意，再补完整句。"),
        ],
    }
    return extras.get(subject, extras["语文"])


def subject_cards_for_day(subject: str, day: int, grade_region: str, count: int) -> list[dict[str, str]]:
    cards = card_templates_for_subject(subject, day, grade_region)
    for tag, q, a in extra_cards_for_subject(subject, day):
        if len(cards) >= count:
            break
        cards.append({"tag": tag, "q": q, "a": a})
    while len(cards) < count:
        idx = len(cards) + 1
        cards.append(
            {
                "tag": f"{subject}{idx}｜复习自测",
                "q": f"今天{subject}最需要回炉的一个点是什么？",
                "a": "写下关键词并盖住答案复述；不会就标★，第二天先问这张。",
            }
        )
    return cards[:count]


def select_daily_cards(subjects: list[str], day: int, grade_region: str, desired_count: int | None, preference_text: str = "") -> list[dict[str, str]]:
    subject_cards = [(subject, card_templates_for_subject(subject, day, grade_region)) for subject in subjects]
    if desired_count is None:
        desired_count = sum(len(cards) for _, cards in subject_cards)
    desired_count = max(1, min(int(desired_count), 8))
    counts = allocate_subject_counts(subjects, desired_count, preference_text)
    grouped = []
    for subject in subjects:
        count = counts.get(subject, 0)
        if count:
            grouped.extend(subject_cards_for_day(subject, day, grade_region, count))
    if len(grouped) >= desired_count:
        return enrich_memory_cues(grouped[:desired_count], day)
    selected: list[dict[str, str]] = []
    for _, cards in subject_cards:
        if cards and len(selected) < desired_count:
            selected.append(cards[0])
    second_round = 1
    while len(selected) < desired_count:
        added = False
        for _, cards in subject_cards:
            if len(cards) > second_round and len(selected) < desired_count:
                selected.append(cards[second_round])
                added = True
        if not added:
            break
        second_round += 1
    return enrich_memory_cues(selected[:desired_count], day)


def card_templates_for_subject(subject: str, day: int, grade_region: str) -> list[dict[str, str]]:
    chinese = [
        [
            ("语文1｜《孙权劝学》", "吕蒙前后变化说明什么？", "关键词：学习改变人；吴下阿蒙 -> 刮目相待。答题句：通过吕蒙变化，说明开卷有益。"),
            ("语文2｜《木兰诗》", "木兰形象抓哪3个词？", "孝顺、勇敢、谨慎。例：替父从军是孝，征战多年是勇，归来不贪功名。"),
        ],
        [
            ("语文1｜《卖油翁》", "卖油翁告诉我们什么道理？", "熟能生巧。答题时写：陈尧咨善射，卖油翁酌油，说明本领来自长期练习。"),
            ("语文2｜互文句", "“将军百战死，壮士十年归”怎么理解？", "互文：将军和壮士都经历多年征战，有人战死，有人归来。不要理解成只有将军死。"),
        ],
        [
            ("语文1｜《陋室铭》", "《陋室铭》的中心句是什么？", "斯是陋室，惟吾德馨。主旨：居室简陋不要紧，品德高尚才可贵。"),
            ("语文2｜《爱莲说》", "莲象征君子哪3种品质？", "洁身自好、不与世俗同流合污、正直端庄。关键词：出淤泥而不染。"),
        ],
        [
            ("语文1｜《河中石兽》", "找石兽为什么不能只凭想象？", "要联系实际，综合分析水流、泥沙、石性。主旨：实践出真知，不能主观臆断。"),
            ("语文2｜古诗哲理", "《登飞来峰》常考哲理是什么？", "站得高才能看得远；不怕眼前困难遮挡。关键词：高处、远见、不畏阻碍。"),
        ],
        [
            ("语文1｜人物形象", "阅读题问“人物形象”怎么答？", "公式：性格词 + 事件依据。例：他善良，因为主动帮助别人。不能只写“很好”。"),
            ("语文2｜标题作用", "标题作用常答哪3点？", "概括内容、点明线索、表达主题/情感。先看标题是否是人物、物品、地点或事件。"),
        ],
        [
            ("语文1｜名著《骆驼祥子》", "祥子人生三起三落最后说明什么？", "旧社会压迫劳动者，个人奋斗难以改变命运。人物变化：勤劳上进 -> 麻木堕落。"),
            ("语文2｜作文审题", "期末作文先圈哪3类词？", "对象、事件、情感。先写清“谁经历什么”，再写“我明白了什么”。"),
        ],
        [
            ("语文1｜古诗文总复习", "默写题最后10分钟怎么查？", "只查易错字：馨、淤、濯、蔓、亵、蕃、烽、凌。错字单独写3遍。"),
            ("语文2｜阅读答题", "阅读题答案从哪里来？", "先回原文找句子，再提关键词，最后用自己的话连成短句。不要脱离原文乱编。"),
        ],
    ]
    english = [
        [
            ("英语1｜can句型", "“你会游泳吗？”怎么问答？", "Can you swim? Yes, I can. / No, I can't. 句型：Can + 主语 + 动词原形？"),
            ("英语2｜时间表达", "问作息时间怎么说？", "What time do you get up? I get up at six thirty. at + 具体时间。"),
        ],
        [
            ("英语1｜交通方式", "问“怎样上学”怎么说？", "How do you get to school? I ride a bike. / I go by bus. on foot=步行。"),
            ("英语2｜多远多久", "How long 和 How far 区别？", "How long 问多久：ten minutes。How far 问多远：two kilometers。"),
        ],
        [
            ("英语1｜校规祈使句", "“不要在走廊跑”怎么写？", "Don't run in the hallways. 规则句常用 Don't + 动词原形。"),
            ("英语2｜must/have to", "must 和 have to 都表示什么？", "都表示必须。I must be on time. I have to wear a uniform. 后接动词原形。"),
        ],
        [
            ("英语1｜现在进行时", "“他正在读书”怎么写？", "He is reading. 结构：am/is/are + doing。now, Look! 常提示进行时。"),
            ("英语2｜天气问答", "问天气怎么说？", "How's the weather? = What's the weather like? It's sunny/raining/cloudy."),
        ],
        [
            ("英语1｜There be", "“附近有一家银行”怎么写？", "There is a bank near here. 单数用 is，复数用 are。"),
            ("英语2｜问路位置", "“在……前面/对面”怎么说？", "in front of 在前面；across from 在对面；next to 紧挨着。"),
        ],
        [
            ("英语1｜外貌描述", "问长相怎么说？", "What does he look like? He is tall. He has short hair. 身高用 is，头发用 has。"),
            ("英语2｜点餐句型", "“你想要什么？”怎么问？", "What would you like? I'd like beef noodles. would like 比 want 更礼貌。"),
        ],
        [
            ("英语1｜一般过去时", "过去发生的事动词怎么变？", "规则加 -ed：played, cleaned。不规则要记：go-went, see-saw, do-did。"),
            ("英语2｜did问句", "“你上周去了吗？”怎么问答？", "Did you go last week? Yes, I did. / No, I didn't. did 后动词用原形。"),
        ],
    ]
    history = [
        [
            ("历史1｜隋朝统一", "隋朝统一的意义是什么？", "结束长期分裂，重新实现全国统一，为唐朝繁盛奠定基础。关键词：统一、稳定、发展。"),
            ("历史2｜大运河", "隋朝大运河作用怎么答？", "加强南北交通，巩固统治，促进经济文化交流。中心：洛阳；连接海河、黄河、淮河、长江、钱塘江。"),
        ],
        [
            ("历史1｜科举制", "科举制为什么重要？", "用考试选官，扩大选官范围，促进社会阶层流动，加强中央集权。"),
            ("历史2｜贞观之治", "唐太宗出现治世的原因？", "吸取隋亡教训，虚心纳谏，重用贤才，轻徭薄赋，完善制度。"),
        ],
        [
            ("历史1｜开元盛世", "唐玄宗前期为什么称盛世？", "政治稳定、经济繁荣、国力强盛。关键词：整顿吏治、发展生产、文教昌盛。"),
            ("历史2｜文成公主入藏", "文成公主入藏的意义？", "促进唐蕃经济文化交流，密切汉藏关系。答题关键词：民族交往、融合。"),
        ],
        [
            ("历史1｜宋代重文轻武", "宋朝为什么重文轻武？", "防止武将专权，加强中央集权。影响：文官地位提高，但军队战斗力受影响。"),
            ("历史2｜经济重心南移", "经济重心南移完成于何时？", "南宋完成。表现：南方农业、手工业、商业发展，财政收入主要来自南方。"),
        ],
        [
            ("历史1｜辽宋夏金元", "民族政权并立说明什么？", "各民族政权有战有和，民族交融加强。不能只背战争，还要记交流。"),
            ("历史2｜元朝行省制度", "行省制度的作用是什么？", "加强对辽阔疆域的管理，巩固统一，是我国省级行政区制度的开端。"),
        ],
        [
            ("历史1｜明朝皇权", "明太祖如何加强皇权？", "废丞相和中书省，权分六部；设厂卫特务机构。关键词：皇权加强。"),
            ("历史2｜郑和/戚继光", "郑和下西洋和戚继光抗倭分别记什么？", "郑和：和平交往、增进联系。戚继光：抗击倭寇，保卫东南沿海。"),
        ],
        [
            ("历史1｜清朝统一多民族国家", "清朝怎样巩固边疆？", "台湾设府，册封达赖班禅，设驻藏大臣，平定叛乱，管辖新疆。"),
            ("历史2｜闭关锁国", "闭关锁国影响怎么答？", "短期有防御作用，长期限制对外交流，使中国逐渐落后于世界潮流。"),
        ],
    ]
    geography = [
        [
            ("地理1｜亚洲位置", "亚洲为什么说位置优越？", "答：跨纬度广、海陆兼备、面积大。法：先答位置特点，再说对气候和交流的影响。"),
            ("地理2｜日本工业", "日本工业为什么多分布在太平洋沿岸？", "答：港口多，便于进口原料、出口产品。法：抓资源少、市场外向、海运便利。"),
        ],
        [
            ("地理1｜东南亚气候", "东南亚为什么适合水稻种植？", "答：热量充足、降水多、劳动力丰富。法：从气候和人口两方面答。"),
            ("地理2｜中东水资源", "中东为什么水资源紧张？", "答：热带沙漠气候广，降水少，蒸发强。法：先说气候，再说河流少。"),
        ],
        [
            ("地理1｜印度季风", "印度水旱灾害为什么频繁？", "答：西南季风不稳定，来得早晚和强弱变化大。法：关键词是季风不稳定。"),
            ("地理2｜俄罗斯资源", "俄罗斯自然资源对工业有什么影响？", "答：煤、铁、石油、天然气丰富，利于发展重工业。法：资源题连到工业部门。"),
        ],
        [
            ("地理1｜俄罗斯交通", "俄罗斯交通为什么欧洲部分更密集？", "答：人口城市多、经济发达，铁路网更密集。法：交通看人口和经济。"),
            ("地理2｜欧洲西部经济", "欧洲西部发展畜牧业有什么优势？", "答：温带海洋性气候，草场广布，居民喜食乳肉。法：气候草场饮食三点。"),
        ],
        [
            ("地理1｜撒哈拉以南非洲", "非洲很多国家经济为什么较单一？", "答：长期受殖民影响，出口初级农矿产品为主。法：抓单一商品经济。"),
            ("地理2｜澳大利亚", "澳大利亚为什么被称为“骑在羊背上的国家”？", "答：绵羊数量多，畜牧业发达。法：称号题先解释称号含义。"),
        ],
        [
            ("地理1｜巴西", "巴西人口和城市主要分布在哪里？", "答：东南沿海。法：原因从气候、交通、开发历史和经济答。"),
            ("地理2｜极地地区", "南极地区为什么酷寒？", "答：纬度高、海拔高、冰雪反射强。法：自然原因题分点答。"),
        ],
        [
            ("地理1｜区域原因题", "地理原因题常从哪4方面想？", "答：位置、气候、地形、资源/交通。法：先自然，后人文。"),
            ("地理2｜区域措施题", "区域发展措施题怎么答不空？", "答：因地制宜、保护环境、改善交通、发展特色产业。法：措施要对应问题。"),
        ],
    ]
    biology = [
        [
            ("生物1｜血液", "血液中能运输氧的是哪种细胞？白细胞主要作用是什么？", "答：红细胞运输氧；白细胞防御和保护。法：红运氧，白防御。"),
            ("生物2｜血管", "动脉、静脉、毛细血管怎样区分？", "答：动脉离心，静脉回心，毛细血管利于物质交换。法：方向+功能。"),
        ],
        [
            ("生物1｜心脏", "心脏瓣膜有什么作用？", "答：防止血液倒流，保证血液按一定方向流动。法：瓣膜题抓防倒流。"),
            ("生物2｜血液循环", "肺循环和体循环分别完成什么？", "答：肺循环完成气体交换；体循环把氧和营养送到全身。法：肺换气，体供给。"),
        ],
        [
            ("生物1｜呼吸", "肺泡适合气体交换有哪些特点？", "答：数量多、壁薄、外包毛细血管。法：面积大、距离短、血管多。"),
            ("生物2｜消化", "淀粉、蛋白质、脂肪分别在哪里开始消化？", "答：淀粉口腔，蛋白质胃，脂肪小肠。法：按营养物质对应器官记。"),
        ],
        [
            ("生物1｜吸收", "小肠为什么是吸收的主要场所？", "答：长、有皱襞和小肠绒毛、毛细血管丰富。法：面积大、壁薄、血管多。"),
            ("生物2｜排泄", "人体排泄的主要器官是什么？", "答：肾脏。形成尿液，排出尿素等废物。法：排泄不等于排便。"),
        ],
        [
            ("生物1｜神经调节", "神经调节的基本方式是什么？", "答：反射。结构基础是反射弧。法：看到刺激—反应就想反射。"),
            ("生物2｜激素调节", "胰岛素分泌不足会导致什么？", "答：血糖升高，可能患糖尿病。法：胰岛素管血糖。"),
        ],
        [
            ("生物1｜眼和耳", "近视眼成像在哪里？怎样矫正？", "答：成像在视网膜前方；戴凹透镜矫正。法：近前凹，远后凸。"),
            ("生物2｜健康", "传染病流行需要哪3个环节？", "答：传染源、传播途径、易感人群。法：控制任一环节都能预防。"),
        ],
        [
            ("生物1｜功能题", "生物结构和功能题怎么答？", "答：先说结构特点，再说它有利于什么功能。法：结构服务功能。"),
            ("生物2｜实验题", "对照实验只能改变几个变量？", "答：一个变量。其他条件保持相同。法：单一变量、公平比较。"),
        ],
    ]
    daofa = [
        [
            ("道法1｜青春期变化", "青春期身体变化应怎样对待？", "正视并悦纳，不自卑不嘲笑；注意卫生、锻炼和作息。关键词：悦纳、尊重。"),
            ("道法2｜独立思维", "独立思维是不是一味反对？", "不是。独立思维是有自己见解，同时接纳他人合理意见。不是任性和顶嘴。"),
        ],
        [
            ("道法1｜批判精神", "批判精神怎么做才正确？", "敢于质疑，但要有依据、讲方法、考虑他人感受。不是攻击别人。"),
            ("道法2｜自信自强", "自强靠什么？", "不断克服弱点、战胜自己、超越自己。自信让我们有勇气尝试。"),
        ],
        [
            ("道法1｜男生女生", "男生女生怎样优势互补？", "认识各自优势，互相学习，正常交往，做到内心坦荡、言谈得当。"),
            ("道法2｜情绪影响", "情绪会带来什么影响？", "积极情绪帮助发挥水平；负面情绪可能影响学习和人际。要学会调节。"),
        ],
        [
            ("道法1｜调节情绪", "调节情绪常用哪4法？", "改变认知评价、转移注意、合理宣泄、放松训练。先说方法名再举例。"),
            ("道法2｜美好情感", "美好情感从哪里来？", "阅读、交往、参与有意义活动、帮助他人。情感让生活更丰富。"),
        ],
        [
            ("道法1｜集体力量", "集体力量从哪里来？", "共同目标和团结协作。个人力量在集体中汇聚，会变得更强大。"),
            ("道法2｜个人与集体", "个人利益和集体利益冲突怎么办？", "坚持集体主义，承认个人合理利益，反对只顾自己的极端个人主义。"),
        ],
        [
            ("道法1｜集体规则", "为什么要遵守集体规则？", "规则保证集体和谐有序，也保障每个人利益。不同意见可合理表达。"),
            ("道法2｜小群体", "小群体一定不好吗？", "不一定。积极小群体能互助进步；若变成小团体主义，就会损害集体。"),
        ],
        [
            ("道法1｜法律特征", "法律的3个基本特征？", "由国家制定或认可；靠国家强制力保证实施；对全体社会成员具有普遍约束力。"),
            ("道法2｜未成年人保护", "保护未成年人有哪些防线？", "家庭、学校、社会、网络、政府、司法保护。青少年也要依法办事、遇事找法。"),
        ],
    ]
    mapping = {"语文": chinese, "英语": english, "历史": history, "地理": geography, "生物": biology, "道法": daofa}
    daily_cards = mapping.get(subject, chinese)
    return [
        {"tag": tag, "q": q, "a": a}
        for tag, q, a in daily_cards[(day - 1) % len(daily_cards)]
    ]


def build_weekly_card_plan(student: dict[str, Any], intake: dict[str, Any] | None, adjustment_note: str = "") -> dict[str, Any]:
    intake = intake or {}
    raw_payload = intake_raw_payload(intake)
    adjustment_note = compact_text(adjustment_note)
    priority = "、".join(
        item
        for item in [
            compact_text(raw_payload.get("priority_subjects")),
            compact_text(raw_payload.get("exam_scope")),
            compact_text(raw_payload.get("card_layout")),
            compact_text(intake.get("priority_subjects")),
            compact_text(student.get("goal")),
            adjustment_note,
        ]
        if item
    ) or "语文、历史、生物、地理、道法"
    subjects = subject_bucket(priority)
    grade_region = compact_text(student.get("grade_region"))
    scope = compact_text(raw_payload.get("exam_scope")) or "七年级下册期末范围待确认"
    profile = expected_card_profile(student, intake, adjustment_note)
    desired_count = profile.get("cards_per_day") or 8
    if profile.get("strict_distribution") and profile.get("distribution"):
        subjects = [subject for subject in profile.get("subjects", []) if profile["distribution"].get(subject)]
    title = f"{student.get('student_code')}｜{student.get('display_name') or '学生'}｜7天知识卡"
    note_line = f"｜已参考家长建议：{adjustment_note[:42]}" if adjustment_note else ""
    days = []
    for day in range(1, 8):
        if profile.get("strict_distribution") and profile.get("distribution"):
            cards = []
            for subject in subjects:
                count = int(profile["distribution"].get(subject, 0))
                if count:
                    cards.extend(subject_cards_for_day(subject, day, grade_region, count))
            cards = enrich_memory_cues(cards[: int(desired_count)], day)
        else:
            cards = select_daily_cards(subjects, day, grade_region, int(desired_count), priority)
        subject_text = " + ".join(subjects)
        days.append(
            {
                "title": f"Day {day:02d}｜小步重复抢分页",
                "module": f"{grade_region or '年级待确认'}｜{scope[:34]}｜{subject_text}｜小步重复+线索记忆｜每天{desired_count}张卡{note_line}",
                "cards": cards,
            }
        )
    return {"title": title, "days": days}


def english_voice_text_for_card(card: dict[str, Any]) -> str:
    if card_subject(card) != "英语":
        return ""
    source = f"{compact_text(card.get('q'))} {compact_text(card.get('a'))}"
    candidates = re.findall(r"[A-Za-z][A-Za-z' ]{2,}[?.!]*", source)
    for item in candidates:
        target = re.sub(r"\s+", " ", item).strip(" ;,，。")
        words = target.split()
        if not target or len(target) > 44 or len(words) > 8:
            continue
        if target.lower() in {"yes", "no"}:
            continue
        return target
    return ""


def prepare_weekly_card_audio(student: dict[str, Any], card_plan: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(card_plan, dict):
        return card_plan
    cache: dict[str, str] = {}
    for day in card_plan.get("days", []):
        if not isinstance(day, dict):
            continue
        cards = day.get("cards", [])
        if not isinstance(cards, list):
            continue
        for card in cards:
            if not isinstance(card, dict):
                continue
            text = compact_text(card.get("audio_text")) or english_voice_text_for_card(card)
            if not text:
                continue
            audio_url = cache.get(text)
            if audio_url is None:
                try:
                    result = synthesize_voice_practice_tts(int(student["id"]), {"text": text, "audio_format": "wav"}, None)
                    audio_url = compact_text(result.get("audio_url"))
                except Exception:
                    audio_url = ""
                cache[text] = audio_url
            card["audio_text"] = text
            card["audio_url"] = audio_url
    return card_plan


def write_cards_docx(student: dict[str, Any], card_plan: dict[str, Any]) -> str:
    import subprocess

    student_dir = UPLOAD_DIR / str(student["student_code"])
    student_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"{student['student_code']}_V1_7天知识卡片.docx"
    path = student_dir / file_name
    generator = ROOT / "generate_cards_docx.py"
    if generator.exists():
        payload_path = student_dir / f"{student['student_code']}_V1_7天知识卡片.json"
        payload_path.write_text(json.dumps(card_plan, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            subprocess.run(
                [sys.executable, str(generator), str(payload_path), str(path)],
                check=True,
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"DOCX母板生成失败：{(exc.stderr or exc.stdout or str(exc))[:400]}") from exc
        return upload_file_url(str(student["student_code"]), file_name)

    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_ROW_HEIGHT_RULE
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    def cm_twips(value: float) -> int:
        return int(Cm(value).twips)

    def set_run(run: Any, size: float = 9, bold: bool = False) -> None:
        run.font.name = "Microsoft YaHei"
        run.font.size = Pt(size)
        run.bold = bold
        r_pr = run._element.get_or_add_rPr()
        r_fonts = r_pr.rFonts
        if r_fonts is None:
            r_fonts = OxmlElement("w:rFonts")
            r_pr.append(r_fonts)
        for key in ("eastAsia", "ascii", "hAnsi"):
            r_fonts.set(qn(f"w:{key}"), "Microsoft YaHei")

    def para(p: Any, line: float = 12, after: float = 0, before: float = 0, align: Any = None) -> None:
        p.paragraph_format.space_before = Pt(before)
        p.paragraph_format.space_after = Pt(after)
        p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
        p.paragraph_format.line_spacing = Pt(line)
        if align is not None:
            p.alignment = align

    def add(p: Any, text: Any, size: float = 9, bold: bool = False) -> Any:
        run = p.add_run(str(text))
        set_run(run, size=size, bold=bold)
        return run

    def margins(cell: Any, top: int = 80, start: int = 90, bottom: int = 70, end: int = 90) -> None:
        tc_pr = cell._tc.get_or_add_tcPr()
        tc_mar = tc_pr.first_child_found_in("w:tcMar")
        if tc_mar is None:
            tc_mar = OxmlElement("w:tcMar")
            tc_pr.append(tc_mar)
        for name, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
            node = tc_mar.find(qn(f"w:{name}"))
            if node is None:
                node = OxmlElement(f"w:{name}")
                tc_mar.append(node)
            node.set(qn("w:w"), str(value))
            node.set(qn("w:type"), "dxa")

    def shade(cell: Any, fill: str) -> None:
        tc_pr = cell._tc.get_or_add_tcPr()
        shd = tc_pr.find(qn("w:shd"))
        if shd is None:
            shd = OxmlElement("w:shd")
            tc_pr.append(shd)
        shd.set(qn("w:fill"), fill)

    def set_cell_width(cell: Any, twips: int) -> None:
        tc_pr = cell._tc.get_or_add_tcPr()
        tc_w = tc_pr.find(qn("w:tcW"))
        if tc_w is None:
            tc_w = OxmlElement("w:tcW")
            tc_pr.append(tc_w)
        tc_w.set(qn("w:w"), str(twips))
        tc_w.set(qn("w:type"), "dxa")

    def fixed_table(table: Any, col_widths_cm: list[float]) -> None:
        table.autofit = False
        tbl_pr = table._tbl.tblPr
        tbl_w = tbl_pr.find(qn("w:tblW"))
        if tbl_w is None:
            tbl_w = OxmlElement("w:tblW")
            tbl_pr.append(tbl_w)
        tbl_w.set(qn("w:w"), str(sum(cm_twips(w) for w in col_widths_cm)))
        tbl_w.set(qn("w:type"), "dxa")
        tbl_layout = tbl_pr.find(qn("w:tblLayout"))
        if tbl_layout is None:
            tbl_layout = OxmlElement("w:tblLayout")
            tbl_pr.append(tbl_layout)
        tbl_layout.set(qn("w:type"), "fixed")
        grid = table._tbl.tblGrid
        if grid is None:
            grid = OxmlElement("w:tblGrid")
            table._tbl.insert(1, grid)
        for child in list(grid):
            grid.remove(child)
        for width_cm in col_widths_cm:
            col = OxmlElement("w:gridCol")
            col.set(qn("w:w"), str(cm_twips(width_cm)))
            grid.append(col)
        for row in table.rows:
            for idx, cell in enumerate(row.cells):
                if idx < len(col_widths_cm):
                    set_cell_width(cell, cm_twips(col_widths_cm[idx]))

    def clear(cell: Any) -> None:
        for p in cell.paragraphs:
            p.clear()
            para(p)

    def prep_cell(cell: Any, fill: str | None = None, valign: Any = WD_CELL_VERTICAL_ALIGNMENT.CENTER) -> None:
        if fill:
            shade(cell, fill)
        cell.vertical_alignment = valign
        clear(cell)

    def set_row_height(row: Any, height_cm: float) -> None:
        row.height = Cm(height_cm)
        row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST

    def merge_row(table: Any, row_idx: int, start_col: int, end_col: int) -> Any:
        return table.cell(row_idx, start_col).merge(table.cell(row_idx, end_col))

    def answer_style(answer: Any) -> tuple[float, float]:
        text = str(answer)
        if len(text) > 95 or text.count("\n") >= 3:
            return 7.2, 9.0
        if len(text) > 65:
            return 7.6, 9.6
        return 8.0, 10.2

    def add_card(cell: Any, card: dict[str, str]) -> None:
        prep_cell(cell)
        margins(cell, 58, 72, 50, 72)
        p = cell.paragraphs[0]
        para(p, line=10.8, after=1)
        add(p, card.get("tag", "复习卡"), size=8.6, bold=True)
        p = cell.add_paragraph()
        para(p, line=11.6, after=2)
        add(p, "问：", size=8.8, bold=True)
        add(p, card.get("q", ""), size=8.8, bold=True)
        a_size, a_line = answer_style(card.get("a", ""))
        p = cell.add_paragraph()
        para(p, line=a_line, after=2)
        add(p, "答：", size=7.9, bold=True)
        add(p, card.get("a", ""), size=a_size)
        p = cell.add_paragraph()
        para(p, line=8.8, after=0)
        add(p, "复背：__________________", size=6.9)
        p = cell.add_paragraph()
        para(p, line=8.6, after=0)
        add(p, "1st：√会 I can  △卡住 Almost  ×不会 I can't", size=6.5)
        p = cell.add_paragraph()
        para(p, line=8.6, after=0)
        add(p, "2nd：√会 I can  △卡住 Almost  ×不会 I can't", size=6.5)

    def add_cover(doc: Any) -> None:
        p = doc.add_paragraph()
        para(p, line=22, after=4, align=WD_ALIGN_PARAGRAPH.CENTER)
        add(p, card_plan["title"], size=19, bold=True)
        p = doc.add_paragraph()
        para(p, line=15, after=10, align=WD_ALIGN_PARAGRAPH.CENTER)
        add(p, "初一期末4科基础抢分｜A4可打印知识卡", size=11, bold=True)
        t = doc.add_table(rows=7, cols=1)
        t.style = "Table Grid"
        fixed_table(t, [18.0])
        blocks = [
            ("这份卡怎么用", "每天只做1页8格：语文1格、历史2格、生物1格、地理2格、道法2格。英语在手机听读，不占知识卡格。"),
            ("为什么要重复", "同一考点会用概念、认读、易错、自测等不同问法出现。先练熟，再慢慢加难度。"),
            ("线索怎么用", "看到“线索”先想画面、地点或动作，再说关键词。线索只是帮想起，答案仍看关键词。"),
            ("家长怎么抽问", "只问每格的“问”。孩子能说出关键词就算过，不要求背长篇；不会的卡画★。"),
            ("错卡怎么回炉", "当天★卡写在页脚；第二天开始前先问昨天★卡，再做当天新卡。"),
            ("降难度规则", "如果明显抗拒，只保留每科1格；先保住愿意开始，再谈完成量。"),
            ("版本说明", "按初一七年级下册期末基础抢分生成；若教材版本或考试范围不同，请把范围发给老师后重生成。"),
        ]
        for idx, (heading, body) in enumerate(blocks):
            cell = t.cell(idx, 0)
            prep_cell(cell, "FAFAFA")
            margins(cell, 112, 145, 104, 145)
            p = cell.paragraphs[0]
            para(p, line=15, after=2)
            add(p, heading, size=11.2, bold=True)
            p = cell.add_paragraph()
            para(p, line=14, after=0)
            add(p, body, size=9.6)

    def add_day_page(doc: Any, day: dict[str, Any], day_index: int) -> None:
        table = doc.add_table(rows=7, cols=4)
        table.style = "Table Grid"
        fixed_table(table, [4.5, 4.5, 4.5, 4.5])
        set_row_height(table.rows[0], 1.05)
        set_row_height(table.rows[1], 0.52)
        for row_idx in range(2, 6):
            set_row_height(table.rows[row_idx], 5.0)
        set_row_height(table.rows[6], 0.62)
        header_left = merge_row(table, 0, 0, 2)
        header_right = table.cell(0, 3)
        routine = merge_row(table, 1, 0, 3)
        card_cells = []
        for row_idx in range(2, 6):
            card_cells.append(merge_row(table, row_idx, 0, 1))
            card_cells.append(merge_row(table, row_idx, 2, 3))
        for cell in (header_left, header_right):
            prep_cell(cell, "F1F5F9")
            margins(cell, 44, 70, 38, 70)
        p = header_left.paragraphs[0]
        para(p, line=13, after=0)
        add(p, day.get("title", f"Day {day_index + 1:02d}"), size=10.8, bold=True)
        p = header_left.add_paragraph()
        para(p, line=8.6, after=0)
        add(p, day.get("module", ""), size=7.2)
        p = header_right.paragraphs[0]
        para(p, line=9.4, after=0)
        add(p, "姓名 Name：______\n日期 Date：______\n用时 Time：____分钟", size=7.0)
        prep_cell(routine, "FAFAFA")
        margins(routine, 22, 68, 18, 68)
        p = routine.paragraphs[0]
        para(p, line=8.6, after=0)
        add(p, "操作：看3分钟 -> 想线索 -> 盖住答 -> 错卡画★ -> 5分钟后再答 -> 页脚记录错卡号。", size=7.4, bold=True)
        cards = day.get("cards", [])[:8]
        for idx, cell in enumerate(card_cells):
            if idx < len(cards):
                add_card(cell, cards[idx])
            else:
                prep_cell(cell)
        footer_texts = [
            "会了 I can：____ / 8",
            "★错卡号：________",
            "明天先问：________",
            "完成状态：□顺 □卡",
        ]
        for idx, cell in enumerate(table.rows[6].cells):
            prep_cell(cell)
            margins(cell, 26, 34, 22, 34)
            p = cell.paragraphs[0]
            para(p, line=8.5, after=0, align=WD_ALIGN_PARAGRAPH.CENTER)
            add(p, footer_texts[idx], size=6.9, bold=True)

    doc = Document()
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.PORTRAIT
    sec.page_width = Cm(21)
    sec.page_height = Cm(29.7)
    sec.top_margin = Cm(1.2)
    sec.bottom_margin = Cm(1.2)
    sec.left_margin = Cm(1.5)
    sec.right_margin = Cm(1.5)
    doc.styles["Normal"].font.name = "Microsoft YaHei"
    doc.styles["Normal"].font.size = Pt(9)
    add_cover(doc)
    for idx, day in enumerate(card_plan.get("days", [])):
        doc.add_page_break()
        add_day_page(doc, day, idx)

    doc.save(path)
    return upload_file_url(str(student["student_code"]), file_name)


DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def _docx_visible_run_sizes(root: ET.Element) -> list[float]:
    sizes: list[float] = []
    for run in root.findall(".//w:r", DOCX_NS):
        text = "".join(node.text or "" for node in run.findall("./w:t", DOCX_NS))
        if not text.strip():
            continue
        rpr = run.find("./w:rPr", DOCX_NS)
        if rpr is not None and any(rpr.find(f"./w:{tag}", DOCX_NS) is not None for tag in ("vanish", "webHidden", "specVanish")):
            continue
        if rpr is None:
            continue
        size = rpr.find("./w:sz", DOCX_NS)
        if size is None:
            continue
        raw = size.attrib.get(f"{{{DOCX_NS['w']}}}val")
        if not raw:
            continue
        try:
            sizes.append(int(raw) / 2)
        except ValueError:
            continue
    return sizes


def validate_docx_delivery(file_url: str) -> dict[str, Any]:
    rel = unquote(file_url.replace(f"{BASE_PREFIX}/uploads/", "", 1))
    path = UPLOAD_DIR / rel
    if not path.exists():
        raise RuntimeError("DOCX文件未生成")
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise RuntimeError("DOCX文件结构不完整")
        try:
            root = ET.fromstring(archive.read("word/document.xml"))
        except ET.ParseError as exc:
            raise RuntimeError("DOCX内容结构无法解析") from exc

    sizes = _docx_visible_run_sizes(root)
    min_font = min(sizes) if sizes else None
    tables = len(root.findall(".//w:tbl", DOCX_NS))
    if tables < 2:
        raise RuntimeError("DOCX缺少封面或每日卡片表格")
    if min_font is not None and min_font < 10.5:
        raise RuntimeError(f"DOCX字号低于五号字要求：{min_font}")
    return {
        "path": str(path),
        "size": path.stat().st_size,
        "tables": tables,
        "min_font": min_font,
        "fonts": sorted(set(round(size, 1) for size in sizes)),
    }


def generate_cards_docx_for_student(
    student_id: int,
    parent_id: int | None = None,
    adjustment_note: str = "",
    card_plan_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    student = get_student_for_access(student_id, parent_id)
    intake = get_row("SELECT * FROM intake_submissions WHERE student_id=? ORDER BY id DESC LIMIT 1", (student_id,))
    ensure_card_generation_ready(student_id, parent_id, adjustment_note)
    adjustment_note = compact_text(adjustment_note)
    profile = expected_card_profile(student, intake, adjustment_note)
    quality_source = "model"
    quality_issues = validate_card_plan(card_plan_override, profile) if card_plan_override else ["模型未提供结构化卡片"]
    if card_plan_override and not quality_issues:
        card_plan = card_plan_override
    else:
        quality_source = "builtin_template"
        card_plan = build_weekly_card_plan(student, intake, adjustment_note)
        fallback_issues = validate_card_plan(card_plan, profile)
        if fallback_issues:
            raise RuntimeError("内置卡片模板未通过质量校验：" + "；".join(fallback_issues[:5]))
    card_plan = prepare_weekly_card_audio(student, card_plan)
    file_url = write_cards_docx(student, card_plan)
    docx_check = validate_docx_delivery(file_url)
    now = now_text()
    plan = get_row("SELECT * FROM plan_versions WHERE id=?", (student.get("current_plan_version_id"),))
    cards_per_day = int(profile.get("cards_per_day") or len(card_plan.get("days", [{}])[0].get("cards", [])) or 0)
    if profile.get("strict_distribution"):
        distribution = profile.get("distribution", {})
        parts = [f"{subject}{count}格" for subject, count in distribution.items() if count]
        card_load_text = f"每天{cards_per_day}格：" + "、".join(parts) if parts else f"每天{cards_per_day}格路径"
        execution_mode_text = "A4纸面卡 + 家长抽问"
    elif cards_per_day >= 8:
        card_load_text = "每天8格路径"
        execution_mode_text = "A4纸面卡 + 家长抽问"
    elif cards_per_day >= 6:
        card_load_text = "每天6张核心卡 + 复习区"
        execution_mode_text = "A4纸面卡 + 每日反馈"
    elif cards_per_day >= 5:
        card_load_text = "每天4-5张主卡"
        execution_mode_text = "A4纸面卡 + 家长10分钟抽查"
    else:
        card_load_text = "3张核心卡 + 1个复习/信心区"
        execution_mode_text = "微信3卡或纸面3卡"
    if not plan:
        created = create_plan_version(
            student_id,
            {
                "version_code": "V1_试运行",
                "plan_type": "trial",
                "reason": "生成7天知识卡片DOCX。",
                "plan_summary": "已生成7天基础知识卡片。",
                "parent_instruction": "每天只做3张核心卡，卡住标★，第二天回炉。",
                "card_load": card_load_text,
                "execution_mode": execution_mode_text,
                "file_url": file_url,
                "status": "published",
                "generate_files": False,
            },
            None,
        )
        plan = created["plan"]
    with connect() as conn:
        summary = f"已生成7天基础知识卡片DOCX。{card_load_text}，先做题，再说采分词，不会的★卡第二天回炉。"
        if adjustment_note:
            summary += f" 已参考家长调整建议：{adjustment_note[:120]}"
        conn.execute(
            """
            UPDATE plan_versions
            SET status='published', file_url=?, card_load=?, execution_mode=?,
                plan_summary=?, parent_instruction=?, published_at=?, updated_at=?
            WHERE id=?
            """,
            (
                file_url,
                card_load_text,
                execution_mode_text,
                summary,
                "请按DOCX每天一页执行；不会的卡标★，第二天优先回炉。完成后提交每日反馈。",
                now,
                now,
                plan["id"],
            ),
        )
        conn.execute(
            "UPDATE students SET current_plan_version_id=?, current_card_load=?, execution_mode=?, updated_at=? WHERE id=?",
            (plan["id"], card_load_text, execution_mode_text, now, student_id),
        )
        conn.commit()
    evidence_note = " ".join(
        part
        for part in (
            adjustment_note,
            compact_text(student.get("goal")),
            compact_text(student.get("main_difficulties")),
            card_load_text,
            summary,
        )
        if part
    )
    evidence_refs = record_plan_evidence_refs(
        student_id,
        int(plan["id"]),
        collect_learning_evidence_context(student, evidence_note),
        evidence_note,
    )
    plan_row = dict(get_row("SELECT * FROM plan_versions WHERE id=?", (plan["id"],)) or {})
    plan_row["evidence_ref_count"] = len(evidence_refs)
    return {
        "student_id": student_id,
        "student_code": student["student_code"],
        "file_url": file_url,
        "card_plan": card_plan,
        "quality": {
            "source": quality_source,
            "model_issues": quality_issues,
            "docx": docx_check,
        },
        "plan": plan_row,
        "evidence_refs": evidence_refs,
    }


def create_plan_version(student_id: int, payload: dict[str, Any], parent_id: int | None = None) -> dict[str, Any]:
    student = get_student_for_access(student_id, parent_id)
    ensure_can_create_plan_for_parent(student_id, parent_id)
    plan_type = compact_text(payload.get("plan_type")) or "adjustment"
    version_code = compact_text(payload.get("version_code")) or next_version_code(student_id, plan_type)
    level_name = compact_text(payload.get("level_name")) or student.get("current_level") or "微型档"
    card_load = compact_text(payload.get("card_load")) or student.get("current_card_load") or "3张核心卡 + 1张信心卡"
    execution_mode = compact_text(payload.get("execution_mode")) or student.get("execution_mode") or "微信3卡"
    reason = compact_text(payload.get("reason")) or "根据最新反馈生成下一版方案。"
    plan_summary = compact_text(payload.get("plan_summary")) or (
        f"{version_code}：保持{level_name}，执行{card_load}；先处理★卡和低完成率，再恢复新卡。"
    )
    parent_instruction = compact_text(payload.get("parent_instruction")) or (
        "明天先问★卡，再做今日卡；孩子答出关键词就算过。若疲劳或抗拒，改为微信3卡保温。"
    )
    file_url = compact_text(payload.get("file_url"))
    status = compact_text(payload.get("status")) or "draft"
    now = now_text()

    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO plan_versions (
              student_id, version_code, plan_type, level_name, card_load,
              execution_mode, reason, plan_summary, parent_instruction,
              status, file_url, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(student_id, version_code) DO UPDATE SET
              plan_type=excluded.plan_type,
              level_name=excluded.level_name,
              card_load=excluded.card_load,
              execution_mode=excluded.execution_mode,
              reason=excluded.reason,
              plan_summary=excluded.plan_summary,
              parent_instruction=excluded.parent_instruction,
              status=excluded.status,
              file_url=excluded.file_url,
              updated_at=excluded.updated_at
            """,
            (
                student_id, version_code, plan_type, level_name, card_load,
                execution_mode, reason, plan_summary, parent_instruction,
                status, file_url, now, now,
            ),
        )
        if cur.lastrowid:
            plan_id = int(cur.lastrowid)
        else:
            row = conn.execute(
                "SELECT id FROM plan_versions WHERE student_id=? AND version_code=?",
                (student_id, version_code),
            ).fetchone()
            plan_id = int(row["id"])
        conn.commit()

    plan = get_row("SELECT * FROM plan_versions WHERE id=?", (plan_id,))
    if payload.get("generate_files", True) and not compact_text(plan.get("file_url")):  # type: ignore[union-attr]
        generated_url = write_plan_file(student, plan)  # type: ignore[arg-type]
        with connect() as conn:
            conn.execute("UPDATE plan_versions SET file_url=?, updated_at=? WHERE id=?", (generated_url, now_text(), plan_id))
            conn.commit()
        plan = get_row("SELECT * FROM plan_versions WHERE id=?", (plan_id,))

    evidence_note = compact_text(payload.get("evidence_note")) or " ".join(
        part for part in (reason, plan_summary, parent_instruction) if compact_text(part)
    )
    evidence_refs = record_plan_evidence_refs(
        student_id,
        plan_id,
        collect_learning_evidence_context(student, evidence_note),
        evidence_note,
    )
    plan = dict(plan or {})
    plan["evidence_ref_count"] = len(evidence_refs)

    return {"plan": plan, "student": student, "evidence_refs": evidence_refs}


def publish_plan(plan_version_id: int, payload: dict[str, Any], parent_id: int | None = None) -> dict[str, Any]:
    plan = get_row("SELECT * FROM plan_versions WHERE id=?", (plan_version_id,))
    if not plan:
        raise ValueError("方案版本不存在")
    student = get_student_for_access(int(plan["student_id"]), parent_id)
    file_url = compact_text(payload.get("file_url")) or compact_text(plan.get("file_url"))
    if payload.get("generate_files", True) and not file_url:
        file_url = write_plan_file(student, plan)
    published_at = now_text()
    with connect() as conn:
        conn.execute(
            """
            UPDATE plan_versions
            SET status='published', file_url=?, published_at=?, updated_at=?
            WHERE id=?
            """,
            (file_url, published_at, published_at, plan_version_id),
        )
        conn.execute(
            """
            UPDATE students
            SET current_plan_version_id=?, current_level=?, current_card_load=?,
                execution_mode=?, updated_at=?
            WHERE id=?
            """,
            (
                plan_version_id, plan.get("level_name"), plan.get("card_load"),
                plan.get("execution_mode"), published_at, student["id"],
            ),
        )
        conn.commit()
    return {
        "status": "published",
        "published_at": published_at,
        "plan": get_row("SELECT * FROM plan_versions WHERE id=?", (plan_version_id,)),
        "notify_parent": bool(payload.get("notify_parent")),
        "message": compact_text(payload.get("message")),
    }


def generate_weekly_report(student_id: int, payload: dict[str, Any], parent_id: int | None = None) -> dict[str, Any]:
    student = get_student_for_access(student_id, parent_id)
    ensure_can_create_plan_for_parent(student_id, parent_id)
    week_start = compact_text(payload.get("week_start")) or today_text()
    week_end = compact_text(payload.get("week_end")) or today_text()
    date.fromisoformat(week_start)
    date.fromisoformat(week_end)
    feedback = list_rows(
        """
        SELECT * FROM daily_feedback
        WHERE student_id=? AND feedback_date BETWEEN ? AND ?
        ORDER BY feedback_date
        """,
        (student_id, week_start, week_end),
    )
    actions = list_rows(
        """
        SELECT * FROM adjustment_actions
        WHERE student_id=?
        ORDER BY id DESC
        LIMIT 20
        """,
        (student_id,),
    )
    days = len(feedback)
    avg_completion = round(sum(float(row.get("completion_rate") or 0) for row in feedback) / days, 2) if days else 0.0
    avg_known = round(sum(float(row.get("known_rate") or 0) for row in feedback) / days, 2) if days else 0.0
    star_cards = "、".join(compact_text(row.get("star_card_codes")) for row in feedback if compact_text(row.get("star_card_codes"))) or "暂无"
    high_actions = [row for row in actions if row.get("priority") == "high"]
    next_mode = "微信3卡保温" if high_actions or avg_completion < 0.5 or avg_known < 0.3 else "保持当前节奏"
    report_text = (
        f"{student.get('student_code')}｜{week_start} 至 {week_end} 周反馈："
        f"本周收到{days}天反馈，平均完成率{avg_completion:.0%}，平均答对率{avg_known:.0%}。"
        f"重点★卡：{star_cards}。下周建议：{next_mode}，先回炉最卡内容，再少量恢复新卡。"
    )
    plan_result = create_plan_version(
        student_id,
        {
            "plan_type": "weekly",
            "reason": "周反馈后生成下周调整方案。",
            "level_name": "微型档" if next_mode == "微信3卡保温" else student.get("current_level"),
            "card_load": "3张核心卡 + ★卡回炉" if next_mode == "微信3卡保温" else student.get("current_card_load"),
            "execution_mode": next_mode,
            "plan_summary": report_text,
            "parent_instruction": "下周先固定每天反馈完成、会了、★卡和情绪；老师根据连续2天表现再决定是否加量。",
            "generate_files": True,
        },
        parent_id,
    )
    plan_id = int(plan_result["plan"]["id"])
    created = now_text()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO weekly_reports (student_id, week_start, week_end, report_text, plan_version_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (student_id, week_start, week_end, report_text, plan_id, created),
        )
        conn.commit()
    return {
        "weekly_report_id": int(cur.lastrowid),
        "student_id": student_id,
        "week_start": week_start,
        "week_end": week_end,
        "report_text": report_text,
        "plan": plan_result["plan"],
    }


def normalize_route(raw_path: str) -> str:
    path = raw_path.rstrip("/") or "/"
    if path == BASE_PREFIX:
        return "/"
    if path.startswith(BASE_PREFIX + "/"):
        return path[len(BASE_PREFIX):] or "/"
    return path


def has_admin_access(headers: Any, query: dict[str, list[str]]) -> bool:
    supplied = headers.get("X-Admin-Key") or query.get("admin_key", [""])[0]
    return bool(ADMIN_KEY) and supplied == ADMIN_KEY


def clean_filename(name: str) -> str:
    name = compact_text(name) or "evidence"
    name = name.replace("\\", "_").replace("/", "_")
    name = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", name)
    return name[:80] or "evidence"


def upload_file_url(student_code: str, file_name: str) -> str:
    return f"{BASE_PREFIX}/uploads/{quote(clean_filename(student_code))}/{quote(clean_filename(file_name))}"


def decode_uploaded_docx(payload: dict[str, Any]) -> tuple[bytes, str]:
    data_url = compact_text(payload.get("data_url"))
    encoded = compact_text(payload.get("content_base64"))
    if data_url:
        if "," not in data_url or ";base64" not in data_url.split(",", 1)[0]:
            raise ValueError("文件内容必须是base64格式")
        encoded = data_url.split(",", 1)[1]
    if not encoded:
        raise ValueError("缺少DOCX文件内容")

    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("DOCX文件内容不是有效base64") from exc
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("单个DOCX文件不能超过8MB")

    original_name = clean_filename(compact_text(payload.get("file_name")) or "current_plan.docx")
    if Path(original_name).suffix.lower() != ".docx":
        raise ValueError("只能上传DOCX文件")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise ValueError("DOCX文件结构不完整")
    except zipfile.BadZipFile as exc:
        raise ValueError("DOCX文件不是有效Word文档") from exc
    return raw, original_name


def bind_current_plan_docx(student_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    student = get_student_for_access(student_id, None)
    plan_id = payload.get("plan_version_id") or student.get("current_plan_version_id")
    if not plan_id:
        raise ValueError("当前学生还没有可绑定的方案，请先生成方案")
    plan = get_row("SELECT * FROM plan_versions WHERE id=?", (plan_id,))
    if not plan or int(plan["student_id"]) != student_id:
        raise ValueError("当前方案不存在或不属于该学生")

    raw, original_name = decode_uploaded_docx(payload)
    stored_name = f"{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}_{len(raw)}_{original_name}"
    student_dir = UPLOAD_DIR / str(student["student_code"])
    student_dir.mkdir(parents=True, exist_ok=True)
    path = student_dir / stored_name
    path.write_bytes(raw)

    file_url = upload_file_url(str(student["student_code"]), stored_name)
    docx_check: dict[str, Any] = {"path": str(path), "size": path.stat().st_size, "package": "valid"}
    try:
        docx_check.update(validate_docx_delivery(file_url))
    except ModuleNotFoundError:
        docx_check["quality_warning"] = "服务器未安装python-docx，仅完成DOCX包结构校验"
    except RuntimeError as exc:
        if truthy(payload.get("strict_quality_check")):
            try:
                path.unlink()
            except OSError:
                pass
            raise
        docx_check["quality_warning"] = str(exc)
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise

    updated = now_text()
    with connect() as conn:
        conn.execute(
            "UPDATE plan_versions SET file_url=?, updated_at=? WHERE id=?",
            (file_url, updated, plan_id),
        )
        conn.execute(
            "UPDATE students SET current_plan_version_id=?, updated_at=? WHERE id=?",
            (plan_id, updated, student_id),
        )
        conn.commit()

    return {
        "status": "bound",
        "student_id": student_id,
        "student_code": student["student_code"],
        "plan_version_id": int(plan_id),
        "version_code": plan.get("version_code"),
        "file_url": file_url,
        "original_name": original_name,
        "size": len(raw),
        "docx_check": docx_check,
        "plan": get_row("SELECT * FROM plan_versions WHERE id=?", (plan_id,)),
    }


def cleanup_tts_cache(force: bool = False) -> dict[str, Any]:
    now_ts = time.time()
    with TTS_CLEANUP_LOCK:
        if not force and now_ts - float(TTS_CLEANUP_STATE["last_at"]) < TTS_CLEANUP_INTERVAL_SECONDS:
            return {"status": "skipped", "reason": "interval"}
        TTS_CLEANUP_STATE["last_at"] = now_ts

    cutoff = now_ts - TTS_CACHE_RETENTION_DAYS * 86400
    removed = 0
    removed_bytes = 0
    errors: list[str] = []
    suffixes = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}

    if not UPLOAD_DIR.exists():
        return {"status": "ok", "removed": 0, "bytes": 0, "retention_days": TTS_CACHE_RETENTION_DAYS}

    for path in UPLOAD_DIR.glob("*/tts_*"):
        try:
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            stat = path.stat()
            if stat.st_mtime >= cutoff:
                continue
            removed_bytes += stat.st_size
            path.unlink()
            removed += 1
        except OSError as exc:
            errors.append(f"{path.name}: {str(exc)[:80]}")

    if removed or errors:
        line = (
            f"{now_text()} TTS cache cleanup removed={removed} bytes={removed_bytes} "
            f"retention_days={TTS_CACHE_RETENTION_DAYS} errors={len(errors)}\n"
        )
        try:
            (ROOT / "server.log").open("a", encoding="utf-8").write(line)
        except OSError:
            pass
    return {
        "status": "ok",
        "removed": removed,
        "bytes": removed_bytes,
        "retention_days": TTS_CACHE_RETENTION_DAYS,
        "errors": errors[:3],
    }


def save_evidence_base64(student_id: int, payload: dict[str, Any], parent_id: int | None = None) -> dict[str, Any]:
    student = get_student_for_access(student_id, parent_id)

    data_url = compact_text(payload.get("data_url"))
    if not data_url or "," not in data_url:
        raise ValueError("缺少文件内容")

    header, encoded = data_url.split(",", 1)
    if ";base64" not in header:
        raise ValueError("文件内容必须是base64")
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("单个文件不能超过8MB")

    original_name = clean_filename(compact_text(payload.get("file_name")) or "evidence.bin")
    suffix = Path(original_name).suffix.lower()
    if suffix not in [".jpg", ".jpeg", ".png", ".webp", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt"]:
        suffix = mimetypes.guess_extension(header[5:].split(";")[0]) or ".bin"
    stored_name = f"{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}_{len(raw)}_{original_name}"
    if not Path(stored_name).suffix:
        stored_name += suffix

    student_dir = UPLOAD_DIR / str(student["student_code"])
    student_dir.mkdir(parents=True, exist_ok=True)
    path = student_dir / stored_name
    path.write_bytes(raw)

    file_url = upload_file_url(str(student["student_code"]), stored_name)
    created = now_text()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO evidence_files (
              student_id, feedback_id, file_type, file_url, storage_path,
              original_name, description, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                student_id,
                payload.get("feedback_id"),
                compact_text(payload.get("file_type")) or "other",
                file_url,
                str(path),
                original_name,
                compact_text(payload.get("description")),
                created,
            ),
        )
        conn.commit()

    return {
        "evidence_id": int(cur.lastrowid),
        "student_id": student_id,
        "file_url": file_url,
        "original_name": original_name,
        "size": len(raw),
        "status": "saved",
    }


def normalize_spoken_english(value: Any) -> str:
    text = compact_text(value).lower()
    text = re.sub(r"[^a-z0-9'\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def pronunciation_text_score(target: str, transcript: str) -> int:
    target_norm = normalize_spoken_english(target)
    transcript_norm = normalize_spoken_english(transcript)
    if not target_norm or not transcript_norm:
        return 0
    ratio = SequenceMatcher(None, target_norm, transcript_norm).ratio()
    target_words = set(target_norm.split())
    spoken_words = set(transcript_norm.split())
    word_hit = len(target_words & spoken_words) / max(len(target_words), 1)
    return int(round(max(ratio * 100, word_hit * 92)))


def voice_feedback(target: str, transcript: str, score: int) -> tuple[str, str]:
    target_words = normalize_spoken_english(target).split()
    spoken = set(normalize_spoken_english(transcript).split())
    missing = []
    for word in target_words:
        if word not in spoken and word not in missing:
            missing.append(word)
    missing_hint = f" 少了：{'、'.join(missing[:3])}。" if missing else ""
    slow_hint = "纠正方法：先听一遍，分成小块慢读，再连成一句。"
    if len(target_words) == 1:
        slow_hint = "纠正方法：先听标准音，拖长元音，再把最后一个音收住。"
    elif len(target_words) <= 3:
        slow_hint = "纠正方法：一个词一个词读清楚，再连起来读。"
    if score >= 86:
        return "passed", "很好，关键词和句子基本说出来了。明天可以换一个场景再说。"
    if score >= 68:
        return "almost", f"差一点就过了。{missing_hint}{slow_hint}"
    if not compact_text(transcript):
        return "retry", "没有识别到声音。请靠近手机，再按住录音读一遍。"
    return "retry", f"这句先标为语音★卡，明天开始前再读一次。{missing_hint}{slow_hint}"


def default_voice_targets(student: dict[str, Any], day_index: int = 0) -> list[dict[str, str]]:
    text = " ".join(
        compact_text(item)
        for item in [student.get("grade_region"), student.get("goal"), student.get("main_difficulties")]
    )
    if "初一" in text or "七" in text:
        banks = [
            [
                {"target_type": "word", "target_text": "bike", "target_meaning": "自行车", "scenario": "上学路上"},
                {"target_type": "phrase", "target_text": "ride a bike", "target_meaning": "骑自行车", "scenario": "上学路上"},
                {"target_type": "sentence", "target_text": "I go to school by bike.", "target_meaning": "我骑自行车去上学。", "scenario": "上学路上"},
            ],
            [
                {"target_type": "phrase", "target_text": "get up", "target_meaning": "起床", "scenario": "早晨作息"},
                {"target_type": "sentence", "target_text": "What time do you get up?", "target_meaning": "你几点起床？", "scenario": "早晨作息"},
                {"target_type": "sentence", "target_text": "I get up at six thirty.", "target_meaning": "我六点半起床。", "scenario": "早晨作息"},
            ],
            [
                {"target_type": "word", "target_text": "swim", "target_meaning": "游泳", "scenario": "能力问答"},
                {"target_type": "sentence", "target_text": "Can you swim?", "target_meaning": "你会游泳吗？", "scenario": "能力问答"},
                {"target_type": "sentence", "target_text": "Yes, I can.", "target_meaning": "是的，我会。", "scenario": "能力问答"},
            ],
            [
                {"target_type": "phrase", "target_text": "near here", "target_meaning": "在附近", "scenario": "问路"},
                {"target_type": "sentence", "target_text": "Is there a bank near here?", "target_meaning": "附近有银行吗？", "scenario": "问路"},
                {"target_type": "sentence", "target_text": "Go along this street.", "target_meaning": "沿着这条街走。", "scenario": "问路"},
            ],
            [
                {"target_type": "phrase", "target_text": "doing homework", "target_meaning": "正在做作业", "scenario": "正在进行"},
                {"target_type": "sentence", "target_text": "What are you doing?", "target_meaning": "你正在做什么？", "scenario": "正在进行"},
                {"target_type": "sentence", "target_text": "I am doing my homework.", "target_meaning": "我正在做作业。", "scenario": "正在进行"},
            ],
            [
                {"target_type": "word", "target_text": "visited", "target_meaning": "参观了", "scenario": "过去经历"},
                {"target_type": "sentence", "target_text": "Did you visit your grandparents?", "target_meaning": "你看望祖父母了吗？", "scenario": "过去经历"},
                {"target_type": "sentence", "target_text": "Yes, I did.", "target_meaning": "是的，我去了。", "scenario": "过去经历"},
            ],
            [
                {"target_type": "phrase", "target_text": "a bowl of noodles", "target_meaning": "一碗面", "scenario": "点餐"},
                {"target_type": "sentence", "target_text": "What would you like?", "target_meaning": "你想要什么？", "scenario": "点餐"},
                {"target_type": "sentence", "target_text": "I would like beef noodles.", "target_meaning": "我想要牛肉面。", "scenario": "点餐"},
            ],
        ]
        today_bank = banks[max(day_index, 0) % len(banks)]
        return today_bank + [
            {"target_type": "sentence", "target_text": "I can try again.", "target_meaning": "我可以再试一次。", "scenario": "鼓励句"},
            {"target_type": "sentence", "target_text": "I know this word.", "target_meaning": "我认识这个词。", "scenario": "自信句"},
        ]
    return [
        {"target_type": "word", "target_text": "today", "target_meaning": "今天", "scenario": "每日开口"},
        {"target_type": "word", "target_text": "school", "target_meaning": "学校", "scenario": "每日开口"},
        {"target_type": "phrase", "target_text": "go to school", "target_meaning": "去上学", "scenario": "每日开口"},
        {"target_type": "sentence", "target_text": "I can try again.", "target_meaning": "我可以再试一次。", "scenario": "鼓励句"},
        {"target_type": "sentence", "target_text": "I know this word.", "target_meaning": "我认识这个词。", "scenario": "自信句"},
    ]


def voice_targets_from_cards(cards: list[dict[str, Any]]) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    for card in cards:
        tag = compact_text(card.get("tag"))
        q = compact_text(card.get("q"))
        a = compact_text(card.get("a"))
        audio_text = compact_text(card.get("audio_text"))
        if audio_text:
            target = re.sub(r"\s+", " ", audio_text).strip(" ;,，。")
            if target:
                words = target.split()
                targets.append(
                    {
                        "target_type": "word" if len(words) == 1 else "sentence" if target.endswith(("?", ".", "!")) else "phrase",
                        "target_text": target,
                        "target_meaning": "来自今日英语知识卡",
                        "scenario": "知识卡跟读",
                    }
                )
                if len(targets) >= 5:
                    return targets
        if "英语" not in tag + q + a:
            continue
        candidates = re.findall(r"[A-Za-z][A-Za-z' ]{2,}[?.!]*", " ".join([q, a]))
        for item in candidates:
            target = re.sub(r"\s+", " ", item).strip(" ;,，。")
            words = target.split()
            if not target or len(target) > 44 or len(words) > 8:
                continue
            if target.lower() in {"yes", "no"}:
                continue
            target_type = "word" if len(words) == 1 else "sentence" if target.endswith(("?", ".", "!")) else "phrase"
            targets.append(
                {
                    "target_type": target_type,
                    "target_text": target,
                    "target_meaning": "来自今日英语知识卡",
                    "scenario": "知识卡跟读",
                }
            )
            if len(targets) >= 5:
                return targets
    return targets[:5]


def normalize_voice_targets(items: Any) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    targets: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        target = re.sub(r"\s+", " ", compact_text(item.get("target_text"))).strip(" ;,，。")
        if not target:
            continue
        words = target.split()
        target_type = compact_text(item.get("target_type"))
        if target_type not in {"word", "phrase", "sentence"}:
            target_type = "word" if len(words) == 1 else "sentence" if target.endswith(("?", ".", "!")) else "phrase"
        targets.append(
            {
                "target_type": target_type,
                "target_text": target[:120],
                "target_meaning": compact_text(item.get("target_meaning"))[:120],
                "scenario": compact_text(item.get("scenario"))[:80],
            }
        )
        if len(targets) >= 5:
            break
    return targets


def extract_voice_targets_from_card_json(student: dict[str, Any], day_index: int = 0) -> list[dict[str, str]]:
    plan = latest_card_plan_for_student(student)
    if not plan:
        return []
    days = plan.get("days") or []
    if not days:
        return []
    idx = min(max(day_index, 0), len(days) - 1)
    day = days[idx] if isinstance(days[idx], dict) else {}
    day_targets = normalize_voice_targets(day.get("english_targets"))
    if day_targets:
        return day_targets
    weekly_targets = plan.get("english_targets")
    if isinstance(weekly_targets, list) and idx < len(weekly_targets):
        if isinstance(weekly_targets[idx], dict):
            plan_targets = normalize_voice_targets(weekly_targets[idx].get("items"))
        else:
            plan_targets = normalize_voice_targets(weekly_targets[idx])
        if plan_targets:
            return plan_targets
    cards = day.get("cards") if isinstance(day.get("cards"), list) else []
    return voice_targets_from_cards(cards[:8])


def merge_weak_voice_items_after_new(student_id: int, items: list[dict[str, str]]) -> list[dict[str, str]]:
    weak = list_rows(
        """
        SELECT target_text, target_meaning, scenario, score
        FROM voice_practice_sessions
        WHERE student_id=? AND score BETWEEN 1 AND 67
          AND TRIM(COALESCE(transcript, '')) <> ''
        ORDER BY id DESC
        LIMIT 5
        """,
        (student_id,),
    )
    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        target = compact_text(item.get("target_text"))
        key = target.lower()
        if not target or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    for row in weak:
        target = compact_text(row.get("target_text"))
        if not target:
            continue
        key = target.lower()
        if key in seen:
            continue
        seen.add(key)
        words = target.split()
        merged.append(
            {
                "target_type": "word" if len(words) == 1 else "sentence" if target.endswith(("?", ".", "!")) else "phrase",
                "target_text": target,
                "target_meaning": compact_text(row.get("target_meaning")) or "上次低分回炉",
                "scenario": f"语音★卡回炉｜{compact_text(row.get('scenario')) or '再练一次'}",
            }
        )
    return merged[:5]


def get_voice_practice_today(student_id: int, parent_id: int | None = None) -> dict[str, Any]:
    student = get_student_for_access(student_id, parent_id)
    knowledge = student_knowledge_today(student)
    day_index = int(knowledge.get("current_day_index") or 0)
    items = extract_voice_targets_from_card_json(student, day_index) or default_voice_targets(student, day_index)
    items = merge_weak_voice_items_after_new(student_id, items)
    today = today_text()
    history = list_rows(
        """
        SELECT target_text, transcript, score, status, feedback_text, created_at
        FROM voice_practice_sessions
        WHERE student_id=? AND practice_date=?
        ORDER BY id DESC
        LIMIT 20
        """,
        (student_id, today),
    )
    weak = list_rows(
        """
        SELECT target_text, target_meaning, scenario, score, feedback_text, created_at
        FROM voice_practice_sessions
        WHERE student_id=? AND score BETWEEN 1 AND 67
          AND TRIM(COALESCE(transcript, '')) <> ''
        ORDER BY id DESC
        LIMIT 5
        """,
        (student_id,),
    )
    return {
        "student_id": student_id,
        "student_code": student.get("student_code"),
        "practice_date": today,
        "mode": "生活化英语跟读",
        "items": items[:5],
        "history": history,
        "weak_items": weak,
        "tts": {
            "server_available": bool(
                os.environ.get("ARK_TTS_API_KEY")
                or (
                    (os.environ.get("XIAOMI_API_KEY") or os.environ.get("XUEJI_TTS_API_KEY"))
                    and (os.environ.get("XIAOMI_BASE_URL") or os.environ.get("XUEJI_TTS_BASE_URL"))
                )
            ),
            "browser_fallback": True,
        },
        "assessment": {
            "mode": "浏览器即时识别 + 讯飞专业评测",
            "provider": "xfyun_ise",
            "quota": xfyun_assessment_usage(student_id, today),
            "note": "按住说话和普通提交只做免费练习；只有点击专业评测才使用讯飞额度。",
        },
    }


def save_voice_audio(student: dict[str, Any], data_url: str, target_text: str) -> str:
    if not data_url or "," not in data_url:
        return ""
    header, encoded = data_url.split(",", 1)
    if ";base64" not in header:
        return ""
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("单个语音文件不能超过8MB")
    ext = ".webm"
    if "wav" in header:
        ext = ".wav"
    elif "mp3" in header or "mpeg" in header:
        ext = ".mp3"
    safe_target = re.sub(r"[^0-9A-Za-z]+", "_", target_text.lower()).strip("_")[:28] or "voice"
    student_dir = UPLOAD_DIR / str(student["student_code"])
    student_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}_{safe_target}{ext}"
    path = student_dir / file_name
    path.write_bytes(raw)
    return upload_file_url(str(student["student_code"]), file_name)


def upload_url_to_path(file_url: str) -> Path | None:
    if not file_url.startswith(f"{BASE_PREFIX}/uploads/"):
        return None
    rel = unquote(file_url.replace(f"{BASE_PREFIX}/uploads/", "", 1))
    target = (UPLOAD_DIR / rel).resolve()
    if not str(target).startswith(str(UPLOAD_DIR.resolve())):
        return None
    return target


def xfyun_ise_auth_url(api_key: str, api_secret: str) -> str:
    host = "ise-api.xfyun.cn"
    path = "/v2/open-ise"
    date_header = email.utils.formatdate(usegmt=True)
    signature_origin = f"host: {host}\ndate: {date_header}\nGET {path} HTTP/1.1"
    digest = hmac.new(api_secret.encode("utf-8"), signature_origin.encode("utf-8"), hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode("utf-8")
    authorization_origin = (
        f'api_key="{api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"'
    )
    query = urlencode(
        {
            "authorization": base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8"),
            "date": date_header,
            "host": host,
        }
    )
    return f"wss://{host}{path}?{query}"


def xfyun_trial_text(target_text: str, target_type: str) -> tuple[str, str]:
    normalized = normalize_spoken_english(target_text)
    word_count = len(normalized.split())
    if (target_type or "").lower() == "word" or word_count <= 1:
        word = normalized or target_text.strip()
        return "read_word", f"\ufeff[word]\n{word}"
    return "read_sentence", f"\ufeff[content]{target_text.strip()}"


def xfyun_daily_limit_for_student(student_id: int) -> int:
    base_limit = XFYUN_ISE_DAILY_LIMIT_PER_STUDENT
    if base_limit <= 0:
        return 0
    try:
        student = get_student_for_access(student_id, None)
        knowledge = student_knowledge_today(student)
        day_index = int(knowledge.get("current_day_index") or 0)
        items = extract_voice_targets_from_card_json(student, day_index) or default_voice_targets(student, day_index)
        target_count = len({compact_text(item.get("target_text")) for item in items if isinstance(item, dict)})
        if target_count:
            return min(max(base_limit, target_count), XFYUN_ISE_DAILY_LIMIT_MAX_PER_STUDENT)
    except Exception:
        pass
    return base_limit


def xfyun_assessment_usage(student_id: int, practice_date: str | None = None) -> dict[str, int]:
    day = practice_date or today_text()
    row = get_row(
        """
        SELECT COUNT(*) AS used
        FROM voice_practice_sessions
        WHERE student_id=? AND practice_date=? AND assessment_provider='xfyun_ise' AND assessment_available=1
        """,
        (student_id, day),
    )
    used = int(row.get("used") or 0) if row else 0
    limit = xfyun_daily_limit_for_student(student_id)
    remaining = max(limit - used, 0) if limit else 0
    return {"used": used, "limit": limit, "remaining": remaining}


def decode_audio_data_url(data_url: str) -> tuple[bytes, str]:
    if not data_url or "," not in data_url:
        raise ValueError("音频文件内容不能为空")
    header, encoded = data_url.split(",", 1)
    if ";base64" not in header:
        raise ValueError("音频文件内容必须是base64格式")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("音频文件内容不是有效base64") from exc
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("单个音频文件不能超过8MB")
    mime = header.split(";", 1)[0].removeprefix("data:")
    ext = ".webm"
    if "wav" in mime:
        ext = ".wav"
    elif "mp3" in mime or "mpeg" in mime:
        ext = ".mp3"
    elif "ogg" in mime:
        ext = ".ogg"
    elif "flac" in mime:
        ext = ".flac"
    elif "mp4" in mime or "m4a" in mime:
        ext = ".m4a"
    return raw, ext


def convert_audio_to_pcm16k(audio_path: Path) -> bytes:
    if not audio_path or not audio_path.exists():
        return b""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pcm") as tmp:
        tmp_path = Path(tmp.name)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(audio_path),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "s16le",
                str(tmp_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=15,
        )
        return tmp_path.read_bytes()
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def parse_xfyun_scores(xml_text: str) -> dict[str, Any]:
    result: dict[str, Any] = {"raw_xml_len": len(xml_text), "phones": [], "words": []}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return result
    score_keys = ["total_score", "accuracy_score", "standard_score", "fluency_score", "integrity_score"]
    for elem in root.iter():
        for key in score_keys:
            if key in elem.attrib and key not in result:
                try:
                    result[key] = round(float(elem.attrib[key]), 1)
                except ValueError:
                    pass
        if elem.tag == "word":
            result["words"].append(
                {
                    "content": elem.attrib.get("content", ""),
                    "score": elem.attrib.get("total_score") or elem.attrib.get("accuracy_score") or "",
                    "message": elem.attrib.get("dp_message") or elem.attrib.get("serr_msg") or "",
                }
            )
        elif elem.tag == "phone":
            result["phones"].append(
                {
                    "content": elem.attrib.get("content", ""),
                    "message": elem.attrib.get("dp_message") or elem.attrib.get("perr_msg") or "",
                }
            )
    return result


def xfyun_feedback(target: str, scores: dict[str, Any]) -> tuple[int, str, str]:
    score = int(round(float(scores.get("total_score") or scores.get("accuracy_score") or 0)))
    bad_phones = [
        item.get("content", "")
        for item in scores.get("phones", [])
        if compact_text(item.get("content")) and compact_text(item.get("message")) not in ("", "0")
    ][:3]
    accuracy = scores.get("accuracy_score")
    fluency = scores.get("fluency_score")
    standard = scores.get("standard_score")
    parts = [f"讯飞评测{score}分"]
    detail = []
    if accuracy not in (None, ""):
        detail.append(f"准确度{accuracy}")
    if fluency not in (None, ""):
        detail.append(f"流畅度{fluency}")
    if standard not in (None, ""):
        detail.append(f"标准度{standard}")
    if detail:
        parts.append("，" + "，".join(detail))
    if bad_phones:
        parts.append(f"。重点重读：{'、'.join(bad_phones)}，先听标准音，再慢速跟读。")
    elif score >= 86:
        parts.append("。发音比较稳，可以换一个生活场景再读一次。")
    else:
        parts.append("。先跟标准音慢读，再把单词连成一句。")
    status = "passed" if score >= 86 else "almost" if score >= 68 else "retry"
    return score, status, "".join(parts)


def assess_voice_with_xfyun(target_text: str, target_type: str, audio_url: str) -> dict[str, Any]:
    audio_path = upload_url_to_path(audio_url)
    return assess_voice_with_xfyun_audio_path(target_text, target_type, audio_path)


def assess_voice_with_xfyun_audio_path(target_text: str, target_type: str, audio_path: Path | str | None) -> dict[str, Any]:
    app_id = os.environ.get("XFYUN_ISE_APPID", "")
    api_key = os.environ.get("XFYUN_ISE_API_KEY", "")
    api_secret = os.environ.get("XFYUN_ISE_API_SECRET", "")
    if not (app_id and api_key and api_secret and audio_path):
        return {"available": False, "message": "讯飞评测未配置或没有录音。"}
    audio_path = Path(audio_path)
    if not audio_path.exists():
        return {"available": False, "message": "录音文件不存在。"}
    try:
        pcm = convert_audio_to_pcm16k(audio_path)
        if not pcm:
            return {"available": False, "message": "录音格式暂不能转换。"}
        import websocket  # type: ignore[import-not-found]

        category, trial_text = xfyun_trial_text(target_text, target_type)
        ws = websocket.create_connection(
            xfyun_ise_auth_url(api_key, api_secret),
            timeout=20,
            sslopt={"cert_reqs": ssl.CERT_NONE},
        )
        try:
            ws.send(
                json.dumps(
                    {
                        "common": {"app_id": app_id},
                        "business": {
                            "sub": "ise",
                            "ent": "en_vip",
                            "category": category,
                            "cmd": "ssb",
                            "text": trial_text,
                            "ttp_skip": True,
                            "aue": "raw",
                            "auf": "audio/L16;rate=16000",
                            "rstcd": "utf8",
                            "rst": "entirety",
                            "ise_unite": "1",
                            "extra_ability": "syll_phone_err_msg;multi_dimension",
                        },
                        "data": {"status": 0},
                    },
                    ensure_ascii=False,
                )
            )
            chunk_size = 1280
            for offset in range(0, len(pcm), chunk_size):
                chunk = pcm[offset : offset + chunk_size]
                is_first = offset == 0
                is_last = offset + chunk_size >= len(pcm)
                ws.send(
                    json.dumps(
                        {
                            "business": {"cmd": "auw", "aus": 4 if is_last else 1 if is_first else 2},
                            "data": {
                                "status": 2 if is_last else 1,
                                "data": base64.b64encode(chunk).decode("ascii"),
                            },
                        }
                    )
                )
                time.sleep(0.02)
            started = time.time()
            while time.time() - started < 25:
                payload = json.loads(ws.recv())
                if payload.get("code") not in (0, None):
                    return {"available": False, "message": compact_text(payload.get("message"))[:160]}
                data = payload.get("data") or {}
                if data.get("status") == 2:
                    xml_data = data.get("data") or ""
                    xml_text = base64.b64decode(xml_data).decode("utf-8", errors="replace") if xml_data else ""
                    scores = parse_xfyun_scores(xml_text)
                    score, status, feedback = xfyun_feedback(target_text, scores)
                    return {
                        "available": True,
                        "provider": "xfyun_ise",
                        "category": category,
                        "score": score,
                        "status": status,
                        "feedback": feedback,
                        "scores": scores,
                    }
            return {"available": False, "message": "讯飞评测超时。"}
        finally:
            try:
                ws.close()
            except Exception:
                pass
    except Exception as exc:
        return {"available": False, "message": f"讯飞评测暂不可用：{str(exc)[:120]}"}


def submit_voice_practice(student_id: int, payload: dict[str, Any], parent_id: int | None = None) -> dict[str, Any]:
    student = get_student_for_access(student_id, parent_id)
    target_text = compact_text(payload.get("target_text"))
    if not target_text:
        raise ValueError("缺少跟读内容")
    practice_date = compact_text(payload.get("practice_date")) or today_text()
    target_type = compact_text(payload.get("target_type")) or "sentence"
    transcript = compact_text(payload.get("transcript"))
    score = pronunciation_text_score(target_text, transcript)
    client_score = payload.get("score")
    if client_score not in (None, ""):
        try:
            score = max(score, min(max(int(float(client_score)), 0), 100))
        except (TypeError, ValueError):
            pass
    status, feedback = voice_feedback(target_text, transcript, score)
    assessment_mode = compact_text(payload.get("assessment_mode")).lower() or "local"
    use_professional = assessment_mode in {"professional", "xfyun", "paid"} or truthy(payload.get("use_professional_assessment"))
    audio_data_url = compact_text(payload.get("audio_data_url"))
    existing_professional = None
    if use_professional and not truthy(payload.get("force_professional_assessment")):
        existing_professional = get_row(
            """
            SELECT id, score, feedback_text
            FROM voice_practice_sessions
            WHERE student_id=? AND practice_date=? AND target_text=?
              AND assessment_provider='xfyun_ise' AND assessment_available=1
            ORDER BY id DESC
            LIMIT 1
            """,
            (student_id, practice_date, target_text),
        )
    if existing_professional:
        use_professional = False
        feedback = (
            f"{feedback} 这个内容今天已经做过一次专业评测（{int(existing_professional['score'])}分），"
            "后续跟读先按免费练习记录，不再重复消耗次数。"
        )
    audio_url = save_voice_audio(student, audio_data_url, target_text) if use_professional and audio_data_url else ""
    assessment: dict[str, Any] = {
        "available": False,
        "provider": "local_practice",
        "message": "本次为免费练习：按浏览器识别文本和关键词命中给出初步判断，不消耗讯飞专业评测次数。",
    }
    if use_professional and not audio_url:
        assessment = {
            "available": False,
            "provider": "xfyun_ise",
            "message": "专业评测需要先录音；普通跟读练习不会消耗次数。",
        }
    elif audio_url:
        usage = xfyun_assessment_usage(student_id, practice_date)
        if usage["limit"] and usage["remaining"] <= 0:
            assessment = {
                "available": False,
                "provider": "xfyun_ise",
                "quota": usage,
                "message": f"今天专业评测次数已用完（{usage['used']}/{usage['limit']}）。可以继续练习，明天再做专业评分。",
            }
        else:
            assessment = assess_voice_with_xfyun(target_text, target_type, audio_url)
            assessment["quota"] = xfyun_assessment_usage(student_id, practice_date)
    if assessment.get("available"):
        score = int(assessment.get("score") or score)
        status = compact_text(assessment.get("status")) or status
        feedback = compact_text(assessment.get("feedback")) or feedback
    elif assessment.get("message") and "次数已用完" in compact_text(assessment.get("message")):
        feedback = f"{feedback} {assessment.get('message')}"
    stored_payload = dict(payload)
    stored_payload["assessment_mode"] = "professional" if use_professional else "local"
    if not use_professional:
        stored_payload.pop("audio_data_url", None)
    if assessment.get("available"):
        stored_payload["_pronunciation_assessment"] = {
            "provider": assessment.get("provider"),
            "category": assessment.get("category"),
            "score": assessment.get("score"),
            "status": assessment.get("status"),
            "scores": assessment.get("scores"),
        }
    elif assessment.get("message"):
        stored_payload["_pronunciation_assessment"] = {
            "provider": "xfyun_ise",
            "available": False,
            "message": assessment.get("message"),
        }
    created = now_text()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO voice_practice_sessions (
              student_id, parent_id, practice_date, target_type, target_text,
              target_meaning, scenario, transcript, score, status,
              feedback_text, audio_url, raw_payload, created_at, assessment_provider, assessment_available
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                student_id,
                parent_id,
                practice_date,
                target_type,
                target_text,
                compact_text(payload.get("target_meaning")),
                compact_text(payload.get("scenario")),
                transcript,
                score,
                status,
                feedback,
                audio_url,
                json.dumps(stored_payload, ensure_ascii=False)[:5000],
                created,
                "xfyun_ise" if assessment.get("available") else "",
                1 if assessment.get("available") else 0,
            ),
        )
        conn.commit()
    assessment_quota = xfyun_assessment_usage(student_id, practice_date)
    return {
        "session_id": int(cur.lastrowid),
        "student_id": student_id,
        "target_text": target_text,
        "transcript": transcript,
        "score": score,
        "status": status,
        "feedback": feedback,
        "audio_url": audio_url,
        "assessment": {
            "provider": assessment.get("provider") if assessment.get("available") else "",
            "available": bool(assessment.get("available")),
            "category": assessment.get("category") or "",
            "scores": assessment.get("scores") or {},
            "quota": assessment_quota,
            "message": "" if assessment.get("available") else assessment.get("message", ""),
            "mode": "professional" if use_professional else "local",
        },
        "created_at": created,
    }


def list_voice_practice_samples_admin(limit: int = 20) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 20), 50))
    return list_rows(
        """
        SELECT v.id, v.student_id, s.student_code, s.display_name, v.practice_date,
               v.target_type, v.target_text, v.transcript, v.score, v.status,
               v.feedback_text, v.audio_url, v.assessment_provider, v.assessment_available,
               v.created_at
        FROM voice_practice_sessions v
        JOIN students s ON s.id = v.student_id
        ORDER BY v.id DESC
        LIMIT ?
        """,
        (limit,),
    )


def preview_voice_assessment_admin(payload: dict[str, Any]) -> dict[str, Any]:
    source_kind = compact_text(payload.get("source_kind"))
    session_id = int(payload.get("session_id") or 0)
    target_text = compact_text(payload.get("target_text"))
    target_type = compact_text(payload.get("target_type")) or "sentence"
    transcript = compact_text(payload.get("transcript"))
    practice_date = compact_text(payload.get("practice_date")) or today_text()
    quota_student_id = payload.get("student_id")
    quota_student_id = int(quota_student_id) if compact_text(quota_student_id) else 0
    source: dict[str, Any] = {"kind": source_kind or ("sample" if session_id else "upload")}
    audio_path: Path | None = None
    temp_path: Path | None = None
    session: dict[str, Any] | None = None
    try:
        if source["kind"] == "sample":
            if not session_id:
                raise ValueError("请先选择一条真实录音")
            session = get_row(
                """
                SELECT v.id, v.student_id, s.student_code, s.display_name, v.practice_date,
                       v.target_type, v.target_text, v.transcript, v.score, v.status,
                       v.feedback_text, v.audio_url, v.assessment_provider, v.assessment_available,
                       v.created_at
                FROM voice_practice_sessions v
                JOIN students s ON s.id = v.student_id
                WHERE v.id=?
                """,
                (session_id,),
            )
            if not session:
                raise ValueError("录音样本不存在")
            audio_url = compact_text(session.get("audio_url"))
            if not audio_url:
                raise ValueError("样本没有可用录音链接")
            audio_path = upload_url_to_path(audio_url)
            if not audio_path:
                raise ValueError("样本录音链接无效")
            quota_student_id = int(session["student_id"])
            source.update(
                {
                    "session_id": session_id,
                    "student_id": int(session["student_id"]),
                    "student_code": session.get("student_code") or "",
                    "display_name": session.get("display_name") or "",
                    "audio_url": audio_url,
                    "created_at": session.get("created_at") or "",
                }
            )
            target_text = target_text or compact_text(session.get("target_text"))
            target_type = target_type or compact_text(session.get("target_type")) or "sentence"
            transcript = transcript or compact_text(session.get("transcript"))
        else:
            audio_url = compact_text(payload.get("audio_url"))
            if audio_url:
                audio_path = upload_url_to_path(audio_url)
                if not audio_path:
                    raise ValueError("录音链接必须是 /xueji/uploads/... 格式")
                source.update({"audio_url": audio_url})
            else:
                raw, ext = decode_audio_data_url(compact_text(payload.get("audio_data_url")))
                with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                    tmp.write(raw)
                    temp_path = Path(tmp.name)
                audio_path = temp_path
                source.update({"uploaded": True, "size": len(raw), "file_ext": ext})
            if quota_student_id:
                source["student_id"] = quota_student_id
        if not target_text:
            raise ValueError("请填写目标文本")

        quota = xfyun_assessment_usage(quota_student_id, practice_date) if quota_student_id else None
        xfyun_result: dict[str, Any] = {}
        if quota and quota["limit"] and quota["remaining"] <= 0:
            xfyun_result = {
                "available": False,
                "provider": "xfyun_ise",
                "message": f"今天专业评测次数已用完（{quota['used']}/{quota['limit']}）。",
            }
        else:
            xfyun_result = assess_voice_with_xfyun_audio_path(target_text, target_type, audio_path)

        fallback_used = not bool(xfyun_result.get("available"))
        if fallback_used:
            fallback_score = pronunciation_text_score(target_text, transcript)
            fallback_status, fallback_feedback = voice_feedback(target_text, transcript, fallback_score)
            final_assessment = {
                "provider": "local_fallback",
                "available": False,
                "score": fallback_score,
                "status": fallback_status,
                "feedback": fallback_feedback,
            }
        else:
            final_assessment = dict(xfyun_result)

        would_consume_quota = None
        if quota is not None:
            would_consume_quota = bool(final_assessment.get("provider") == "xfyun_ise" and final_assessment.get("available"))

        return {
            "source": source,
            "practice_date": practice_date,
            "target_text": target_text,
            "target_type": target_type,
            "transcript": transcript,
            "quota": quota,
            "would_consume_quota": would_consume_quota,
            "fallback_used": fallback_used,
            "xfyun": xfyun_result,
            "assessment": final_assessment,
        }
    finally:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def decode_ark_tts_lines(lines: Any) -> bytes:
    audio = bytearray()
    for raw_line in lines:
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else str(raw_line)
        if not line.strip():
            continue
        item = json.loads(line)
        code = int(item.get("code") or 0)
        if code == 20000000:
            break
        if code != 0:
            message = compact_text(item.get("message") or item.get("msg")) or "unknown error"
            raise RuntimeError(f"Ark TTS {code}: {message[:120]}")
        if item.get("data"):
            audio.extend(base64.b64decode(item["data"]))
    return bytes(audio)


def request_ark_tts(text: str, speed: float) -> tuple[bytes, str]:
    endpoint = os.environ.get("ARK_TTS_URL") or "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional"
    audio_format = os.environ.get("ARK_TTS_FORMAT") or "mp3"
    body = json.dumps(
        {
            "req_params": {
                "text": text,
                "speaker": os.environ.get("ARK_TTS_VOICE") or "zh_female_vv_uranus_bigtts",
                "audio_params": {
                    "format": audio_format,
                    "sample_rate": int(os.environ.get("ARK_TTS_SAMPLE_RATE") or "24000"),
                    "speech_rate": int(round((speed - 1.0) * 100)),
                },
            }
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": os.environ.get("ARK_TTS_API_KEY") or "",
            "X-Api-Resource-Id": os.environ.get("ARK_TTS_RESOURCE_ID") or "seed-tts-2.0",
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Control-Require-Usage-Tokens-Return": "*",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return decode_ark_tts_lines(resp), resp.headers.get("X-Tt-Logid", "")


def synthesize_voice_practice_tts(student_id: int, payload: dict[str, Any], parent_id: int | None = None) -> dict[str, Any]:
    student = get_student_for_access(student_id, parent_id)
    text = compact_text(payload.get("text"))
    if not text:
        raise ValueError("缺少朗读文本")
    cleanup_tts_cache()
    model = os.environ.get("XIAOMI_TTS_MODEL") or os.environ.get("XUEJI_TTS_MODEL") or "mimo-v2.5-tts"
    api_key = os.environ.get("XIAOMI_API_KEY") or os.environ.get("XUEJI_TTS_API_KEY")
    base_url = (os.environ.get("XIAOMI_BASE_URL") or os.environ.get("XUEJI_TTS_BASE_URL") or "").rstrip("/")
    voice = os.environ.get("XIAOMI_TTS_VOICE") or os.environ.get("XUEJI_TTS_VOICE") or "Chloe"
    audio_format = os.environ.get("XIAOMI_TTS_FORMAT") or os.environ.get("XUEJI_TTS_FORMAT") or "wav"
    speed_text = compact_text(payload.get("speed")) or os.environ.get("XIAOMI_TTS_SPEED") or os.environ.get("XUEJI_TTS_SPEED") or "0.8"
    try:
        speed = max(0.5, min(1.2, float(speed_text)))
    except (TypeError, ValueError):
        speed = 0.8
    provider = (os.environ.get("XUEJI_TTS_PROVIDER") or "auto").strip().lower()
    ark_key = os.environ.get("ARK_TTS_API_KEY")
    if provider == "ark" or (provider == "auto" and ark_key):
        if not ark_key:
            return {"audio_url": "", "fallback": "browser_speech_synthesis", "message": "Ark TTS未配置。"}
        try:
            raw, log_id = request_ark_tts(text, speed)
        except Exception as exc:
            return {"audio_url": "", "fallback": "browser_speech_synthesis", "message": f"服务器TTS暂不可用：{str(exc)[:160]}"}
        if not raw:
            return {"audio_url": "", "fallback": "browser_speech_synthesis", "message": "服务器TTS没有返回音频。"}
        student_dir = UPLOAD_DIR / str(student["student_code"])
        student_dir.mkdir(parents=True, exist_ok=True)
        ark_format = os.environ.get("ARK_TTS_FORMAT") or "mp3"
        cache_key = json.dumps(
            {
                "text": text,
                "provider": "ark",
                "resource": os.environ.get("ARK_TTS_RESOURCE_ID") or "seed-tts-2.0",
                "voice": os.environ.get("ARK_TTS_VOICE") or "zh_female_vv_uranus_bigtts",
                "format": ark_format,
                "speed": speed,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()[:12]
        suffix = ".mp3" if ark_format == "mp3" else f".{ark_format}"
        file_name = f"tts_{digest}{suffix}"
        (student_dir / file_name).write_bytes(raw)
        return {
            "audio_url": upload_file_url(str(student["student_code"]), file_name),
            "fallback": "",
            "message": "ok",
            "playback_rate": 1,
            "provider": "ark_seed_tts_2",
            "log_id": log_id,
        }
    if not api_key or not base_url:
        return {"audio_url": "", "fallback": "browser_speech_synthesis", "message": "服务器TTS未配置，前端将使用浏览器朗读。"}

    use_mimo_chat_tts = model.startswith("mimo-") and not (os.environ.get("XIAOMI_TTS_URL") or os.environ.get("XUEJI_TTS_URL"))
    if use_mimo_chat_tts:
        endpoint = chat_endpoint(base_url)
        body_obj = {
            "model": model,
            "messages": [
                {"role": "user", "content": "Please read the following English learning item clearly and naturally."},
                {"role": "assistant", "content": text},
            ],
            "audio": {"format": audio_format, "voice": voice, "speed": speed},
            "stream": False,
        }
    else:
        endpoint = os.environ.get("XIAOMI_TTS_URL") or os.environ.get("XUEJI_TTS_URL") or f"{base_url}/audio/speech"
        body_obj = {"model": model, "voice": voice, "input": text, "format": audio_format, "speed": speed}

    body = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}", "api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            response_body = resp.read()
    except Exception as exc:
        return {"audio_url": "", "fallback": "browser_speech_synthesis", "message": f"服务器TTS暂不可用：{str(exc)[:160]}"}
    if use_mimo_chat_tts:
        try:
            data = json.loads(response_body.decode("utf-8"))
            audio_data = (((data.get("choices") or [{}])[0].get("message") or {}).get("audio") or {}).get("data")
            raw = base64.b64decode(audio_data) if audio_data else b""
        except Exception as exc:
            return {"audio_url": "", "fallback": "browser_speech_synthesis", "message": f"服务器TTS返回格式暂不可用：{str(exc)[:120]}"}
    else:
        raw = response_body
    if not raw:
        return {"audio_url": "", "fallback": "browser_speech_synthesis", "message": "服务器TTS没有返回音频。"}
    student_dir = UPLOAD_DIR / str(student["student_code"])
    student_dir.mkdir(parents=True, exist_ok=True)
    cache_key = json.dumps(
        {"text": text, "model": model, "voice": voice, "format": audio_format, "speed": speed},
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()[:12]
    suffix = ".wav" if audio_format == "wav" else ".mp3" if audio_format == "mp3" else f".{audio_format}"
    file_name = f"tts_{digest}{suffix}"
    (student_dir / file_name).write_bytes(raw)
    return {"audio_url": upload_file_url(str(student["student_code"]), file_name), "fallback": "", "message": "ok", "playback_rate": 1}


def create_student_access_link(student_id: int, parent_id: int | None = None) -> dict[str, Any]:
    student = get_student_for_access(student_id, parent_id)
    row = get_row(
        """
        SELECT * FROM student_access_tokens
        WHERE student_id=? AND enabled=1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (student_id,),
    )
    if row:
        token = row["token"]
    else:
        token = secrets.token_urlsafe(24)
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO student_access_tokens (token, student_id, parent_id, label, enabled, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (token, student_id, student.get("parent_id"), "学生端链接", now_text()),
            )
            conn.commit()
    return {
        "student_id": student_id,
        "student_code": student.get("student_code"),
        "token": token,
        "path": f"{BASE_PREFIX}/student?token={quote(token)}",
        "status": "enabled",
    }


def student_from_access_token(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return resolve_student_portal_token(token)


def latest_card_plan_for_student(student: dict[str, Any]) -> dict[str, Any] | None:
    student_dir = UPLOAD_DIR / str(student.get("student_code"))
    if not student_dir.exists():
        return None
    fallback_future: dict[str, Any] | None = None
    today = datetime.now(TZ).date()
    for path in sorted(student_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and isinstance(data.get("days"), list) and data.get("days"):
            data["_source_path"] = str(path)
            data["_source_mtime"] = datetime.fromtimestamp(path.stat().st_mtime, TZ).strftime("%Y-%m-%d %H:%M:%S")
            start_date = parse_date_text(data.get("start_date"))
            if start_date and start_date > today:
                if fallback_future is None:
                    fallback_future = data
                continue
            return data
    return fallback_future


def clean_student_day_text(value: Any, fallback: str = "") -> str:
    text = compact_text(value)
    if not text:
        return fallback
    parts = [part.strip() for part in re.split(r"[|｜]", text) if part.strip()]
    if len(parts) > 1:
        kept = [part for part in parts if part.count("?") < 2 and part.count("？") < 2]
        text = "｜".join(kept)
    if text.count("?") >= 2 or text.count("？") >= 2:
        return fallback
    return text or fallback


def learning_focus_for_cards(cards: list[dict[str, Any]]) -> str:
    if any(card_subject(card) == "英语" or compact_text(card.get("audio_text")) for card in cards):
        return "英语"
    physics_markers = ["随手实验", "振动", "参照物", "速度", "影子", "光线", "测量", "液化", "汽化", "音调", "响度"]
    if any(
        card_subject(card) == "物理"
        or any(marker in " ".join(compact_text(card.get(key)) for key in ("tag", "q", "a")) for marker in physics_markers)
        for card in cards
    ):
        return "物理"
    return ""


def memory_story_for_cards(cards: list[dict[str, Any]]) -> str:
    focus = learning_focus_for_cards(cards)
    if focus == "英语":
        return "今天先追一段英语剧情：先猜发生了什么，再听5个目标，最后用中文夹英语讲出关键线索。"
    if focus == "物理":
        return "今天先抓一个怪现象：说出哪里奇怪、猜一个原因，再用物理词解释；最后把实验格随手试一次。"
    subjects = []
    cues = []
    for card in cards[:6]:
        subject = card_subject(card)
        if subject and subject not in subjects:
            subjects.append(subject)
        answer = compact_text(card.get("a"))
        cue_match = re.search(r"线索[：:]\s*([^。；;]+)", answer)
        if cue_match:
            cues.append(cue_match.group(1))
    if not cards:
        return "今天先完成一小步：听一遍、说一遍、能想起关键词就算过。"
    subject_text = "、".join(subjects) or "今天的知识"
    cue_text = "；".join(cues[:3]) or "先看题目，再想关键词，最后说出一句完整答案"
    return f"把{subject_text}放进一条上学路：出门想语文一句话，走到楼梯看历史时间线，到教室说理科和道法关键词。今天只抓这些线索：{cue_text}。"


def student_guide_note_for_knowledge(knowledge: dict[str, Any]) -> str:
    cards = knowledge.get("cards") if isinstance(knowledge.get("cards"), list) else []
    focus = learning_focus_for_cards(cards)
    if focus == "英语":
        return "今天是英语故事周：先猜剧情，再听读5个目标，翻卡找线索，最后用中文夹英语复述。"
    if focus == "物理":
        return "今天是物理兴趣周：先看怪现象，再说原因和物理词，最后完成一格随手实验。"
    return "按今日计划先复习到期卡，再完成新卡；先想再看答案，请家长最后抽问。"


def feedback_pacing_for_student(student_id: int) -> dict[str, Any]:
    feedback = list_rows(
        """
        SELECT feedback_date, planned_cards, completed_cards, known_cards, completion_rate,
               known_rate, star_card_codes, mood
        FROM daily_feedback
        WHERE student_id=?
        ORDER BY feedback_date DESC, id DESC
        LIMIT 3
        """,
        (student_id,),
    )
    if not feedback:
        return {
            "level": "normal",
            "review_limit": 3,
            "new_limit": 8,
            "star_terms": [],
            "note": "今天按计划推进；先做间隔回炉，再做今日新卡。",
        }
    latest = feedback[0]
    completion = latest.get("completion_rate")
    known = latest.get("known_rate")
    planned = int(latest.get("planned_cards") or 0)
    completed = int(latest.get("completed_cards") or 0)
    known_cards = int(latest.get("known_cards") or 0)
    completion_rate = float(completion) if completion is not None else (completed / planned if planned else 0)
    known_rate = float(known) if known is not None else (known_cards / planned if planned else 0)
    star_terms = [
        term.strip()
        for term in re.split(r"[、,，;\s]+", compact_text(latest.get("star_card_codes")))
        if term.strip()
    ]
    if completion_rate < 0.5 or known_rate < 0.3:
        return {
            "level": "support",
            "review_limit": 4,
            "new_limit": 4,
            "star_terms": star_terms,
            "note": "最近完成或答对偏低：今天先回炉旧卡，再做少量新卡，不硬冲进度。",
        }
    if completion_rate >= 0.85 and known_rate >= 0.8:
        return {
            "level": "advance",
            "review_limit": 2,
            "new_limit": 8,
            "star_terms": star_terms,
            "note": "最近完成稳定：保留少量回炉卡，今日新卡可以按计划完整推进。",
        }
    return {
        "level": "normal",
        "review_limit": 3,
        "new_limit": 6,
        "star_terms": star_terms,
        "note": "今天按正常节奏：先复 D1/D3/D7，再做今日卡。",
    }


def card_matches_terms(card: dict[str, Any], terms: list[str]) -> bool:
    if not terms:
        return False
    text = " ".join(compact_text(card.get(key)) for key in ("tag", "q", "a"))
    return any(term and term in text for term in terms)


def due_review_cards(history_days: list[dict[str, Any]], idx: int, pacing: dict[str, Any]) -> list[dict[str, Any]]:
    due_offsets = [1, 3, 7]
    candidates: list[dict[str, Any]] = []
    for offset in due_offsets:
        source_idx = idx - offset
        if source_idx < 0 or source_idx >= len(history_days):
            continue
        source_day = history_days[source_idx]
        for card in source_day.get("cards") or []:
            if not isinstance(card, dict):
                continue
            review_card = dict(card)
            review_card["tag"] = f"回炉D-{offset}｜{compact_text(card.get('tag')) or '旧卡'}"
            review_card["review_from_day"] = source_idx
            candidates.append(review_card)
    star_terms = pacing.get("star_terms") or []
    starred = [card for card in candidates if card_matches_terms(card, star_terms)]
    others = [card for card in candidates if not card_matches_terms(card, star_terms)]
    limit = int(pacing.get("review_limit") or 3)
    return (starred + others)[:limit]


def student_knowledge_today(student: dict[str, Any]) -> dict[str, Any]:
    plan = latest_card_plan_for_student(student)
    if not plan:
        return {
            "title": "今日知识卡",
            "module": "暂无卡片",
            "cards": [],
            "memory_story": "今天先完成一小步：听一遍、说一遍、能想起关键词就算过。",
            "current_day_index": 0,
            "days_total": 0,
            "review_cards": [],
            "history_days": [],
            "adjustment_note": "还没有可读取的周计划卡片。",
        }
    days = plan.get("days") or []
    plan_anchor = compact_text(plan.get("start_date")) or compact_text(plan.get("_source_mtime")) or compact_text(student.get("created_at"))
    anchor_date = plan_anchor[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", plan_anchor) else ""
    if anchor_date:
        feedback_count = int(
            get_row(
                "SELECT COUNT(*) AS count FROM daily_feedback WHERE student_id=? AND feedback_date>?",
                (student["id"], anchor_date),
            )["count"]  # type: ignore[index]
        )
    else:
        feedback_count = int(
            get_row("SELECT COUNT(*) AS count FROM daily_feedback WHERE student_id=?", (student["id"],))["count"]  # type: ignore[index]
        )
    natural_day_offset = day_offset_from_timestamp(plan_anchor)
    idx = min(max(feedback_count, natural_day_offset, 0), max(len(days) - 1, 0))
    history_days = []
    for day_idx, day in enumerate(days):
        if not isinstance(day, dict):
            continue
        day_cards = day.get("cards") if isinstance(day.get("cards"), list) else []
        day_cards = day_cards[:8]
        day_english_targets = normalize_voice_targets(day.get("english_targets"))
        history_days.append(
            {
                "index": day_idx,
                "title": clean_student_day_text(day.get("title"), f"Day {day_idx + 1:02d}"),
                "module": clean_student_day_text(day.get("module")),
                "cards": day_cards,
                "english_targets": day_english_targets,
                "memory_story": memory_story_for_cards(day_cards),
            }
        )
    current_day = history_days[idx] if history_days and 0 <= idx < len(history_days) else {"title": f"Day {idx + 1:02d}", "module": "", "cards": [], "memory_story": ""}
    pacing = feedback_pacing_for_student(int(student["id"]))
    review_cards = due_review_cards(history_days, idx, pacing)
    new_limit = int(pacing.get("new_limit") or 8)
    cards = (current_day.get("cards") or [])[:new_limit]
    return {
        "title": current_day.get("title") or f"Day {idx + 1:02d}",
        "module": current_day.get("module") or "",
        "cards": cards,
        "memory_story": current_day.get("memory_story") or "",
        "current_day_index": idx,
        "days_total": len(history_days),
        "review_cards": review_cards,
        "history_days": history_days,
        "adjustment_note": pacing.get("note") or "",
        "plan_source": Path(compact_text(plan.get("_source_path"))).name if compact_text(plan.get("_source_path")) else "",
        "plan_started_at": plan_anchor,
    }


def get_student_portal_today(token: str) -> dict[str, Any]:
    student, access = resolve_student_portal_token(token)
    voice = get_voice_practice_today(int(student["id"]), None)
    knowledge = student_knowledge_today(student)
    account = student_account_public(student_account_for_student(int(student["id"])))
    return {
        "student": {
            "id": student["id"],
            "student_code": student.get("student_code"),
            "display_name": student.get("display_name") or "同学",
            "grade_region": student.get("grade_region") or "",
            "goal": student.get("goal") or "",
        },
        "access": {"last_seen_at": access.get("last_seen_at") or ""},
        "account": account,
        "date": today_text(),
        "knowledge": knowledge,
        "voice": voice,
        "guide": {
            "steps": ["看今日任务", "读背英语", "翻知识卡", "先想再看答案", "请家长抽问"],
            "note": student_guide_note_for_knowledge(knowledge),
        },
    }


def submit_student_portal_voice(token: str, payload: dict[str, Any]) -> dict[str, Any]:
    student, _ = resolve_student_portal_token(token)
    return submit_voice_practice(int(student["id"]), payload, None)


def synthesize_student_portal_tts(token: str, payload: dict[str, Any]) -> dict[str, Any]:
    student, _ = student_from_access_token(token)
    return synthesize_voice_practice_tts(int(student["id"]), payload, None)


class App(BaseHTTPRequestHandler):
    server_version = "XuejiLoop/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        message = "%s %s\n" % (now_text(), fmt % args)
        (ROOT / "server.log").open("a", encoding="utf-8").write(message)

    def send_json(self, data: Any, status: int = 200) -> None:
        body = json_dumps(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Parent-Token, X-Student-Token, X-Admin-Key")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, body: str, status: int = 200, content_type: str = "text/plain; charset=utf-8") -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        if content_type.startswith("text/html"):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(raw)

    def send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_json({"error": "file_not_found"}, status=404)
            return
        raw = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        safe_name = clean_filename(path.name)
        ascii_stem = re.sub(r"[^0-9A-Za-z._-]+", "_", Path(safe_name).stem).strip("._")
        if not safe_name.isascii() or not re.search(r"[A-Za-z]", ascii_stem):
            version = re.search(r"V\d+", safe_name, flags=re.IGNORECASE)
            ascii_stem = f"xueji_cards_{version.group(0).upper()}" if version else "xueji_cards"
        ascii_name = f"{ascii_stem}{Path(safe_name).suffix}"
        encoded_name = quote(safe_name)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header(
            "Content-Disposition",
            f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}",
        )
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw.strip() else {}

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Parent-Token, X-Student-Token, X-Admin-Key")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = normalize_route(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if path == "/":
                html = (ROOT / "xueji_parent_h5.html").read_text(encoding="utf-8")
                self.send_text(html, content_type="text/html; charset=utf-8")
            elif path == "/student":
                html = (ROOT / "xueji_student_h5.html").read_text(encoding="utf-8")
                self.send_text(html, content_type="text/html; charset=utf-8")
            elif path == "/admin":
                html = (ROOT / "xueji_loop_tool_api.html").read_text(encoding="utf-8")
                self.send_text(html, content_type="text/html; charset=utf-8")
            elif path == "/health":
                self.send_json({"status": "ok", "db": str(DB_PATH), "now": now_text()})
            elif path == "/api/parents/me":
                parent = require_parent(self.headers)
                self.send_json({"parent": public_parent(parent), "students": list_parent_students(int(parent["id"]))})
            elif path == "/api/parents/me/referrals":
                parent = require_parent(self.headers)
                self.send_json(parent_referral_summary(int(parent["id"])))
            elif path == "/api/parents/me/students":
                parent = require_parent(self.headers)
                self.send_json({"items": list_parent_students(int(parent["id"]))})
            elif path == "/api/admin/parents":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json({"items": list_parents_admin()})
            elif path == "/api/admin/entitlement-presets":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json({"items": entitlement_presets_payload()})
            elif path == "/api/admin/referrals":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json({"items": list_referrals_admin()})
            elif path == "/api/admin/model-configs":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json({"items": list_model_configs(), "task_labels": TASK_LABELS})
            elif path == "/api/admin/ai-jobs":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                status = query.get("status", ["all"])[0]
                self.send_json({"items": list_ai_jobs_admin(status)})
            elif path == "/api/admin/ai-usage":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                logs = list_rows(
                    """
                    SELECT l.*, p.phone, p.nickname, s.student_code, s.display_name
                    FROM ai_usage_logs l
                    LEFT JOIN parents p ON p.id = l.parent_id
                    LEFT JOIN students s ON s.id = l.student_id
                    ORDER BY l.id DESC
                    LIMIT 200
                    """
                )
                self.send_json({"items": logs})
            elif path == "/api/admin/voice-practice/samples":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                limit = int(query.get("limit", ["20"])[0] or 20)
                self.send_json({"items": list_voice_practice_samples_admin(limit)})
            elif path == "/api/admin/learning-resources":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json(list_learning_resources_admin(query))
            elif path == "/api/admin/learning-scope-index":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json(list_learning_scope_index_admin(query))
            elif path == "/api/admin/learning-coverage":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json(list_learning_coverage_admin(query))
            elif path == "/api/admin/learning-collection-tasks":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json(list_learning_collection_tasks_admin(query))
            elif path == "/api/admin/learning-collection-task-records":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json(list_learning_collection_task_records_admin(query))
            elif path == "/api/admin/knowledge-points":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json(list_knowledge_points_admin(query))
            elif path == "/api/admin/exam-patterns":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json(list_exam_patterns_admin(query))
            elif match := re.fullmatch(r"/api/ai-jobs/(\d+)", path):
                if has_admin_access(self.headers, query):
                    parent_id = None
                else:
                    parent = require_parent(self.headers)
                    parent_id = int(parent["id"])
                self.send_json(get_ai_job(int(match.group(1)), parent_id))
            elif path == "/api/students":
                if has_admin_access(self.headers, query):
                    students = list_rows(
                        """
                        SELECT id, student_code, display_name, grade_region, goal,
                               current_level, current_card_load, execution_mode,
                               current_plan_version_id, status, created_at
                        FROM students
                        ORDER BY id DESC
                        LIMIT 100
                        """
                    )
                else:
                    parent = require_parent(self.headers)
                    students = list_parent_students(int(parent["id"]))
                self.send_json({"items": students})
            elif match := re.fullmatch(r"/api/students/(\d+)", path):
                if has_admin_access(self.headers, query):
                    parent_id = None
                else:
                    parent = require_parent(self.headers)
                    ensure_parent_service(parent)
                    parent_id = int(parent["id"])
                self.send_json(get_student_detail(int(match.group(1)), parent_id))
            elif match := re.fullmatch(r"/api/students/(\d+)/today", path):
                if has_admin_access(self.headers, query):
                    parent_id = None
                else:
                    parent = require_parent(self.headers)
                    ensure_parent_service(parent)
                    parent_id = int(parent["id"])
                self.send_json(get_today(int(match.group(1)), parent_id))
            elif match := re.fullmatch(r"/api/students/(\d+)/voice-practice/today", path):
                if has_admin_access(self.headers, query):
                    parent_id = None
                else:
                    parent = require_parent(self.headers)
                    ensure_parent_service(parent)
                    parent_id = int(parent["id"])
                self.send_json(get_voice_practice_today(int(match.group(1)), parent_id))
            elif match := re.fullmatch(r"/api/students/(\d+)/student-link", path):
                parent = require_parent(self.headers)
                ensure_parent_service(parent)
                self.send_json(create_student_access_link(int(match.group(1)), int(parent["id"])))
            elif match := re.fullmatch(r"/api/student-access/([^/]+)/today", path):
                self.send_json(get_student_portal_today(unquote(match.group(1))))
            elif match := re.fullmatch(r"/api/students/(\d+)/plans", path):
                if has_admin_access(self.headers, query):
                    parent_id = None
                    parent = None
                else:
                    parent = require_parent(self.headers)
                    ensure_parent_service(parent)
                    parent_id = int(parent["id"])
                student = get_student_for_access(int(match.group(1)), parent_id)
                plans = list_rows(
                    "SELECT * FROM plan_versions WHERE student_id=? ORDER BY id DESC",
                    (student["id"],),
                )
                plans = [apply_plan_entitlement(plan, parent) for plan in plans]
                self.send_json({"items": plans})
            elif path == "/api/admin/adjustments":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                status = query.get("status", ["pending"])[0]
                actions = list_rows(
                    """
                    SELECT a.id AS action_id, a.student_id, s.student_code,
                           s.display_name, a.feedback_id, a.action_type,
                           a.trigger_reason, a.action_text, a.priority,
                           a.status, a.created_at
                    FROM adjustment_actions a
                    JOIN students s ON s.id = a.student_id
                    WHERE a.status=?
                    ORDER BY CASE a.priority WHEN 'high' THEN 0 ELSE 1 END, a.id DESC
                    LIMIT 100
                    """,
                    (status,),
                )
                self.send_json({"items": actions})
            elif match := re.fullmatch(r"/uploads/([^/]+)/([^/]+)", path):
                student_code = clean_filename(unquote(match.group(1)))
                file_name = clean_filename(unquote(match.group(2)))
                target = (UPLOAD_DIR / student_code / file_name).resolve()
                if not str(target).startswith(str(UPLOAD_DIR.resolve())):
                    self.send_json({"error": "invalid_path"}, status=400)
                    return
                self.send_file(target)
            else:
                self.send_json({"error": "not_found", "path": path}, status=404)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
        except Exception as exc:  # pragma: no cover - surfaced in local tool
            self.send_json({"error": "server_error", "detail": str(exc)}, status=500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = normalize_route(parsed.path)
        query = parse_qs(parsed.query)
        try:
            payload = self.read_json()
            if path == "/api/parents/register":
                self.send_json(register_parent(payload), status=201)
            elif path == "/api/parents/login":
                self.send_json(login_parent(payload))
            elif path == "/api/parents/logout":
                self.send_json(logout_parent(token_from_headers(self.headers)))
            elif path == "/api/students/login":
                self.send_json(login_student(payload))
            elif path == "/api/students/logout":
                self.send_json(logout_student(student_token_from_headers(self.headers)))
            elif path == "/api/students/intake":
                parent = require_parent(self.headers)
                ensure_parent_service(parent)
                self.send_json(create_student(payload, int(parent["id"])), status=201)
            elif match := re.fullmatch(r"/api/students/(\d+)/daily-feedback", path):
                parent = require_parent(self.headers)
                ensure_parent_service(parent)
                self.send_json(save_feedback(int(match.group(1)), payload, int(parent["id"])), status=201)
            elif match := re.fullmatch(r"/api/students/(\d+)/evidence", path):
                parent = require_parent(self.headers)
                ensure_parent_service(parent)
                self.send_json(save_evidence_base64(int(match.group(1)), payload, int(parent["id"])), status=201)
            elif match := re.fullmatch(r"/api/students/(\d+)/voice-practice/submissions", path):
                parent = require_parent(self.headers)
                ensure_parent_service(parent)
                self.send_json(submit_voice_practice(int(match.group(1)), payload, int(parent["id"])), status=201)
            elif match := re.fullmatch(r"/api/students/(\d+)/voice-practice/tts", path):
                parent = require_parent(self.headers)
                ensure_parent_service(parent)
                self.send_json(synthesize_voice_practice_tts(int(match.group(1)), payload, int(parent["id"])))
            elif match := re.fullmatch(r"/api/students/(\d+)/student-link", path):
                parent = require_parent(self.headers)
                ensure_parent_service(parent)
                self.send_json(create_student_access_link(int(match.group(1)), int(parent["id"])), status=201)
            elif match := re.fullmatch(r"/api/students/(\d+)/account", path):
                if has_admin_access(self.headers, query):
                    parent_id = None
                else:
                    parent = require_parent(self.headers)
                    ensure_parent_service(parent)
                    parent_id = int(parent["id"])
                student = get_student_for_access(int(match.group(1)), parent_id)
                self.send_json(
                    upsert_student_account(
                        int(student["id"]),
                        payload.get("phone") or payload.get("login_phone") or "",
                        payload.get("password") or payload.get("login_password") or "",
                    ),
                    status=201,
                )
            elif match := re.fullmatch(r"/api/student-access/([^/]+)/voice-practice/submissions", path):
                self.send_json(submit_student_portal_voice(unquote(match.group(1)), payload), status=201)
            elif match := re.fullmatch(r"/api/student-access/([^/]+)/voice-practice/tts", path):
                self.send_json(synthesize_student_portal_tts(unquote(match.group(1)), payload))
            elif match := re.fullmatch(r"/api/students/(\d+)/ai-generate", path):
                parent = require_parent(self.headers)
                self.send_json(enqueue_ai_job(int(match.group(1)), parent, payload), status=202)
            elif match := re.fullmatch(r"/api/students/(\d+)/ai-jobs", path):
                parent = require_parent(self.headers)
                self.send_json(enqueue_ai_job(int(match.group(1)), parent, payload), status=202)
            elif path == "/api/admin/model-configs":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json(upsert_model_config(payload), status=201)
            elif path == "/api/admin/model-configs/xiaomi":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json(configure_xiaomi_models(payload), status=201)
            elif path == "/api/admin/voice-assessment/calibrate":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json(preview_voice_assessment_admin(payload), status=201)
            elif path == "/api/admin/learning-resources":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json(upsert_learning_resource_admin(payload), status=201)
            elif path == "/api/admin/knowledge-points":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json(upsert_knowledge_point_admin(payload), status=201)
            elif path == "/api/admin/exam-patterns":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json(upsert_exam_pattern_admin(payload), status=201)
            elif path == "/api/admin/learning-collection-task-records":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json(upsert_learning_collection_task_record_admin(payload), status=201)
            elif path == "/api/admin/learning-collection-task-records/sync":
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                sync_query = {
                    key: [str(value)]
                    for key, value in payload.items()
                    if key in {"stage", "grade", "subject", "q"} and compact_text(value)
                }
                self.send_json(sync_learning_collection_tasks_admin(sync_query), status=201)
            elif match := re.fullmatch(r"/api/admin/students/(\d+)/current-plan-docx", path):
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json(bind_current_plan_docx(int(match.group(1)), payload), status=201)
            elif match := re.fullmatch(r"/api/admin/students/(\d+)/password", path):
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json(reset_student_password_admin(int(match.group(1)), payload))
            elif match := re.fullmatch(r"/api/students/(\d+)/plans", path):
                if has_admin_access(self.headers, query):
                    parent_id = None
                else:
                    parent = require_parent(self.headers)
                    ensure_parent_service(parent)
                    parent_id = int(parent["id"])
                self.send_json(create_plan_version(int(match.group(1)), payload, parent_id), status=201)
            elif match := re.fullmatch(r"/api/students/(\d+)/cards-docx", path):
                if has_admin_access(self.headers, query):
                    parent_id = None
                else:
                    parent = require_parent(self.headers)
                    ensure_parent_service(parent)
                    parent_id = int(parent["id"])
                self.send_json(
                    generate_cards_docx_for_student(int(match.group(1)), parent_id, payload.get("note") or payload.get("adjustment_note") or ""),
                    status=201,
                )
            elif match := re.fullmatch(r"/api/plans/(\d+)/publish", path):
                if has_admin_access(self.headers, query):
                    parent_id = None
                else:
                    parent = require_parent(self.headers)
                    ensure_parent_service(parent)
                    parent_id = int(parent["id"])
                self.send_json(publish_plan(int(match.group(1)), payload, parent_id))
            elif match := re.fullmatch(r"/api/students/(\d+)/weekly-report", path):
                if has_admin_access(self.headers, query):
                    parent_id = None
                else:
                    parent = require_parent(self.headers)
                    ensure_parent_service(parent)
                    parent_id = int(parent["id"])
                self.send_json(generate_weekly_report(int(match.group(1)), payload, parent_id), status=201)
            elif match := re.fullmatch(r"/api/admin/parents/(\d+)/entitlement", path):
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json(update_parent_entitlement(int(match.group(1)), payload))
            elif match := re.fullmatch(r"/api/admin/parents/(\d+)/password", path):
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json(reset_parent_password_admin(int(match.group(1)), payload))
            elif match := re.fullmatch(r"/api/admin/ai-jobs/(\d+)/handle", path):
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                self.send_json(update_ai_job_admin(int(match.group(1)), payload))
            elif match := re.fullmatch(r"/api/admin/adjustments/(\d+)/handle", path):
                if not has_admin_access(self.headers, query):
                    self.send_json({"error": "admin_key_required"}, status=401)
                    return
                action_id = int(match.group(1))
                with connect() as conn:
                    conn.execute(
                        "UPDATE adjustment_actions SET status='handled', handled_at=? WHERE id=?",
                        (now_text(), action_id),
                    )
                    conn.commit()
                self.send_json({"status": "handled", "action_id": action_id})
            else:
                self.send_json({"error": "not_found", "path": path}, status=404)
        except json.JSONDecodeError:
            self.send_json({"error": "JSON格式不正确"}, status=400)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
        except Exception as exc:  # pragma: no cover - surfaced in local tool
            self.send_json({"error": "server_error", "detail": str(exc)}, status=500)


def main() -> int:
    init_db()
    cleanup_tts_cache(force=True)
    if "--check" in sys.argv:
        print(f"ok {DB_PATH}")
        return 0
    httpd = ThreadingHTTPServer((HOST, PORT), App)
    print(f"学记教育闭环工具已启动：http://{HOST}:{PORT}/")
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
