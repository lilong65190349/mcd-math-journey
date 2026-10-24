"""麦麦取经录 —— 本地进度与错题本（标准库 sqlite3）。

数据库文件：``runtime/progress.db``（首次使用时自动建目录、自动建表）。
可用环境变量 ``MMQJ_DB_PATH`` 指定到别处——``scripts/selfcheck.py`` 就是靠它
在一份临时库上复跑文档示例，不会碰用户自己的进度。
不联网、不上传，所有记录只存在本机；不采集任何儿童个人信息
（没有姓名、手机号、学校、生日等字段，只有题号与答题结果）。

多故事线
--------
``attempts`` 与 ``stage_clear`` 各有一列 ``story``（默认 ``'xiyou'``），记录这次作答属于哪条线。
老版本数据库（没有该列）在 :func:`connect` 时会自动迁移：**先复制成 ``progress.db.bak``，
再 ALTER TABLE 补列**，绝不删库重建，已有进度一条不丢。

闯关系统（v2.0）
----------------
新增两张表（都是 ``CREATE TABLE IF NOT EXISTS``，对老库零风险）：

* ``stage_runs``：一次「进关 → 抽题 → 全部答对」的完整记录（种子 / 用时 / 基准 / 品阶 / 谁守的关）；
  中途退出或刷新的 run 停在 ``finished_at IS NULL``，统计时视为作废；
* ``collection``：角色卡（``best_tier`` 只升不降，``intro_unlocked`` 只解锁不回锁）。

``stage_clear`` 另补一列 ``tier``（老库同样按「缺了才加」处理），记录该关历史最高品阶。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):  # pragma: no cover - 脚本运行路径
    sys.path.insert(0, str(Path(__file__).resolve().parent))

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
DB_PATH: Path = Path(
    os.environ.get("MMQJ_DB_PATH") or (PROJECT_ROOT / "runtime" / "progress.db")
)

DEFAULT_STORY: str = "xiyou"
"""老库与老调用方在缺省情况下归属的故事线。"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    story         TEXT    NOT NULL DEFAULT 'xiyou',
    level_id      TEXT    NOT NULL,
    kp_id         TEXT    NOT NULL,
    user_answer   TEXT    NOT NULL DEFAULT '',
    expected      TEXT    NOT NULL DEFAULT '',
    correct       INTEGER NOT NULL DEFAULT 0,
    verify_source TEXT    NOT NULL DEFAULT 'local',
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS stage_clear (
    level_id   TEXT PRIMARY KEY,
    story      TEXT    NOT NULL DEFAULT 'xiyou',
    cleared_at TEXT    NOT NULL,
    stars      INTEGER NOT NULL DEFAULT 1,
    tier       TEXT
);

CREATE INDEX IF NOT EXISTS idx_attempts_kp ON attempts(kp_id);
CREATE INDEX IF NOT EXISTS idx_attempts_level ON attempts(level_id);

-- 单次挑战（run）：一次「进关 → 抽题 → 全部答对」的完整记录。
-- 计时、品阶、收集都以 run 为最小单位；中途退出的 run 永远不会写 finished_at。
CREATE TABLE IF NOT EXISTS stage_runs (
    run_id         TEXT PRIMARY KEY,
    story          TEXT    NOT NULL,
    level_id       TEXT    NOT NULL,
    seed           TEXT    NOT NULL,
    started_at     TEXT    NOT NULL,
    finished_at    TEXT,
    elapsed_ms     INTEGER NOT NULL DEFAULT 0,
    budget_ms      INTEGER NOT NULL DEFAULT 0,
    question_count INTEGER NOT NULL DEFAULT 0,
    correct_count  INTEGER NOT NULL DEFAULT 0,
    used_hint      INTEGER NOT NULL DEFAULT 0,
    used_skip      INTEGER NOT NULL DEFAULT 0,
    retry_count    INTEGER NOT NULL DEFAULT 0,
    tier           TEXT,
    clean          INTEGER NOT NULL DEFAULT 0,
    boss_id        TEXT,
    guard_id       TEXT,
    scene_variant  TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_story_level ON stage_runs(story, level_id);

-- 角色收集：收卡来自「通关」，介绍解锁来自「干净通关」，两者递进且不回锁。
CREATE TABLE IF NOT EXISTS collection (
    story             TEXT    NOT NULL,
    char_id           TEXT    NOT NULL,
    first_seen_at     TEXT    NOT NULL,
    best_tier         TEXT,
    intro_unlocked    INTEGER NOT NULL DEFAULT 0,
    intro_unlocked_at TEXT,
    PRIMARY KEY (story, char_id)
);
"""

_STORY_TABLES = ("attempts", "stage_clear")


def _now() -> str:
    """当前时间字符串（秒级）。"""
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _table_columns(connection: sqlite3.Connection, table: str) -> List[str]:
    """返回表的列名列表（表不存在时返回空列表）。"""
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return [str(row["name"] if isinstance(row, sqlite3.Row) else row[1]) for row in rows]


def _migrate(connection: sqlite3.Connection, path: Path) -> None:
    """建表 + 给老库补 ``story`` 列（迁移前按需备份）。

    迁移策略（**不删库、不丢历史进度**）：

    1. 表已存在但缺 ``story`` 列时，若表里已有数据，先把整库复制成
       ``progress.db.bak``（同目录）；
    2. ``ALTER TABLE ... ADD COLUMN story TEXT NOT NULL DEFAULT 'xiyou'``，
       老记录自动归属西游记，与改造前行为一致；
    3. 最后补索引。
    """
    existing = {
        str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    needs_backup = False
    for table in _STORY_TABLES:
        if table in existing and "story" not in _table_columns(connection, table):
            count = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            if count:
                needs_backup = True
    if needs_backup:
        connection.commit()
        backup = path.with_name(path.name + ".bak")
        try:
            shutil.copy2(path, backup)
        except OSError:  # pragma: no cover - 备份失败不应阻塞迁移，但要让调用方看见
            print(f"警告：进度库备份失败（{backup}），仍继续补列迁移。", file=sys.stderr)

    connection.executescript(_SCHEMA)
    connection.commit()

    for table in _STORY_TABLES:
        if "story" not in _table_columns(connection, table):
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN story TEXT NOT NULL DEFAULT '{DEFAULT_STORY}'"
            )
    # story 索引必须等列补好之后再建，否则老库会在建索引这一步直接报错。
    connection.execute("CREATE INDEX IF NOT EXISTS idx_attempts_story ON attempts(story)")
    # 品阶列同样按「缺了才补」处理：老库的 stage_clear 没有 tier，补上后默认 NULL（不计品阶）。
    if "tier" not in _table_columns(connection, "stage_clear"):
        connection.execute("ALTER TABLE stage_clear ADD COLUMN tier TEXT")
    connection.commit()


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """打开（并按需初始化 / 迁移）数据库连接。"""
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    _migrate(connection, path)
    return connection


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------


def record_attempt(
    level_id: str,
    kp_id: str,
    user_answer: str,
    expected: str,
    correct: bool,
    verify_source: str = "local",
    story: str = DEFAULT_STORY,
    db_path: Optional[Path] = None,
) -> int:
    """记录一次作答。

    Args:
        level_id: 关卡编号，如 ``"3-2"`` 或 ``"sanguo-3-2"``。
        kp_id: 知识点编号，如 ``"g3_mul_basic"``。
        user_answer: 学生输入的原始答案。
        expected: 参考答案。
        correct: 是否正确。
        verify_source: ``"mcp"`` 或 ``"local"``。
        story: 故事线 id，默认 ``xiyou``。
        db_path: 自定义数据库路径（测试用）。

    Returns:
        新记录的 ``id``。
    """
    with connect(db_path) as connection:
        cursor = connection.execute(
            "INSERT INTO attempts (story, level_id, kp_id, user_answer, expected, correct, verify_source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(story or DEFAULT_STORY),
                str(level_id),
                str(kp_id),
                str(user_answer),
                str(expected),
                1 if correct else 0,
                str(verify_source),
                _now(),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid or 0)


def clear_stage(
    level_id: str,
    accuracy: float,
    story: str = DEFAULT_STORY,
    db_path: Optional[Path] = None,
    tier: Optional[str] = None,
) -> int:
    """标记一个关卡通关，并按正确率给星。

    Args:
        level_id: 关卡标识（本项目用关号字符串，如 ``"3"`` 表示第三关）。
        accuracy: 该关正确率（0~1）。
        story: 故事线 id，默认 ``xiyou``。
        db_path: 自定义数据库路径。
        tier: 本次品阶（``gold`` / ``silver`` / ``bronze`` / ``None``）。
            **只升不降**：重玩拿到的品阶更低时保留历史最高，符合文档 6.5。

    Returns:
        星级 1~3。
    """
    ratio = max(0.0, min(1.0, float(accuracy)))
    stars = 3 if ratio >= 0.9 else (2 if ratio >= 0.7 else 1)
    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT tier FROM stage_clear WHERE level_id = ?", (str(level_id),)
        ).fetchone()
        previous = str(row["tier"]) if row and row["tier"] else None
        kept = _best_tier(previous, tier)
        connection.execute(
            "INSERT INTO stage_clear (level_id, story, cleared_at, stars, tier) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(level_id) DO UPDATE SET story=excluded.story, "
            "cleared_at=excluded.cleared_at, stars=MAX(stage_clear.stars, excluded.stars), "
            "tier=excluded.tier",
            (str(level_id), str(story or DEFAULT_STORY), _now(), stars, kept),
        )
        connection.commit()
    return stars


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def get_wrong_book(
    kp_id: Optional[str] = None,
    story: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """取错题本。

    Args:
        kp_id: 只看某个知识点；``None`` 表示全部。
        story: 只看某条故事线；``None`` 表示全量汇总（每条记录带 ``story`` 字段）。
        db_path: 自定义数据库路径。

    Returns:
        按最近出错时间倒序的错题条目列表。
    """
    sql = (
        "SELECT story, level_id, kp_id, wrong_count, last_time, "
        "user_answer AS last_answer, expected FROM ("
        "  SELECT story, level_id, kp_id, user_answer, expected, created_at,"
        "         COUNT(*) OVER (PARTITION BY story, level_id, kp_id) AS wrong_count,"
        "         MAX(created_at) OVER (PARTITION BY story, level_id, kp_id) AS last_time,"
        "         ROW_NUMBER() OVER (PARTITION BY story, level_id, kp_id ORDER BY id DESC) AS rn"
        "  FROM attempts WHERE correct = 0 "
    )
    params: List[Any] = []
    if story:
        sql += "AND story = ? "
        params.append(str(story))
    if kp_id:
        sql += "AND kp_id = ? "
        params.append(str(kp_id))
    sql += ") WHERE rn = 1 ORDER BY last_time DESC"
    with connect(db_path) as connection:
        rows = connection.execute(sql, params).fetchall()
    return [
        {
            "story": str(row["story"] or DEFAULT_STORY),
            "level_id": str(row["level_id"]),
            "kp_id": str(row["kp_id"]),
            "wrong_count": int(row["wrong_count"]),
            "last_time": str(row["last_time"]),
            "last_answer": str(row["last_answer"]),
            "expected": str(row["expected"]),
        }
        for row in rows
    ]


def get_stage_clears(
    story: Optional[str] = None, db_path: Optional[Path] = None
) -> Dict[str, Dict[str, Any]]:
    """返回 ``{关卡标识: {"cleared_at", "stars", "story"}}``。"""
    sql = "SELECT level_id, story, cleared_at, stars FROM stage_clear"
    params: List[Any] = []
    if story:
        sql += " WHERE story = ?"
        params.append(str(story))
    with connect(db_path) as connection:
        rows = connection.execute(sql, params).fetchall()
    return {
        str(row["level_id"]): {
            "story": str(row["story"] or DEFAULT_STORY),
            "cleared_at": str(row["cleared_at"]),
            "stars": int(row["stars"]),
        }
        for row in rows
    }


def _best_tier(current: Optional[str], candidate: Optional[str]) -> Optional[str]:
    """两个品阶取更优（判定口径与 ``journey.grade_tier`` 同源，不另写一套）。"""
    if not current:
        return candidate
    if not candidate:
        return current
    try:
        import journey

        return journey.best_tier(current, candidate)
    except Exception:  # pragma: no cover - 规则文件坏掉时退化成「以新值为准」
        return candidate


def get_clear_map(
    story: Optional[str] = None, db_path: Optional[Path] = None
) -> Dict[str, Dict[str, Any]]:
    """给解锁判定用的通关表：``{stage_key: {"cleared", "best_tier", "stars", "story"}}``。

    只包含 ``stage_clear`` 里真正存在的记录，所以「有 ``stage_runs`` 记录但没通关」的关
    不会出现在这里——这正是文档 13.1 要求的那种边界。
    """
    sql = "SELECT level_id, story, cleared_at, stars, tier FROM stage_clear"
    params: List[Any] = []
    if story:
        sql += " WHERE story = ?"
        params.append(str(story))
    with connect(db_path) as connection:
        rows = connection.execute(sql, params).fetchall()
    return {
        str(row["level_id"]): {
            "cleared": True,
            "story": str(row["story"] or DEFAULT_STORY),
            "best_tier": (str(row["tier"]) if row["tier"] else None),
            "stars": int(row["stars"]),
            "cleared_at": str(row["cleared_at"]),
        }
        for row in rows
    }


# ---------------------------------------------------------------------------
# 挑战（run）与角色收集
# ---------------------------------------------------------------------------


def create_run(
    run_id: str,
    story: str,
    level_id: str,
    seed: str,
    boss_id: str = "",
    guard_id: str = "",
    scene_variant: str = "",
    db_path: Optional[Path] = None,
) -> None:
    """开一次挑战（只写开场信息，计时与结果等结算时再补）。

    中途退出 / 刷新页面时这条记录会停在「没有 ``finished_at``」的状态，
    统计时视为作废——不会计入品阶，也不会触发收集。
    """
    with connect(db_path) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO stage_runs "
            "(run_id, story, level_id, seed, started_at, boss_id, guard_id, scene_variant) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(run_id),
                str(story),
                str(level_id),
                str(seed),
                _now(),
                str(boss_id),
                str(guard_id),
                str(scene_variant),
            ),
        )
        connection.commit()


def finish_run(
    run_id: str,
    elapsed_ms: int,
    budget_ms: int,
    question_count: int,
    correct_count: int,
    used_hint: bool = False,
    used_skip: bool = False,
    retry_count: int = 0,
    tier: Optional[str] = None,
    clean: bool = False,
    db_path: Optional[Path] = None,
) -> None:
    """结算一次挑战：补齐用时、基准、计数、品阶与是否干净通关。"""
    with connect(db_path) as connection:
        connection.execute(
            "UPDATE stage_runs SET finished_at = ?, elapsed_ms = ?, budget_ms = ?, "
            "question_count = ?, correct_count = ?, used_hint = ?, used_skip = ?, "
            "retry_count = ?, tier = ?, clean = ? WHERE run_id = ?",
            (
                _now(),
                int(elapsed_ms),
                int(budget_ms),
                int(question_count),
                int(correct_count),
                1 if used_hint else 0,
                1 if used_skip else 0,
                int(retry_count),
                (str(tier) if tier else None),
                1 if clean else 0,
                str(run_id),
            ),
        )
        connection.commit()


def get_runs(
    story: Optional[str] = None,
    finished_only: bool = True,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """取挑战记录（默认只取已结算的，按开始时间倒序）。"""
    sql = "SELECT * FROM stage_runs"
    clauses: List[str] = []
    params: List[Any] = []
    if story:
        clauses.append("story = ?")
        params.append(str(story))
    if finished_only:
        clauses.append("finished_at IS NOT NULL")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY started_at DESC, run_id DESC"
    with connect(db_path) as connection:
        rows = connection.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def upsert_collection(
    story: str,
    char_ids: List[str],
    tier: Optional[str],
    clean: bool,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """把出场角色写进角色库，返回**本次新增**的卡片信息。

    规则（文档第七节）：
    * 通关（有品阶）才收卡；跳过没有品阶，不收卡，也不写库；
    * ``best_tier`` 只升不降；
    * ``intro_unlocked`` 一旦为真就不再回锁，只有「干净通关」才置 1。

    Args:
        story: 故事线 id。
        char_ids: 本次出场的角色 id（闯关大将 + 本场守关大将）。
        tier: 本次品阶；``None`` 表示不收卡。
        clean: 是否干净通关。
        db_path: 自定义数据库路径。

    Returns:
        每个角色一条：``{"id", "new", "best_tier", "intro_unlocked", "intro_new"}``。
    """
    if not tier:
        return []
    results: List[Dict[str, Any]] = []
    now = _now()
    with connect(db_path) as connection:
        for char_id in char_ids:
            row = connection.execute(
                "SELECT best_tier, intro_unlocked FROM collection WHERE story = ? AND char_id = ?",
                (str(story), str(char_id)),
            ).fetchone()
            is_new = row is None
            previous_tier = str(row["best_tier"]) if row and row["best_tier"] else None
            intro_before = int(row["intro_unlocked"]) if row else 0
            kept = _best_tier(previous_tier, tier)
            intro_after = 1 if (intro_before or clean) else 0
            if is_new:
                connection.execute(
                    "INSERT INTO collection (story, char_id, first_seen_at, best_tier, "
                    "intro_unlocked, intro_unlocked_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(story),
                        str(char_id),
                        now,
                        kept,
                        intro_after,
                        now if intro_after else None,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE collection SET best_tier = ?, intro_unlocked = ?, "
                    "intro_unlocked_at = COALESCE(intro_unlocked_at, ?) "
                    "WHERE story = ? AND char_id = ?",
                    (
                        kept,
                        intro_after,
                        now if (intro_after and not intro_before) else None,
                        str(story),
                        str(char_id),
                    ),
                )
            results.append(
                {
                    "id": str(char_id),
                    "new": is_new,
                    "best_tier": kept,
                    "intro_unlocked": bool(intro_after),
                    "intro_new": bool(intro_after and not intro_before),
                }
            )
        connection.commit()
    return results


def get_collection(
    story: Optional[str] = None, db_path: Optional[Path] = None
) -> Dict[str, Dict[str, Any]]:
    """取角色收集表：``{char_id: {"best_tier", "intro_unlocked", "first_seen_at", "story"}}``。

    不传 ``story`` 时把各线的记录合在一起（角色 id 本身带线前缀，不会撞车）。
    """
    sql = "SELECT story, char_id, first_seen_at, best_tier, intro_unlocked FROM collection"
    params: List[Any] = []
    if story:
        sql += " WHERE story = ?"
        params.append(str(story))
    with connect(db_path) as connection:
        rows = connection.execute(sql, params).fetchall()
    return {
        str(row["char_id"]): {
            "story": str(row["story"]),
            "best_tier": (str(row["best_tier"]) if row["best_tier"] else None),
            "intro_unlocked": int(row["intro_unlocked"]),
            "first_seen_at": str(row["first_seen_at"]),
        }
        for row in rows
    }


def stage_accuracy(
    stage: int, story: str = DEFAULT_STORY, db_path: Optional[Path] = None
) -> Dict[str, Any]:
    """统计某条故事线里某一关的作答情况。

    ``level_id`` 有两种写法：默认线是 ``3-2``，其他线是 ``sanguo-3-2``，
    因此用「段首匹配 + 中间段匹配」两种 LIKE 覆盖，且始终带上 ``story`` 过滤。
    """
    stage = int(stage)
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT level_id, correct FROM attempts "
            "WHERE story = ? AND (level_id LIKE ? OR level_id LIKE ?)",
            (str(story or DEFAULT_STORY), f"{stage}-%", f"%-{stage}-%"),
        ).fetchall()
    attempted_levels = {str(row["level_id"]) for row in rows}
    correct_levels = {str(row["level_id"]) for row in rows if int(row["correct"]) == 1}
    total = len(rows)
    correct = sum(1 for row in rows if int(row["correct"]) == 1)
    return {
        "stage": stage,
        "story": str(story or DEFAULT_STORY),
        "attempts": total,
        "correct": correct,
        "accuracy": (correct / total) if total else 0.0,
        "attempted_levels": len(attempted_levels),
        "correct_levels": len(correct_levels),
    }


def get_summary(
    story: Optional[str] = None, db_path: Optional[Path] = None
) -> Dict[str, Any]:
    """汇总：总题数、正确率、已通关卡数、薄弱知识点 Top3。

    Args:
        story: 只统计某条故事线；``None`` 表示全量汇总。
    """
    where = " WHERE story = ?" if story else ""
    params: List[Any] = [str(story)] if story else []
    with connect(db_path) as connection:
        total = int(
            connection.execute(f"SELECT COUNT(*) AS c FROM attempts{where}", params).fetchone()["c"]
        )
        correct = int(
            connection.execute(
                f"SELECT COUNT(*) AS c FROM attempts{where}{' AND' if where else ' WHERE'} correct = 1",
                params,
            ).fetchone()["c"]
        )
        cleared = int(
            connection.execute(
                f"SELECT COUNT(*) AS c FROM stage_clear{where}", params
            ).fetchone()["c"]
        )
        weak_rows = connection.execute(
            f"SELECT kp_id, COUNT(*) AS wrong_count FROM attempts{where}"
            f"{' AND' if where else ' WHERE'} correct = 0 "
            "GROUP BY kp_id ORDER BY wrong_count DESC, kp_id ASC LIMIT 3",
            params,
        ).fetchall()
        story_rows = connection.execute(
            "SELECT story, COUNT(*) AS c FROM attempts GROUP BY story ORDER BY story"
        ).fetchall()

    names: Dict[str, str] = {}
    try:  # 知识点中文名仅用于展示，取不到也不影响统计
        import problem_gen

        names = {key: value["name"] for key, value in problem_gen.kp_index().items()}
    except Exception:  # pragma: no cover - 展示用，失败降级
        names = {}

    weak = [
        {
            "kp_id": str(row["kp_id"]),
            "kp_name": names.get(str(row["kp_id"]), str(row["kp_id"])),
            "wrong_count": int(row["wrong_count"]),
        }
        for row in weak_rows
    ]
    return {
        "total_attempts": total,
        "correct_attempts": correct,
        "accuracy": (correct / total) if total else 0.0,
        "cleared_stages": cleared,
        "weak_kps": weak,
        "by_story": {str(row["story"] or DEFAULT_STORY): int(row["c"]) for row in story_rows},
    }


# ---------------------------------------------------------------------------
# 家长周报
# ---------------------------------------------------------------------------


def _story_label(story_id: str) -> str:
    """故事线中文名（展示用，取不到就退化为 id）。"""
    try:
        import problem_gen

        return problem_gen.storyline_name(story_id)
    except Exception:  # pragma: no cover - 展示用，失败降级
        return str(story_id)


def weekly_report(
    days: int = 7, story: Optional[str] = None, db_path: Optional[Path] = None
) -> str:
    """生成家长视角的 Markdown 周报。

    Args:
        days: 统计最近多少天，默认 7 天。
        story: 只统计某条故事线；``None`` 表示全量汇总，并在报告里标注每条线的题量。
        db_path: 自定义数据库路径。

    Returns:
        Markdown 文本。
    """
    since = (datetime.now() - timedelta(days=int(days))).replace(microsecond=0)
    sql = (
        "SELECT story, level_id, kp_id, correct, verify_source, created_at FROM attempts "
        "WHERE created_at >= ?"
    )
    params: List[Any] = [since.isoformat(sep=" ")]
    if story:
        sql += " AND story = ?"
        params.append(str(story))
    sql += " ORDER BY created_at ASC"
    with connect(db_path) as connection:
        rows = connection.execute(sql, params).fetchall()

    total = len(rows)
    correct = sum(1 for row in rows if int(row["correct"]) == 1)
    accuracy = (correct / total * 100) if total else 0.0
    active_days = len({str(row["created_at"])[:10] for row in rows})
    mcp_checks = sum(1 for row in rows if str(row["verify_source"]) == "mcp")

    story_counts: Dict[str, int] = {}
    for row in rows:
        key = str(row["story"] or DEFAULT_STORY)
        story_counts[key] = story_counts.get(key, 0) + 1
    story_text = "、".join(
        f"{_story_label(sid)} {count} 题"
        for sid, count in sorted(story_counts.items(), key=lambda item: (-item[1], item[0]))
    )

    wrong_by_kp: Dict[str, int] = {}
    for row in rows:
        if int(row["correct"]) == 0:
            wrong_by_kp[str(row["kp_id"])] = wrong_by_kp.get(str(row["kp_id"]), 0) + 1
    weak = sorted(wrong_by_kp.items(), key=lambda item: (-item[1], item[0]))[:3]

    names: Dict[str, str] = {}
    try:
        import problem_gen

        names = {key: value["name"] for key, value in problem_gen.kp_index().items()}
    except Exception:  # pragma: no cover - 展示用，失败降级
        names = {}

    lines: List[str] = []
    lines.append(f"# 本周学习周报（最近 {days} 天）")
    lines.append("")
    lines.append(f"统计区间：{since.strftime('%Y-%m-%d')} 至 {datetime.now().strftime('%Y-%m-%d')}")
    lines.append("")
    lines.append("## 一、总体情况")
    lines.append("")
    lines.append(f"- 本周共作答 **{total}** 题，答对 **{correct}** 题，正确率 **{accuracy:.1f}%**。")
    lines.append(f"- 有 **{active_days}** 天进行了练习。")
    if story_text:
        prefix_text = f"统计故事线：{_story_label(str(story))}" if story else "覆盖故事线"
        lines.append(f"- {prefix_text}：{story_text}。")
    if mcp_checks:
        lines.append(f"- 其中 **{mcp_checks}** 题的答案通过麦当劳 MCP 的 calculate-price 做了真实复核。")
    lines.append("")

    lines.append("## 二、需要再看一眼的知识点")
    lines.append("")
    if weak:
        for kp_id, count in weak:
            lines.append(f"- {names.get(kp_id, kp_id)}：错过 {count} 次。建议把这一类题的「先算什么、再算什么」用一句话说清楚。")
    else:
        lines.append("- 本周没有积累错题，说明思路和计算都比较稳。")
    lines.append("")

    lines.append("## 三、下周建议")
    lines.append("")
    if total == 0:
        first_stage = "第一关"
        try:
            import problem_gen

            stages = problem_gen.list_stages(story)
            if stages:
                first_stage = f"第一关「{stages[0]['name']}」"
        except Exception:  # pragma: no cover - 展示用，失败降级
            pass
        lines.append(f"- 先从{first_stage}开始，每天 3 题，重点是把题目里的条件读完整。")
    elif accuracy >= 90:
        lines.append("- 正确率已经很高，可以挑战关卡后半段的多步题，练习把解题过程写清楚。")
    elif accuracy >= 70:
        lines.append("- 保持每天 3-5 题，错题当天重做一遍，重点练「该用哪种运算」的判断。")
    else:
        lines.append("- 把节奏放慢：每题先说出「已知什么、要求什么」，再动笔，错题隔天再做一次。")
    lines.append("")
    lines.append("## 四、给孩子的鼓励")
    lines.append("")
    lines.append("- 坚持把每一步写出来，比一次算对更重要；错了能说出错在哪，就是进步。")
    lines.append("")
    lines.append("> 适量、均衡搭配，不浪费。")
    lines.append("")
    lines.append("> 本报告由「麦麦取经录」在本机生成，非麦当劳官方产品，数据不上传。")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - 手工调试入口
    print(weekly_report())
