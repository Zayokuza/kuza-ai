"""
patch_file — surgical find/replace in files.
Much more efficient than write_file for small edits.
"""
from pathlib import Path
from utils.logger import success
from utils.config import AGENT_CONFIG

def tool_patch_file(path: str, old_str: str, new_str: str) -> str:
    """
    Replace first occurrence of old_str with new_str in file.
    Snapshots before patching for /undo support.
    """
    # Validate the path before checking existence or reading content. This
    # prevents failed patches from becoming an out-of-workspace read primitive.
    from core.filesystem import get_filesystem, FilesystemAccessError
    fs = get_filesystem(
        allow_self_modification=AGENT_CONFIG.get("allow_self_modification", False)
    )
    try:
        p = fs.validate_path(path)
    except FilesystemAccessError as e:
        return f"[ERROR] {e}"

    if not p.exists():
        return f"[ERROR] File not found: {path}"

    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"[ERROR] Could not read {path}: {e}"

    count = content.count(old_str)
    if count == 0:
        lines = content.splitlines()
        return (
            f"[ERROR] String not found. [PATCH_FAILED] old_str not found in {path} ({len(lines)} lines).\n"
            "The file may have changed since you last read it. "
            "Use read_file, then issue a corrected patch."
        )
    
    if count > 1:
        lines = content.splitlines()
        return (
            f"[ERROR] String found {count} times in {path}. "
            f"Provide more context in 'old_str' to make it unique.\n"
            f"File has {len(lines)} lines."
        )

    # Show diff preview
    new_content = content.replace(old_str, new_str, 1)

    # Pre-patch syntax check for Python files: reject patches that break syntax
    if p.suffix == '.py':
        try:
            from core.linter import check_syntax
            syn_err = check_syntax(new_content, str(p))
            if syn_err:
                return (
                    f"[ERROR] Patch would introduce syntax error: {syn_err}\n"
                    "Fix the syntax in your patch and try again. "
                    "Consider using write_file to replace the entire file instead."
                )
        except Exception:
            pass  # linter unavailable — allow patch

    try:
        # Route through the same validated Filesystem instance so self-mod
        # configuration and checkpoint behavior cannot depend on import order.
        fs.write(str(p), new_content, action_kind="patch")
        return f"Patched {path} ({len(old_str)} chars → {len(new_str)} chars)"
    except FilesystemAccessError as e:
        return f"[ERROR] {e}"
    except Exception as e:
        return f"[ERROR] Could not write {path}: {e}"
