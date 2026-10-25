#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦取经录 · WorkBuddy 技能启动器（薄壳，零第三方依赖）。

为什么需要这个文件
------------------
技能包（``skill/``）随仓库分发，也会被复制或软链到
``~/.workbuddy/skills/mcd-math-journey/``。技能被**加载**时，进程的 ``cwd``
未必是仓库根目录——仓库根那份 ``SKILL.md`` 里写的 ``python scripts/cli.py ...``
换个目录跑就会因为找不到文件而失败。

如果为了让命令「在哪都能跑」而把 ``scripts/`` 复制一份进技能包，仓库里就会
出现两份业务代码，改一处忘一处，早晚漂移。所以这里只放一个**薄启动器**：
它唯一的职责是「找到项目根，然后把参数原样转交给 ``scripts/cli.py``」。

因此本文件：

* 不导入项目里的任何模块（那会把 ``sys.path`` 和导入期副作用拖进来）；
* 不含任何数学 / 数据 / 展示逻辑，只做「定位 + 转发 + 透传退出码」；
* 只用标准库，与项目整体风格保持一致。

参数与 stdin / stdout / stderr 全部透传，所以 ``play`` 依然能逐题读用户的答案，
``play`` 未解锁关卡约定的退出码 ``2`` 也不会被吞掉。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

ENV_ROOT: str = "MMQJ_ROOT"
"""用户显式指定项目根的环境变量（优先级最高）。"""

CONFIG_NAME: str = ".mmqj_root"
"""``install.py`` 装到本机时写下的「项目根记录文件」。

装到 ``~/.workbuddy/skills/`` 之后启动器已经不在仓库里了，向上找永远找不到
``scripts/cli.py``；写一个记录文件，用户装完就能直接用，不必先折腾环境变量。
环境变量仍然优先于它——于是「临时指到别的仓库」不需要改文件。
"""

CLI_REL: Tuple[str, ...] = ("scripts", "cli.py")
"""启动器默认转发的目标（唯一的业务代码入口）。"""

DATA_SOURCE_REL: Tuple[str, ...] = ("scripts", "data_source.py")
"""抓取数据用的旁路目标（见 ``ROUTES``）。"""

REQUIRED_MARKERS: Tuple[Path, ...] = (
    Path("scripts/cli.py"),
    Path("data/storylines/index.json"),
)
"""判定「这就是项目根」的两个锚点。

只要 ``scripts/cli.py`` 过于宽松（别人把 scripts 拷走也算），再加一个数据锚点
``data/storylines/index.json``：两个都在，才说明是完整 clone 下来的仓库根。
"""

ROUTES: dict = {
    "data": DATA_SOURCE_REL,
}
"""除 ``cli.py`` 之外的旁路路由：``invoke.py data --fetch`` → ``scripts/data_source.py --fetch``。

只加这一条的理由：``cli.py`` 没有 ``--fetch`` 子命令，抓取入口本来就是独立的
``data_source.py``；把这条也收进启动器，模型就永远不需要知道仓库在哪。
"""

EXIT_NO_ROOT: int = 3
"""找不到项目根的退出码。与 ``cli.py`` 的语义错开：2 表示「关卡未解锁 / 参数错误」。"""

EXIT_LAUNCH_FAILED: int = 4
"""子进程起不来（Python 解释器不可用等）。"""

EXIT_INTERRUPTED: int = 130
"""用户 Ctrl-C 中断（沿用 shell 惯例）。"""


# ---------------------------------------------------------------------------
# 项目根定位
# ---------------------------------------------------------------------------


def _missing_markers(path: Path) -> List[str]:
    """返回 ``path`` 下缺失的锚点（用于报错时说清「差什么」）。

    Args:
        path: 待检查目录。

    Returns:
        缺失锚点的相对路径列表；空列表表示这是个合法的项目根。
    """
    return [str(marker) for marker in REQUIRED_MARKERS if not (path / marker).is_file()]


def _is_project_root(path: Path) -> bool:
    """判断目录是否为麦麦取经录项目根。"""
    return path.is_dir() and not _missing_markers(path)


def _resolve(raw: str) -> Path:
    """把用户给的路径字符串转成绝对路径，解析失败时退化为原样展开。

    Args:
        raw: 路径字符串（可能含 ``~``、相对路径、Windows 盘符）。

    Returns:
        尽量解析后的绝对路径；``resolve()`` 抛错时返回 ``expanduser()`` 的结果。
    """
    candidate = Path(raw).expanduser()
    try:
        return candidate.resolve()
    except OSError:  # pragma: no cover - 极少数损坏路径
        return candidate


def locate_root(here: Optional[Path] = None) -> Tuple[Optional[Path], str]:
    """按固定顺序定位项目根。

    顺序（每一步都可能命中，命中即返回）：

    1. 环境变量 ``MMQJ_ROOT``——显式指定最优先；一旦设置就当作权威，
       指错了直接报错，不再偷偷回落到别处（否则「我明明设了却用的是旧仓库」
       会变成很难查的问题）。
    2. 记录文件 ``.mmqj_root``（``install.py`` 写下；装到本机时的常见路径）。
    3. 从本文件所在目录向上逐级查找，直到找到同时含 ``scripts/cli.py``
       与 ``data/storylines/index.json`` 的目录（仓库内直接使用时的路径）。
    4. 兜底：``skill/`` 是仓库子目录的话，仓库根就是它的上一级。

    Args:
        here: 本文件的解析后路径；``None`` 时用 ``__file__``。

    Returns:
        ``(项目根, 命中方式)``；找不到时项目根为 ``None``，命中方式说明原因
        （``env-broken`` / ``config-broken`` / ``missing``），供调用方组织人话报错。
    """
    here = here or Path(__file__).resolve()

    # 1) 环境变量：显式指定就是权威
    env_raw = (os.environ.get(ENV_ROOT) or "").strip()
    if env_raw:
        candidate = _resolve(env_raw)
        if _is_project_root(candidate):
            return candidate, "env"
        return None, "env-broken"

    # 2) 记录文件（安装到本机时的常见情况）
    config_path = here.parent / CONFIG_NAME
    if config_path.is_file():
        try:
            recorded = config_path.read_text(encoding="utf-8").strip()
        except OSError:  # pragma: no cover - 权限异常
            recorded = ""
        if recorded:
            candidate = _resolve(recorded)
            if _is_project_root(candidate):
                return candidate, "config"
            return None, "config-broken"

    # 3) 向上逐级查找（仓库内直接运行 skill/invoke.py 时命中这一条）
    for parent in here.parents:
        if _is_project_root(parent):
            return parent, "walk"

    # 4) 兜底：本文件在 <repo>/skill/ 下
    fallback = here.parent.parent
    if _is_project_root(fallback):  # pragma: no cover - 与第 3 步重叠，保留作自证
        return fallback, "sibling"

    return None, "missing"


# ---------------------------------------------------------------------------
# 报错：人话，不是 traceback
# ---------------------------------------------------------------------------


def _print_no_root(reason: str, here: Path) -> None:
    """把「找不到项目根」渲染成可执行的指引。

    宁可多写两行提示，也不要抛 traceback：模型拿到 traceback 只能猜，
    拿到「设哪个变量、指到哪个目录」才知道下一步做什么。

    Args:
        reason: ``locate_root`` 返回的原因码。
        here: 启动器自身的解析后路径（报错里写出来便于用户自查）。
    """
    lines: List[str] = [
        "找不到麦麦取经录项目根目录。请先 git clone 仓库，"
        "然后设置环境变量 MMQJ_ROOT 指向仓库根（含 scripts/cli.py 的目录）。",
        "",
        f"  启动器位置：{here}",
    ]
    if reason == "env-broken":
        env_raw = (os.environ.get(ENV_ROOT) or "").strip()
        bad = _resolve(env_raw)
        lines.append(f"  {ENV_ROOT} 当前指向：{bad}")
        missing = _missing_markers(bad)
        if bad.is_dir():
            lines.append(f"  该目录缺少：{'、'.join(missing)}")
        else:
            lines.append("  该目录不存在。")
        lines.append(f"  改成仓库根即可：{ENV_ROOT}=<仓库根>；或删掉 {ENV_ROOT} 让它自动查找。")
    elif reason == "config-broken":
        config_path = here.parent / CONFIG_NAME
        recorded = ""
        try:
            recorded = config_path.read_text(encoding="utf-8").strip()
        except OSError:  # pragma: no cover - 权限异常
            recorded = ""
        lines.append(f"  记录文件：{config_path}" + (f"（指向 {recorded}）" if recorded else ""))
        lines.append("  仓库可能被移动或删除了：把该文件改成新的仓库根，或删掉后重新跑 skill/install.py。")
    else:
        lines.append("  已从启动器所在目录逐级向上查找，没有找到同时含 scripts/cli.py 与 data/storylines/index.json 的目录。")
        lines.append("  如果只是想看一眼它找的是哪：python invoke.py where")

    lines.append("")
    lines.append("  设置方法：")
    lines.append('    Windows PowerShell：setx MMQJ_ROOT "D:\\path\\to\\mcd-math-journey"')
    lines.append("    macOS / Linux：export MMQJ_ROOT=/path/to/mcd-math-journey")
    lines.append("  自查：python <本文件> where")
    print("\n".join(lines), file=sys.stderr)


def _print_broken_target(target: Path) -> None:
    """目标脚本缺失时的人话提示（项目根对了但文件不全）。"""
    print(
        f"项目根已找到，但缺少要执行的脚本：{target}\n"
        "仓库可能没有 clone 完整，请重新 git clone 或 git checkout 恢复 scripts/ 目录。",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# 转发
# ---------------------------------------------------------------------------


def _dispatch(target: Path, args: List[str]) -> int:
    """用当前解释器执行目标脚本，透传参数、标准输入输出与退出码。

    刻意不传 ``stdin`` / ``stdout`` / ``stderr``（默认即「继承父进程」）：
    ``play`` 要逐题读 ``input()``，任何捕获都会让交互失效。
    也刻意不改 ``cwd``：子进程里所有数据路径都由 ``scripts/`` 下的 ``__file__``
    推导，改 ``cwd`` 只会让父进程传入的相对路径语义悄悄漂移。

    Args:
        target: 要执行的脚本绝对路径。
        args: 原样透传的参数列表。

    Returns:
        子进程退出码；起不来时返回 :data:`EXIT_LAUNCH_FAILED`。
    """
    if not target.is_file():
        _print_broken_target(target)
        return EXIT_LAUNCH_FAILED

    python = sys.executable or "python"
    command = [python, str(target), *args]
    try:
        completed = subprocess.run(command)
    except OSError as exc:
        print(
            f"启动失败：无法执行 {python}（{exc}）。请确认 Python 已安装并在 PATH 中。",
            file=sys.stderr,
        )
        return EXIT_LAUNCH_FAILED
    except KeyboardInterrupt:  # pragma: no cover - 交互中断
        print("\n已中断。", file=sys.stderr)
        return EXIT_INTERRUPTED
    return int(completed.returncode)


def _print_where(root: Path, origin: str, here: Path) -> int:
    """打印定位结果（排查「技能到底在用哪个仓库」时的第一手信息）。"""
    origin_label = {
        "env": f"环境变量 {ENV_ROOT}",
        "config": f"记录文件 {here.parent / CONFIG_NAME}",
        "walk": "从启动器目录逐级向上查找",
        "sibling": "skill/ 的上一级目录",
    }.get(origin, origin)
    print(f"项目根：{root}")
    print(f"命中方式：{origin_label}")
    print(f"启动器：{here}")
    for marker in REQUIRED_MARKERS:
        print(f"  锚点 {marker}：{'OK' if (root / marker).is_file() else '缺失'}")
    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    """启动器主入口。

    Args:
        argv: 命令行参数（不含脚本名）；``None`` 时取 ``sys.argv[1:]``。

    Returns:
        进程退出码：透传子进程退出码，定位失败时为 :data:`EXIT_NO_ROOT`。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    here = Path(__file__).resolve()
    root, origin = locate_root(here)

    if args and args[0] in ("where", "--where"):
        if root is None:
            _print_no_root(origin, here)
            return EXIT_NO_ROOT
        return _print_where(root, origin, here)

    if root is None:
        _print_no_root(origin, here)
        return EXIT_NO_ROOT

    route = CLI_REL
    if args and args[0] in ROUTES:
        route = ROUTES[args[0]]
        args = args[1:]

    return _dispatch(root.joinpath(*route), args)


if __name__ == "__main__":  # pragma: no cover - 脚本入口
    raise SystemExit(main())
