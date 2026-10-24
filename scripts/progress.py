"""麦麦取经录 —— 本地进度与错题本（标准库 sqlite3）。

数据库文件：``runtime/progress.db``（首次使用时自动建目录、自动建表）。
可用环境变量 ``MMQJ_DB_PATH`` 指定到别处——``scripts/selfcheck.py`` 就是靠它
在一份临时库上复跑文档示例，不会碰用户自己的进度。
不联网、不上传，所有记录只存在本机；不采集任何儿童个人信息
（没有姓名、手机号、学校、生日等字段，只有题号与答题结果）。
"""

from __future__ import annotations

import os
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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
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
    cleared_at TEXT NOT NULL,
    stars      INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_attempts_kp ON attempts(kp_id);
CREATE INDEX IF NOT EXISTS idx_attempts_level ON attempts(level_id);
"""


def _now() -> str:
    """当前时间字符串（秒级）。"""
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """打开（并按需初始化）数据库连接。"""
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.executescript(_SCHEMA)
    connection.commit()
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
    db_path: Optional[Path] = None,
) -> int:
    """记录一次作答。

    Args:
        level_id: 关卡编号，如 ``"3-2"``。
        kp_id: 知识点编号，如 ``"g3_mul_basic"``。
        user_answer: 学生输入的原始答案。
        expected: 参考答案。
        correct: 是否正确。
        verify_source: ``"mcp"`` 或 ``"local"``。
        db_path: 自定义数据库路径（测试用）。

    Returns:
        新记录的 ``id``。
    """
    with connect(db_path) as connection:
        cursor = connection.execute(
            "INSERT INTO attempts (level_id, kp_id, user_answer, expected, correct, verify_source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(level_id), str(kp_id), str(user_answer), str(expected), 1 if correct else 0, str(verify_source), _now()),
        )
        connection.commit()
        return int(cursor.lastrowid or 0)


def clear_stage(level_id: str, accuracy: float, db_path: Optional[Path] = None) -> int:
    """标记一个关卡通关，并按正确率给星。

    Args:
        level_id: 关卡标识（本项目用关号字符串，如 ``"3"`` 表示第三关）。
        accuracy: 该关正确率（0~1）。
        db_path: 自定义数据库路径。

    Returns:
        星级 1~3。
    """
    ratio = max(0.0, min(1.0, float(accuracy)))
    stars = 3 if ratio >= 0.9 else (2 if ratio >= 0.7 else 1)
    with connect(db_path) as connection:
        connection.execute(
            "INSERT INTO stage_clear (level_id, cleared_at, stars) VALUES (?, ?, ?) "
            "ON CONFLICT(level_id) DO UPDATE SET cleared_at=excluded.cleared_at, "
            "stars=MAX(stage_clear.stars, excluded.stars)",
            (str(level_id), _now(), stars),
        )
        connection.commit()
    return stars


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def get_wrong_book(kp_id: Optional[str] = None, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """取错题本。

    Args:
        kp_id: 只看某个知识点；``None`` 表示全部。
        db_path: 自定义数据库路径。

    Returns:
        按最近出错时间倒序的错题条目列表。
    """
    sql = (
        "SELECT level_id, kp_id, wrong_count, last_time, user_answer AS last_answer, expected FROM ("
        "  SELECT level_id, kp_id, user_answer, expected, created_at,"
        "         COUNT(*) OVER (PARTITION BY level_id, kp_id) AS wrong_count,"
        "         MAX(created_at) OVER (PARTITION BY level_id, kp_id) AS last_time,"
        "         ROW_NUMBER() OVER (PARTITION BY level_id, kp_id ORDER BY id DESC) AS rn"
        "  FROM attempts WHERE correct = 0 "
    )
    params: List[Any] = []
    if kp_id:
        sql += "AND kp_id = ? "
        params.append(str(kp_id))
    sql += ") WHERE rn = 1 ORDER BY last_time DESC"
    with connect(db_path) as connection:
        rows = connection.execute(sql, params).fetchall()
    return [
        {
            "level_id": str(row["level_id"]),
            "kp_id": str(row["kp_id"]),
            "wrong_count": int(row["wrong_count"]),
            "last_time": str(row["last_time"]),
            "last_answer": str(row["last_answer"]),
            "expected": str(row["expected"]),
        }
        for row in rows
    ]


def get_stage_clears(db_path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """返回 ``{关卡标识: {"cleared_at", "stars"}}``。"""
    with connect(db_path) as connection:
        rows = connection.execute("SELECT level_id, cleared_at, stars FROM stage_clear").fetchall()
    return {
        str(row["level_id"]): {"cleared_at": str(row["cleared_at"]), "stars": int(row["stars"])}
        for row in rows
    }


def stage_accuracy(stage: int, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """统计某一关的作答情况。"""
    prefix = f"{int(stage)}-"
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT level_id, correct FROM attempts WHERE level_id LIKE ?", (prefix + "%",)
        ).fetchall()
    attempted_levels = {str(row["level_id"]) for row in rows}
    correct_levels = {str(row["level_id"]) for row in rows if int(row["correct"]) == 1}
    total = len(rows)
    correct = sum(1 for row in rows if int(row["correct"]) == 1)
    return {
        "stage": int(stage),
        "attempts": total,
        "correct": correct,
        "accuracy": (correct / total) if total else 0.0,
        "attempted_levels": len(attempted_levels),
        "correct_levels": len(correct_levels),
    }


def get_summary(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """汇总：总题数、正确率、已通关卡数、薄弱知识点 Top3。"""
    with connect(db_path) as connection:
        total = int(connection.execute("SELECT COUNT(*) AS c FROM attempts").fetchone()["c"])
        correct = int(
            connection.execute("SELECT COUNT(*) AS c FROM attempts WHERE correct = 1").fetchone()["c"]
        )
        cleared = int(connection.execute("SELECT COUNT(*) AS c FROM stage_clear").fetchone()["c"])
        weak_rows = connection.execute(
            "SELECT kp_id, COUNT(*) AS wrong_count FROM attempts WHERE correct = 0 "
            "GROUP BY kp_id ORDER BY wrong_count DESC, kp_id ASC LIMIT 3"
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
    }


# ---------------------------------------------------------------------------
# 家长周报
# ---------------------------------------------------------------------------


def weekly_report(days: int = 7, db_path: Optional[Path] = None) -> str:
    """生成家长视角的 Markdown 周报。

    Args:
        days: 统计最近多少天，默认 7 天。
        db_path: 自定义数据库路径。

    Returns:
        Markdown 文本。
    """
    since = (datetime.now() - timedelta(days=int(days))).replace(microsecond=0)
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT level_id, kp_id, correct, verify_source, created_at FROM attempts "
            "WHERE created_at >= ? ORDER BY created_at ASC",
            (since.isoformat(sep=" "),),
        ).fetchall()

    total = len(rows)
    correct = sum(1 for row in rows if int(row["correct"]) == 1)
    accuracy = (correct / total * 100) if total else 0.0
    active_days = len({str(row["created_at"])[:10] for row in rows})
    mcp_checks = sum(1 for row in rows if str(row["verify_source"]) == "mcp")

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
        lines.append("- 先从第一关「五行山下」开始，每天 3 题，重点是把题目里的条件读完整。")
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
