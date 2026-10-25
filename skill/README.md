# 麦麦取经录 · WorkBuddy 技能包

一句话：把「麦麦取经录」（用麦当劳真实菜单 / 营养 / 价格数据出的中小学数学应用题）装成一个
**可对话触发的 WorkBuddy 技能** —— 说一句「麦麦取经录，出一道四年级的题」就能用。

这个目录里**没有业务代码**，只有三样东西：

| 文件 | 作用 |
| --- | --- |
| `SKILL.md` | 给模型的**操作指南**：接到什么话跑哪条命令、怎么喂输入、怎么回话 |
| `invoke.py` | **薄启动器**：找到仓库根 → 把参数原样转给 `scripts/cli.py`，透传 stdin 与退出码 |
| `install.py` | 一键安装（可选）：把上面两个文件复制到 `~/.workbuddy/skills/mcd-math-journey/` |

数学 / 数据 / 展示逻辑始终只有仓库里 `scripts/` 那一份，**不要复制它**：两份代码必然漂移。

---

## 一、安装（三种方式，选一种）

前提：仓库已经在本地（`git clone` 过，目录里有 `scripts/` 和 `data/`）。

### 方式 A · 一键脚本（推荐）

```bash
python skill/install.py
```

会装到 `~/.workbuddy/skills/mcd-math-journey/`，顺手写下仓库位置记录并自检一次。
幂等：重复跑只覆盖自己这三个文件，不影响别的技能。

想装到别处：`python skill/install.py --dest /path/to/skills/mcd-math-journey`

### 方式 B · 手动复制

```bash
# macOS / Linux
mkdir -p ~/.workbuddy/skills/mcd-math-journey
cp skill/SKILL.md skill/invoke.py skill/README.md ~/.workbuddy/skills/mcd-math-journey/

# Windows PowerShell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.workbuddy\skills\mcd-math-journey" | Out-Null
Copy-Item skill\SKILL.md,skill\invoke.py,skill\README.md "$env:USERPROFILE\.workbuddy\skills\mcd-math-journey\"
```

复制完要设 `MMQJ_ROOT`（见第二节），因为手动复制不会写仓库记录文件。

### 方式 C · 软链（改代码后即时生效，适合开发）

```bash
# macOS / Linux
ln -s "$(pwd)/skill" ~/.workbuddy/skills/mcd-math-journey

# Windows（管理员 PowerShell）
New-Item -ItemType SymbolicLink -Path "$env:USERPROFILE\.workbuddy\skills\mcd-math-journey" -Target "$PWD\skill"
```

软链时 `invoke.py` 就在仓库里，启动器会自己向上找到仓库根，**不需要设环境变量**。

---

## 二、仓库位置：`MMQJ_ROOT`

技能被加载时，命令行进程的 `cwd` 未必是仓库根，所以启动器要自己找仓库。它按顺序找：

1. 环境变量 `MMQJ_ROOT`
2. 安装时写下的 `.mmqj_root` 记录文件（方式 A 会自动写）
3. 从启动器所在目录逐级向上找，直到找到同时含 `scripts/cli.py` 与 `data/storylines/index.json` 的目录
4. `skill/` 的上一级（仓库内直接使用时）

一般情况（方式 A / C）不用管。手动复制、或仓库被移动过，就设一下：

```bash
# Windows PowerShell（永久，需重开窗口）
setx MMQJ_ROOT "D:\path\to\mcd-math-journey"
# Windows 当前窗口临时
$env:MMQJ_ROOT = "D:\path\to\mcd-math-journey"

# macOS / Linux（写进 ~/.zshrc 或 ~/.bashrc 后 source 一次）
export MMQJ_ROOT=/path/to/mcd-math-journey
```

看一眼它到底认了哪个仓库：

```bash
python ~/.workbuddy/skills/mcd-math-journey/invoke.py where
```

---

## 三、验证装好了

1. 在 WorkBuddy 对话里说一句触发词，例如：
   - 「麦麦取经录，出一道四年级的数学题」
   - 「取经数学，我要闯西游记第 1 关」
   - 「看看我的错题本」
2. 或者不开对话，直接命令行验证（不经 WorkBuddy 也能证明它可用）：

```bash
python ~/.workbuddy/skills/mcd-math-journey/invoke.py doctor    # 环境自检
python ~/.workbuddy/skills/mcd-math-journey/invoke.py stages    # 五条故事线关卡总览
```

`doctor` 能打出「Python 版本 / 令牌 / 数据级别」就说明启动器找到了仓库、业务代码跑通了。

---

## 四、卸载

```bash
# 删掉技能目录即可（进度数据库在仓库的 runtime/ 下，不受影响）
rm -rf ~/.workbuddy/skills/mcd-math-journey
```

Windows：`Remove-Item -Recurse -Force "$env:USERPROFILE\.workbuddy\skills\mcd-math-journey"`

软链方式则删链接本身即可（`rm ~/.workbuddy/skills/mcd-math-journey`，不要加 `-rf` 顺着删）。

---

## 五、常见问题

**Q：命令报「找不到麦麦取经录项目根目录」？**
启动器没找到仓库。跑一次 `python skill/install.py` 重写记录文件，或按第二节设 `MMQJ_ROOT`。
退出码是 `3`（与「关卡未解锁」的 `2` 区分开）。

**Q：`play` 跑完只出了题就退出了，没等我回答？**
正常。`play` 从标准输入读答案；在对话 / 脚本里非交互执行时读完题面就 EOF 退出，**这正是「只出题不泄露答案」的用法**。
要作答就自己开终端跑同一条命令（记得带同一个 `--seed` 复现同一道题），或用管道喂答案。

**Q：为什么不在技能包里放一份 `scripts/`？**
两份业务代码必然漂移。技能包只放启动器，业务代码永远回仓库那一份执行。

**Q：出题的数字是实时的吗？**
看数据表标题后缀：`（实时数据）` / `（缓存数据 2026-10-25）` / `（示例数据）`。没配 `MCD_MCP_TOKEN`
时会降级到缓存或示例快照，`invoke.py doctor` 里能看到当前级别。

**Q：这个技能能下单 / 点餐吗？**
不能，也不该能。只读教学工具，工具白名单见仓库根 `SKILL.md` 第二节，写操作工具在代码层就被禁止。

---

## 六、合规

非麦当劳官方产品，输出仅供参考，餐品信息与价格以麦当劳官方渠道实时结果为准。
不采集儿童个人信息，不下单不代付，不提供医疗 / 营养建议。完整约束见仓库根 `SKILL.md`。
