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
    stars      INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_attempts_kp ON attempts(kp_id);
CREATE INDEX IF NOT EXISTS idx_attempts_level ON attempts(level_id);
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
) -> int:
    """标记一个关卡通关，并按正确率给星。

    Args:
        level_id: 关卡标识（本项目用关号字符串，如 ``"3"`` 表示第三关）。
        accuracy: 该关正确率（0~1）。
        story: 故事线 id，默认 ``xiyou``。
        db_path: 自定义数据库路径。

    Returns:
        星级 1~3。
    """
    ratio = max(0.0, min(1.0, float(accuracy)))
    stars = 3 if ratio >= 0.9 else (2 if ratio >= 0.7 else 1)
    with connect(db_path) as connection:
        connection.execute(
            "INSERT INTO stage_clear (level_id, story, cleared_at, stars) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(level_id) DO UPDATE SET story=excluded.story, "
            "cleared_at=excluded.cleared_at, stars=MAX(stage_clear.stars, excluded.stars)",
            (str(level_id), str(story or DEFAULT_STORY), _now(), stars),
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
