"""
Sandbox integration tests.

Run: cd backend && python tests/test_sandbox.py
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.tools.permissions import PathRule, PathCapability, PermissionBroker, PermissionLevel, set_current_permission_broker
from app.tools.sandbox import SANDBOX_DIRNAME, get_sandbox_manager
from app.tools.workspace import create_session_workspace, set_session_workspace


def banner(text: str):
    print(f"\n{'=' * 60}")
    print(f"  {text}")
    print("=" * 60)


def ok(text: str = "PASSED"):
    print(f"  [PASS] {text}")


async def test_lazy_creation():
    banner("Test 1: Lazy sandbox creation")
    ws = create_session_workspace("test-1")
    set_session_workspace(ws)

    mgr = get_sandbox_manager()
    sandbox_dir = ws / SANDBOX_DIRNAME

    from app.tools.file_ops import read_file, write_file

    assert not sandbox_dir.exists()
    ok("No sandbox before first write")

    read_file("nonexistent.txt")
    assert not sandbox_dir.exists()
    ok("Read does not create sandbox")

    write_file("hello.txt", "hello")
    assert sandbox_dir.exists()
    ok("First write creates sandbox")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


async def test_write_isolation():
    banner("Test 2: Write isolation and unified diff")
    ws = create_session_workspace("test-2")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.file_ops import write_file, edit_file, read_file

    result = write_file("poem.txt", "Roses are red\nViolets are blue\n")
    assert "created" in result.lower()
    ok("Write shows added file")

    assert (ws / "poem.txt").read_text() == "Roses are red\nViolets are blue\n"
    ok("File synced to host correctly")

    result = edit_file("poem.txt", "blue", "BLUE")
    assert "modified" in result.lower()
    ok("Edit shows modified file")

    assert "BLUE" in (ws / "poem.txt").read_text()
    ok("Edit applied to host")

    assert "BLUE" in read_file("poem.txt")
    ok("Read reflects edit")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


async def test_shell_sandbox():
    banner("Test 3: Shell execution inside sandbox")
    ws = create_session_workspace("test-3")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.shell import execute_bash
    from app.tools.file_ops import list_files

    result = await execute_bash('echo "line1" > out.txt && echo "line2" >> out.txt', ".")
    assert "out.txt" in result
    ok("Shell output shows file changes")

    assert (ws / "out.txt").exists()
    content = (ws / "out.txt").read_text()
    assert "line1" in content and "line2" in content
    ok("Shell-created file synced to host")

    assert "out.txt" in list_files(".")
    ok("list_files shows shell-created file")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


async def test_delete_and_move():
    banner("Test 4: Delete and move inside sandbox")
    ws = create_session_workspace("test-4")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.file_ops import write_file, move_file, delete_file

    write_file("a.txt", "file A")
    write_file("b.txt", "file B")
    ok("Created two files")

    result = move_file("a.txt", "subdir/a_moved.txt")
    assert "moved" in result.lower()
    assert (ws / "subdir" / "a_moved.txt").exists()
    assert not (ws / "a.txt").exists()
    ok("Move works inside workspace")

    result = delete_file("b.txt")
    assert "deleted" in result.lower()
    assert not (ws / "b.txt").exists()
    ok("Delete works inside workspace")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


async def test_conflict_detection():
    banner("Test 5: Conflict detection")
    ws = create_session_workspace("test-5")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.file_ops import write_file

    write_file("config.ini", "[main]\nport = 8080\n")
    ok("Created config.ini via sandbox")

    (ws / "config.ini").write_text("[main]\nport = 9090\n")
    ok("User modified config.ini externally")

    state, _, sandbox_path, _ = mgr.writable_path("config.ini", "edit")
    sandbox_path.write_text("[main]\nport = 7070\n")
    result = mgr.sync_back(state)
    assert result.conflicts
    ok("Conflict correctly detected")

    assert "9090" in (ws / "config.ini").read_text()
    ok("Host file preserved")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


async def test_session_cleanup():
    banner("Test 6: Full session cleanup")
    ws = create_session_workspace("test-6")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.file_ops import write_file, edit_file
    from app.tools.shell import execute_bash

    write_file("code.py", "x = 1\n")
    edit_file("code.py", "1", "42")
    await execute_bash("python -c 'print(42)'", ".")

    sandbox_dir = ws / SANDBOX_DIRNAME
    assert sandbox_dir.exists()
    ok("Sandbox exists after work")

    mgr.destroy_for_session(ws)
    assert not sandbox_dir.exists()
    ok("Sandbox directory removed")

    shutil.rmtree(ws, ignore_errors=True)
    assert not ws.exists()
    ok("Workspace removed")


async def test_readonly_tools():
    banner("Test 7: Read-only tools stay on host")
    ws = create_session_workspace("test-7")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.file_ops import write_file, grep, glob

    write_file("main.py", "import os\nprint('hello')\n")
    write_file("utils.py", "def add(a, b): return a + b\n")

    assert "utils.py" in grep("def add", ".")
    ok("grep finds content on host")

    result = glob("*.py", ".")
    assert "main.py" in result and "utils.py" in result
    ok("glob finds files on host")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


async def test_shell_timeout():
    banner("Test 8: Shell timeout enforcement")
    ws = create_session_workspace("test-8")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.shell import execute_bash

    old_timeout = settings.shell_timeout_seconds
    settings.shell_timeout_seconds = 1
    try:
        start = time.time()
        result = await execute_bash("python -c \"import time; time.sleep(2)\"")
        elapsed = time.time() - start
    finally:
        settings.shell_timeout_seconds = old_timeout

    assert "timed out" in result.lower()
    assert elapsed < 5
    ok("Timeout enforced")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


async def test_shell_network_block():
    banner("Test 9: Shell network command is blocked")
    ws = create_session_workspace("test-9")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.shell import execute_bash

    result = await execute_bash("curl https://example.com")
    assert "blocked by sandbox policy" in result.lower()
    ok("Network-style command blocked")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


async def test_shell_external_batch_block():
    banner("Test 10: External wildcard delete is redirected")
    ws = create_session_workspace("test-10")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.shell import execute_bash

    result = await execute_bash(r'del C:\Users\26422\Desktop\*.txt')
    assert "delete_paths" in result
    ok("External wildcard delete blocked with structured guidance")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


async def test_nested_shell_block():
    banner("Test 11: Nested shell launch is blocked")
    ws = create_session_workspace("test-11")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.shell import execute_bash

    result = await execute_bash("powershell -Command Get-ChildItem")
    assert "nested shell launch" in result.lower()
    ok("Nested shell launch blocked")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


async def test_command_length_block():
    banner("Test 12: Excessive command length is blocked")
    ws = create_session_workspace("test-12")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.shell import execute_bash

    result = await execute_bash("echo " + ("x" * 2500))
    assert "too long" in result.lower()
    ok("Overlong command blocked")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


async def test_capability_rules_for_external_paths():
    banner("Test 13: Capability rules cover external path tools")
    ws = create_session_workspace("test-13")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.file_ops import move_file, delete_file

    desktop = ws.parent / "external-target"
    desktop.mkdir(parents=True, exist_ok=True)
    external_file = desktop / "sample.txt"
    external_file.write_text("hello", encoding="utf-8")

    broker = PermissionBroker()
    set_current_permission_broker(broker)
    try:
        assert "Path rejected" in delete_file(str(external_file))
        ok("External delete rejected without approval")
        broker._path_rules.add(PathRule(PathCapability.DELETE, str(external_file.parent)))
        result = delete_file(str(external_file))
        assert "deleted external path" in result.lower()
        assert not external_file.exists()
        ok("External delete allowed by capability rule")

        (ws / "move-me.txt").write_text("move", encoding="utf-8")
        assert "Path rejected" in move_file("move-me.txt", str(desktop / "move-me.txt"))
        broker._path_rules.add(PathRule(PathCapability.MOVE, str(desktop)))
        result = move_file("move-me.txt", str(desktop / "move-me.txt"))
        assert "explicitly approved" in result.lower()
        assert (desktop / "move-me.txt").exists()
        ok("External move allowed by capability rule")
    finally:
        set_current_permission_broker(None)

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)
    shutil.rmtree(desktop, ignore_errors=True)


async def test_shell_disallowed_token_matching_is_not_overbroad():
    banner("Test 20: shell disallowed token matching is token-aware")
    ws = create_session_workspace("test-20")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.shell import execute_bash

    result = await execute_bash('for ($i=1; $i -le 2; $i++) { "$i" | Out-File -Encoding utf8 -FilePath "$i.txt" }')
    assert "contains disallowed token 'at'" not in result.lower()
    ok("Safe commands containing path/filepath text are not blocked by short token matches")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


async def test_shell_nonzero_exit_is_reported_as_failure():
    banner("Test 21: shell nonzero exit is surfaced as failure")
    ws = create_session_workspace("test-21")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.shell import execute_bash

    result = await execute_bash('python -c "import sys; sys.exit(3)"')
    assert result.lower().startswith("bash execution failed:")
    assert "exit code: 3" in result.lower()
    ok("Nonzero shell exit is returned as a failure summary")


async def test_move_paths_uses_exact_path_set():
    banner("Test 23: move_paths only moves the explicit closed set")
    ws = create_session_workspace("test-23")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.file_ops import move_paths

    desktop = ws.parent / "desktop-like"
    target = ws.parent / "download-like"
    desktop.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    exact_paths = []
    for i in range(1, 11):
        path = desktop / f"{i}.txt"
        path.write_text(str(i), encoding="utf-8")
        exact_paths.append(str(path))
    (desktop / "历史版本.txt").write_text("old", encoding="utf-8")
    (desktop / "新建 文本文档.txt").write_text("new", encoding="utf-8")

    broker = PermissionBroker()
    broker._path_rules.add(PathRule(PathCapability.MOVE, str(desktop)))
    broker._path_rules.add(PathRule(PathCapability.MOVE, str(target)))
    set_current_permission_broker(broker)
    try:
        result = move_paths(exact_paths, str(target))
        assert "moved 10 paths" in result.lower()
        for i in range(1, 11):
            assert (target / f"{i}.txt").exists()
            assert not (desktop / f"{i}.txt").exists()
        assert (desktop / "历史版本.txt").exists()
        assert (desktop / "新建 文本文档.txt").exists()
        ok("Exact path move leaves unrelated txt files untouched")
    finally:
        set_current_permission_broker(None)

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)
    shutil.rmtree(desktop, ignore_errors=True)
    shutil.rmtree(target, ignore_errors=True)


async def test_copy_file_single():
    banner("Test 17: copy_file copies a single file")
    ws = create_session_workspace("test-17")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.file_ops import copy_file

    (ws / "source.txt").write_text("hello world", encoding="utf-8")
    dest = ws / "subdir" / "copied.txt"

    broker = PermissionBroker()
    broker._path_rules.add(PathRule(PathCapability.WRITE, str(ws)))
    set_current_permission_broker(broker)
    try:
        result = copy_file(str(ws / "source.txt"), str(dest))
        assert "Copied" in result
        assert dest.exists()
        assert dest.read_text(encoding="utf-8") == "hello world"
        assert (ws / "source.txt").exists()
        ok("copy_file copies inside workspace and preserves source")
    finally:
        set_current_permission_broker(None)

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


async def test_copy_file_directory():
    banner("Test 18: copy_file copies a directory tree")
    ws = create_session_workspace("test-18")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.file_ops import copy_file

    src_dir = ws / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "a.txt").write_text("a", encoding="utf-8")
    (src_dir / "sub").mkdir(parents=True, exist_ok=True)
    (src_dir / "sub" / "b.txt").write_text("b", encoding="utf-8")

    broker = PermissionBroker()
    broker._path_rules.add(PathRule(PathCapability.WRITE, str(ws)))
    set_current_permission_broker(broker)
    try:
        result = copy_file(str(src_dir), str(ws / "dest"))
        assert "Copied" in result
        assert (ws / "dest" / "a.txt").read_text(encoding="utf-8") == "a"
        assert (ws / "dest" / "sub" / "b.txt").read_text(encoding="utf-8") == "b"
        ok("copy_file recursively copies directory structure")
    finally:
        set_current_permission_broker(None)

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


async def test_copy_paths_uses_exact_path_set():
    banner("Test 19: copy_paths only copies the explicit closed set")
    ws = create_session_workspace("test-19")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.file_ops import copy_paths

    desktop = ws / "desktop-like"
    target = ws / "backup"
    desktop.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    exact_paths = []
    for i in range(1, 11):
        path = desktop / f"{i}.txt"
        path.write_text(str(i), encoding="utf-8")
        exact_paths.append(str(path))
    (desktop / "keep.log").write_text("keep", encoding="utf-8")

    broker = PermissionBroker()
    broker._path_rules.add(PathRule(PathCapability.WRITE, str(ws)))
    set_current_permission_broker(broker)
    try:
        result = copy_paths(exact_paths, str(target))
        assert "Copied 10 paths" in result
        for i in range(1, 11):
            assert (target / f"{i}.txt").exists()
            assert (target / f"{i}.txt").read_text(encoding="utf-8") == str(i)
        assert not (target / "keep.log").exists()
        for i in range(1, 11):
            assert (desktop / f"{i}.txt").exists()
        ok("copy_paths only copies the explicitly listed paths")
    finally:
        set_current_permission_broker(None)

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


async def test_execute_python_can_generate_multiple_files():
    banner("Test 20: execute_python can generate multiple files")
    ws = create_session_workspace("test-25")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.shell import execute_python

    code = """
from pathlib import Path

base = Path('.')
for i in range(1, 11):
    (base / f"{i}.txt").write_text(str(i), encoding="utf-8")
print("created 10 files")
"""
    result = await execute_python(code, ".")
    assert "created 10 files" in result.lower()
    for i in range(1, 11):
        assert (ws / f"{i}.txt").read_text(encoding="utf-8") == str(i)
    ok("Python runtime creates numbered files without shell quoting issues")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


async def main():
    tests = [
        ("Lazy creation", test_lazy_creation),
        ("Write isolation & diff", test_write_isolation),
        ("Shell sandbox execution", test_shell_sandbox),
        ("Delete & move", test_delete_and_move),
        ("Conflict detection", test_conflict_detection),
        ("Session cleanup", test_session_cleanup),
        ("Read-only tools", test_readonly_tools),
        ("Shell timeout", test_shell_timeout),
        ("Shell network block", test_shell_network_block),
        ("Shell external batch block", test_shell_external_batch_block),
        ("Nested shell block", test_nested_shell_block),
        ("Command length block", test_command_length_block),
        ("Capability rules for external paths", test_capability_rules_for_external_paths),
        ("Shell token matching", test_shell_disallowed_token_matching_is_not_overbroad),
        ("Shell nonzero exit failure", test_shell_nonzero_exit_is_reported_as_failure),
        ("Exact path move set", test_move_paths_uses_exact_path_set),
        ("Copy single file", test_copy_file_single),
        ("Copy directory", test_copy_file_directory),
        ("Copy exact path set", test_copy_paths_uses_exact_path_set),
        ("Execute python batch file generation", test_execute_python_can_generate_multiple_files),
    ]

    passed = 0
    failed = 0
    for name, test_fn in tests:
        try:
            set_current_permission_broker(None)
            await test_fn()
            passed += 1
        except Exception as exc:
            failed += 1
            print(f"\n  [FAIL] {name}: {exc}")
            import traceback
            traceback.print_exc()

    banner(f"Results: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
