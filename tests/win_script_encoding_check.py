"""Windows 脚本编码不变式：含非 ASCII 的 .ps1 必须有 UTF-8 BOM。

为什么必须有这个测试：`setup_windows.cmd`（Windows 一键安装入口）调用的是
`powershell.exe`，即 Windows PowerShell 5.1。5.1 对**无 BOM** 的文件按系统 ANSI
代码页解码，而这些脚本里全是 UTF-8 中文，结果是乱码，并且乱码字节会撞坏字符串
字面量，直接变成语法错误——整个一键安装脚本连解析都过不去，用户看到的是
`Unexpected token '缂哄皯'` 之类的报错。标准 Windows 只有 5.1（pwsh 7 不是自带），
所以这不是边缘情况。

这个缺陷极难发现，因为：
- 在 PowerShell 7 下完全正常（7 默认按 UTF-8 读无 BOM 文件）；
- 用编辑器/脚本重写文件时 BOM 会被静默丢弃，没有任何提示；
- mojibake 有时只是显示乱码而不报错（字符串里没有引号字节冲突时），
  于是"能跑"和"文字正确"是两回事。

所以这里按字节断言，而不是"能不能跑起来"。

反向验证（必须做）：删掉任一文件的 BOM，本测试必须变红。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BOM = b"\xef\xbb\xbf"

PASS: list[str] = []
FAIL: list[str] = []


def check(cond: bool, msg: str) -> None:
    (PASS if cond else FAIL).append(msg)
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")


def repo_ps1_files() -> list[Path]:
    """.venv 里的第三方脚本不属于本仓库，必须排除。"""
    out = []
    for path in REPO.rglob("*.ps1"):
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith(".venv/") or "/.venv/" in rel:
            continue
        out.append(path)
    return sorted(out)


def parse_with_windows_powershell(path: Path) -> tuple[bool, str]:
    """用 Windows PowerShell 5.1 真正解析一次；非 Windows 上跳过。

    BOM 不变式是为了 5.1 的正确性，所以只要条件允许就用 5.1 亲自验证，
    而不是只凭"有 BOM 就行"的推断。
    """
    exe = "powershell.exe"
    try:
        subprocess.run([exe, "-NoProfile", "-Command", "exit 0"], capture_output=True, timeout=60)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return True, "跳过：本机没有 Windows PowerShell 5.1"

    script = (
        "$e=$null;$t=$null;"
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$t,[ref]$e);"
        "if($e){$e|ForEach-Object{$_.Message};exit 1}else{exit 0}"
    )
    proc = subprocess.run([exe, "-NoProfile", "-Command", script], capture_output=True, timeout=120)
    if proc.returncode == 0:
        return True, "5.1 解析通过"
    detail = proc.stdout.decode("utf-8", "replace").strip()[:200]
    return False, f"5.1 解析失败：{detail}"


def main() -> None:
    files = repo_ps1_files()
    print(f"== 仓库内 .ps1 文件（已排除 .venv）：{len(files)} 个 ==")
    for path in files:
        print(f"   {path.relative_to(REPO).as_posix()}")

    # 自断言覆盖：找不到文件时不能"零个文件全部合规"地假绿。
    check(len(files) >= 3, f"至少扫到 3 个仓库脚本（实际 {len(files)}）")
    names = {p.name for p in files}
    for required in ("setup_windows.ps1", "start_local.ps1", "smoke_runner.ps1"):
        check(required in names, f"扫到了关键脚本 {required}")

    for path in files:
        rel = path.relative_to(REPO).as_posix()
        raw = path.read_bytes()
        has_bom = raw[:3] == BOM
        non_ascii = any(b > 127 for b in raw)

        if non_ascii:
            check(has_bom, f"{rel}：含非 ASCII，必须带 UTF-8 BOM（5.1 才能正确解码）")
        else:
            # 纯 ASCII 无需 BOM；带 BOM 也无害，但这里不强制，只如实记录。
            check(True, f"{rel}：纯 ASCII，无需 BOM（当前 BOM={has_bom}）")

        # UTF-8 可解码是底线：不可解码说明文件已经被写坏。
        try:
            raw.decode("utf-8")
            check(True, f"{rel}：UTF-8 解码正常")
        except UnicodeDecodeError as exc:
            check(False, f"{rel}：不是合法 UTF-8（{exc}）")

    # 含非 ASCII 且带 BOM 的脚本，再用真实的 5.1 解析一次。
    print("\n== 用 Windows PowerShell 5.1 实际解析 ==")
    for path in files:
        rel = path.relative_to(REPO).as_posix()
        raw = path.read_bytes()
        if not any(b > 127 for b in raw):
            continue
        ok, detail = parse_with_windows_powershell(path)
        check(ok, f"{rel}：{detail}")

    # .sh 必须没有 BOM：BOM 会破坏 shebang，让脚本无法直接执行。
    print("\n== .sh 不得带 BOM（BOM 会破坏 shebang）==")
    sh_files = sorted(
        p
        for p in REPO.rglob("*.sh")
        if not p.relative_to(REPO).as_posix().startswith(".venv/")
    )
    check(len(sh_files) >= 3, f"至少扫到 3 个 shell 脚本（实际 {len(sh_files)}）")
    for path in sh_files:
        rel = path.relative_to(REPO).as_posix()
        raw = path.read_bytes()
        check(raw[:3] != BOM, f"{rel}：不带 BOM")
        if raw.startswith(b"#!"):
            check(
                raw.split(b"\n", 1)[0].startswith(b"#!"),
                f"{rel}：首行仍是 shebang",
            )

    print(f"\n结果: {len(PASS)} 通过, {len(FAIL)} 失败")
    if FAIL:
        print("失败项:")
        for item in FAIL:
            print(f"  - {item}")
        sys.exit(1)


if __name__ == "__main__":
    main()
