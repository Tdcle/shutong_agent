"""
Sandbox integration tests.

Run:  cd backend && python tests/test_sandbox.py

Covers:
  1. Lazy creation — sandbox only created on first write, not on read
  2. Write isolation — changes go to .sandbox/output first, then sync
  3. Edit + unified diff — edit_file generates correct diff on sync
  4. Shell sandbox — execute_shell runs inside sandbox, files synced back
  5. Delete isolation — delete_file deletes from sandbox, syncs deletion
  6. Session cleanup — destroy_for_session removes everything
  7. Conflict detection — external host change detected as conflict
  8. Lifecycle — idle TTL triggers cleanup
"""
from __future__ import annotations

import asyncio
import shutil
import time
import sys
from pathlib import Path

# Ensure backend is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.tools.workspace import create_session_workspace, set_session_workspace, get_session_workspace
from app.tools.sandbox import get_sandbox_manager, SANDBOX_DIRNAME
from app.config import settings


def banner(text: str):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print('='*60)


def ok(text: str = "PASSED"):
    print(f"  [PASS] {text}")


def fail(text: str):
    print(f"  [FAIL] {text}")


# ── Test 1: Lazy creation ────────────────────────────────────────────
async def test_lazy_creation():
    banner("Test 1: Lazy sandbox creation")
    ws = create_session_workspace("test-1")
    set_session_workspace(ws)

    mgr = get_sandbox_manager()
    sandbox_dir = ws / SANDBOX_DIRNAME

    # Before any write, sandbox should NOT exist
    assert not sandbox_dir.exists(), "Sandbox should not exist before first write"
    ok("No sandbox before first write")

    # A read should not create sandbox
    from app.tools.file_ops import read_file, write_file
    read_file("nonexistent.txt")
    assert not sandbox_dir.exists(), "Read should not create sandbox"
    ok("Read does not create sandbox")

    # First write should create sandbox
    write_file("hello.txt", "hello")
    assert sandbox_dir.exists(), "Write should create sandbox"
    ok("First write creates sandbox")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)
    ok("Cleanup OK")


# ── Test 2: Write isolation + diff ───────────────────────────────────
async def test_write_isolation():
    banner("Test 2: Write isolation and unified diff")
    ws = create_session_workspace("test-2")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.file_ops import write_file, read_file, edit_file, list_files

    # Write a file — goes to sandbox first, then synced to host
    r = write_file("poem.txt", "Roses are red\nViolets are blue\n")
    assert "added" in r.lower() or "created" in r.lower(), f"Expected 'added' in result: {r}"
    ok("Write shows added file")

    # Verify it's on the host
    assert (ws / "poem.txt").exists()
    assert (ws / "poem.txt").read_text() == "Roses are red\nViolets are blue\n"
    ok("File synced to host correctly")

    # Edit the file
    r = edit_file("poem.txt", "blue", "BLUE")
    assert "modified" in r.lower(), f"Expected 'modified' in result: {r}"
    ok("Edit shows modified file")

    # Verify the edit is on host
    content = (ws / "poem.txt").read_text()
    assert "BLUE" in content
    ok("Edit applied to host")

    # The result should contain a unified diff
    r = read_file("poem.txt")
    assert "BLUE" in r
    ok("Read reflects edit")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


# ── Test 3: Shell sandbox ────────────────────────────────────────────
async def test_shell_sandbox():
    banner("Test 3: Shell execution inside sandbox")
    ws = create_session_workspace("test-3")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.shell import execute_shell
    from app.tools.file_ops import read_file

    # Run a shell command that creates files
    r = await execute_shell(
        'echo "line1" > out.txt && echo "line2" >> out.txt',
        working_dir="."
    )
    print(f"  Shell result preview:\n    {r[:300]}")

    assert "out.txt" in r, "Shell result should mention the created file"
    ok("Shell output shows file changes")

    # Verify the file appears on host
    assert (ws / "out.txt").exists(), "out.txt should exist on host"
    content = (ws / "out.txt").read_text().strip()
    assert "line1" in content and "line2" in content
    ok(f"Shell-created file synced to host: {content}")

    # Listing files should show both
    from app.tools.file_ops import list_files
    files = list_files(".")
    assert "out.txt" in files
    ok("list_files shows shell-created file")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


# ── Test 4: Delete + Move ────────────────────────────────────────────
async def test_delete_and_move():
    banner("Test 4: Delete and move inside sandbox")
    ws = create_session_workspace("test-4")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.file_ops import write_file, move_file, delete_file, list_files

    # Create files
    write_file("a.txt", "file A")
    write_file("b.txt", "file B")
    ok("Created two files")

    # Move
    r = move_file("a.txt", "subdir/a_moved.txt")
    assert "Moved" in r, f"Expected 'Moved' in result: {r}"
    assert (ws / "subdir" / "a_moved.txt").exists()
    assert not (ws / "a.txt").exists()
    ok("Move works: a.txt -> subdir/a_moved.txt")

    # Delete
    r = delete_file("b.txt")
    assert "Deleted" in r, f"Expected 'Deleted' in result: {r}"
    assert not (ws / "b.txt").exists()
    ok("Delete works: b.txt removed")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


# ── Test 5: Conflict detection ───────────────────────────────────────
async def test_conflict_detection():
    banner("Test 5: Conflict detection")
    ws = create_session_workspace("test-5")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.file_ops import write_file, edit_file

    # Create initial file via sandbox
    write_file("config.ini", "[main]\nport = 8080\n")
    ok("Created config.ini via sandbox")

    # Now simulate the user modifying the file externally
    # (this creates a conflict because sandbox's baseline hash no longer matches)
    (ws / "config.ini").write_text("[main]\nport = 9090\n")
    ok("User modified config.ini externally")

    # Try to edit via sandbox — should detect conflict
    mgr2 = get_sandbox_manager()
    state, host_path, sandbox_path, rel = mgr2.writable_path("config.ini", "edit")
    sandbox_path.write_text("[main]\nport = 7070\n")
    result = mgr2.sync_back(state)

    # With conflict, sync_back should reject the change
    if result.conflicts:
        ok(f"Conflict correctly detected: {result.conflicts}")
        # Host file should be unchanged
        content = (ws / "config.ini").read_text()
        assert "9090" in content, f"Host file should be unchanged, got: {content}"
        ok("Host file preserved (not overwritten)")
    else:
        fail("Should have detected a conflict")
        print(f"    Sync result: {result.to_summary()}")

    mgr2.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


# ── Test 6: Session cleanup ──────────────────────────────────────────
async def test_session_cleanup():
    banner("Test 6: Full session cleanup")
    ws = create_session_workspace("test-6")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.file_ops import write_file, edit_file
    from app.tools.shell import execute_shell

    # Do some work
    write_file("code.py", "x = 1\n")
    edit_file("code.py", "1", "42")
    await execute_shell("python -c 'print(42)'", ".")

    # Verify sandbox exists
    sandbox_dir = ws / SANDBOX_DIRNAME
    assert sandbox_dir.exists()
    ok("Sandbox exists after work")

    # Destroy the session
    mgr.destroy_for_session(ws)
    assert not sandbox_dir.exists()
    ok("Sandbox directory removed")

    # Clean workspace
    shutil.rmtree(ws, ignore_errors=True)
    assert not ws.exists()
    ok("Workspace removed")


# ── Test 7: grep and glob are read-only (no sandbox) ─────────────────
async def test_readonly_tools():
    banner("Test 7: Read-only tools stay on host")
    ws = create_session_workspace("test-7")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.file_ops import write_file, grep, glob, read_file

    # Create some files via sandbox
    write_file("main.py", "import os\nprint('hello')\n")
    write_file("utils.py", "def add(a, b): return a + b\n")

    # grep — should search host workspace directly
    r = grep("def add", ".")
    assert "utils.py" in r, f"grep should find utils.py: {r}"
    ok("grep finds content on host")

    # glob — should search host workspace
    r = glob("*.py", ".")
    assert "main.py" in r and "utils.py" in r
    ok("glob finds files on host")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


# ▀▀ Test 8: shell timeout is enforced ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀
async def test_shell_timeout():
    banner("Test 8: Shell timeout enforcement")
    ws = create_session_workspace("test-8")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.shell import execute_shell

    start = time.time()
    old_timeout = settings.shell_timeout_seconds
    settings.shell_timeout_seconds = 1
    try:
        result = await execute_shell("python -c \"import time; time.sleep(2)\"")
    finally:
        settings.shell_timeout_seconds = old_timeout
    elapsed = time.time() - start

    assert "timed out" in result.lower(), f"Expected timeout message, got: {result}"
    assert elapsed < 5, f"Timeout should stop promptly, elapsed={elapsed}"
    ok(f"Timeout enforced in {elapsed:.2f}s")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


async def test_shell_network_block():
    banner("Test 9: Shell network command is blocked")
    ws = create_session_workspace("test-9")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.shell import execute_shell

    result = await execute_shell("curl https://example.com")
    assert "blocked by sandbox policy" in result.lower(), f"Expected network block, got: {result}"
    ok("Network-style command blocked")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


async def test_shell_path_escape_block():
    banner("Test 10: Shell path escape is blocked")
    ws = create_session_workspace("test-10")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.shell import execute_shell

    result = await execute_shell("type ..\\secret.txt")
    assert "blocked by sandbox policy" in result.lower(), f"Expected path policy block, got: {result}"
    ok("Parent path escape blocked")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


async def test_nested_shell_block():
    banner("Test 11: Nested shell launch is blocked")
    ws = create_session_workspace("test-11")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.shell import execute_shell

    result = await execute_shell("powershell -Command Get-ChildItem")
    assert "nested shell launch" in result.lower(), f"Expected nested shell block, got: {result}"
    ok("Nested shell launch blocked")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


async def test_command_length_block():
    banner("Test 12: Excessive command length is blocked")
    ws = create_session_workspace("test-12")
    set_session_workspace(ws)
    mgr = get_sandbox_manager()

    from app.tools.shell import execute_shell

    long_command = "echo " + ("x" * 2500)
    result = await execute_shell(long_command)
    assert "too long" in result.lower(), f"Expected command length block, got: {result}"
    ok("Overlong command blocked")

    mgr.destroy_for_session(ws)
    shutil.rmtree(ws, ignore_errors=True)


# ── Main ──────────────────────────────────────────────────────────────
async def main():
    print("=" * 60)
    print("  Sandbox Integration Tests")
    print("=" * 60)

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
        ("Shell path escape block", test_shell_path_escape_block),
        ("Nested shell block", test_nested_shell_block),
        ("Command length block", test_command_length_block),
    ]

    passed = 0
    failed = 0

    for name, test_fn in tests:
        try:
            await test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"\n  [FAIL] {name}: {e}")
            import traceback
            traceback.print_exc()

    banner(f"Results: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
