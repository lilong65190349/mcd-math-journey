#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「麦麦取经录」安装成 WorkBuddy 技能（复制到 ``~/.workbuddy/skills/``）。

为什么要有这个脚本
------------------
技能目录必须落在 WorkBuddy 的扫描路径下才可能被触发，而仓库里那份 ``skill/``
不在扫描路径里。手工复制容易漏文件、也容易忘了「启动器怎么知道仓库在哪」。

所以这里做两件小事，并且**只做这两件**：

1. 把 ``SKILL.md`` / ``invoke.py`` / ``README.md`` 复制到
   ``~/.workbuddy/skills/mcd-math-journey/``（只碰自己的子目录，不碰别人的技能）；
2. 在装好的目录里写下 ``.mmqj_root`` —— 记录本仓库的位置，让启动器
   ``invoke.py`` 不必问环境变量也能找到 ``scripts/cli.py``。

刻意**不复制** ``scripts/``：业务代码永远只有仓库里那一份，否则两份必然漂移。

脚本是幂等的：重复执行只会覆盖上面那三个文件与本记录文件，不删别的、不报错。
只用标准库。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

SKILL_NAME: str = "mcd-math-journey"
"""安装后的技能目录名（与 SKILL.md frontmatter 的 ``name`` 保持一致）。"""

PAYLOAD: Tuple[str, ...] = ("SKILL.md", "invoke.py", "README.md")
"""需要复制进技能目录的文件（都是给人 / 给模型看的，不含业务代码）。"""

ROOT_RECORD: str = ".mmqj_root"
"""记录仓库位置的隐藏文件；``invoke.py`` 的定位链会读它。"""

REQUIRED_MARKERS: Tuple[Path, ...] = (
    Path("scripts/cli.py"),
    Path("data/storylines/index.json"),
)
"""判定「源目录确实在完整仓库里」的锚点，避免从半截仓库装出去。"""


def _is_repo_root(path: Path) -> bool:
    """判断目录是否为完整仓库根。"""
    return path.is_dir() and all((path / marker).is_file() for marker in REQUIRED_MARKERS)


def _default_dest() -> Path:
    """WorkBuddy 的技能扫描目录下的本项目技能目录。"""
    return Path.home() / ".workbuddy" / "skills" / SKILL_NAME


def _source_dir() -> Path:
    """本脚本所在目录（即仓库里的 ``skill/``）。"""
    return Path(__file__).resolve().parent


def _copy_payload(source: Path, dest: Path) -> List[str]:
    """复制 payload 文件，返回实际写下的文件名。

    Args:
        source: 仓库内的 ``skill/`` 目录。
        dest: 技能目标目录（已创建）。

    Returns:
        写成功的文件名列表。

    Raises:
        OSError: 复制失败（权限、磁盘等），由调用方转成人话提示。
    """
    written: List[str] = []
    for name in PAYLOAD:
        origin = source / name
        if not origin.is_file():
            raise OSError(f"源文件缺失：{origin}")
        shutil.copyfile(origin, dest / name)
        written.append(name)
    return written


def _write_root_record(dest: Path, repo_root: Optional[Path]) -> Optional[Path]:
    """写下仓库位置记录，让装好的启动器知道去哪找 ``scripts/cli.py``。

    Args:
        dest: 技能目录。
        repo_root: 仓库根；``None`` 表示当前这份 ``skill/`` 不在完整仓库里
            （例如用户只拷了 skill 目录），此时不写记录文件。

    Returns:
        写好的记录文件路径；未写时返回 ``None``。
    """
    if repo_root is None:
        return None
    record = dest / ROOT_RECORD
    record.write_text(str(repo_root) + "\n", encoding="utf-8")
    return record


def _verify(dest: Path) -> Tuple[bool, str]:
    """装完立刻自检一次，避免「装完其实用不了」。

    Args:
        dest: 技能目录。

    Returns:
        ``(是否通过, 输出文本)``。
    """
    command = [sys.executable or "python", str(dest / "invoke.py"), "where"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - 环境异常
        return False, f"自检没能执行：{exc}"
    output = (completed.stdout or completed.stderr or "").strip()
    return completed.returncode == 0, output


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器（只有一个可选 ``--dest``，便于测试或多用户环境）。"""
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="把麦麦取经录安装成 WorkBuddy 技能（幂等，可重复执行）。",
    )
    parser.add_argument(
        "--dest",
        default=None,
        help=f"安装目标目录，默认 {_default_dest()}",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """安装主入口。

    Args:
        argv: 命令行参数（不含脚本名）。

    Returns:
        0 安装成功；1 安装失败（已打印原因与建议）。
    """
    args = build_parser().parse_args(argv)
    source = _source_dir()
    dest = Path(args.dest).expanduser().resolve() if args.dest else _default_dest()

    # 源目录在完整仓库里的话，把仓库根记下来；只拷了 skill/ 的话就不写记录。
    repo_root = source.parent if _is_repo_root(source.parent) else None

    print("麦麦取经录 · 技能安装")
    print(f"  源目录：{source}")
    print(f"  目标目录：{dest}")

    try:
        dest.mkdir(parents=True, exist_ok=True)
        written = _copy_payload(source, dest)
        record = _write_root_record(dest, repo_root)
    except OSError as exc:
        print()
        print(f"安装失败：{exc}", file=sys.stderr)
        print(
            "  常见原因：没有写权限、目标路径被占用。\n"
            "  可以换一个目录重试：python install.py --dest <可写目录>",
            file=sys.stderr,
        )
        return 1

    print(f"  已写入：{'、'.join(written)}")
    if record is not None:
        print(f"  仓库记录：{record} → {repo_root}")
    else:
        print(
            "  仓库记录：未写入（当前 skill/ 不在完整仓库里）\n"
            "    需要先设置环境变量 MMQJ_ROOT 指向仓库根，启动器才能找到 scripts/cli.py。"
        )

    ok, output = _verify(dest)
    print()
    if ok:
        print("自检 where：OK")
        for line in output.splitlines():
            print(f"  {line}")
    else:
        print("自检 where：未通过（技能已装上，但暂时跑不起来）")
        for line in output.splitlines():
            print(f"  {line}")
        print("  修法见 skill/README.md 的「找不到项目根」一节。")

    print()
    print("怎么用：在 WorkBuddy 对话里直接说触发词，例如")
    print("  「麦麦取经录，出一道四年级的数学题」")
    print("  「取经数学，我要闯西游记第 1 关」")
    print("  「看看我的错题本」")
    print(f"命不中时先自查技能目录是否就位：{dest / 'SKILL.md'}")
    print("卸载：删掉整个技能目录即可")
    print(f"  {dest}")
    return 0


if __name__ == "__main__":  # pragma: no cover - 脚本入口
    raise SystemExit(main())
