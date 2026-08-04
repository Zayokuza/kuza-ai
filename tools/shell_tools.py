import subprocess
import shlex
from pathlib import Path
from utils.logger import warning, confirm as ask_confirm
from utils.config import AGENT_CONFIG

# Commands that always require an explicit warning + confirmation
DANGEROUS_COMMANDS = [
    "rm", "rmdir", "mkfs", "dd", "chmod", "wget", "curl", "mv", "cp",
]

SHELL_METACHARACTERS = (
    "&&", "||", ";", "|", "`", "$(", "${", "<(", ">(", "\n", ">", "<",
)


def _matched_metacharacter(command: str):
    """Return the first shell-control token present in *command*."""
    if "\n" in command:
        return "\n"
    # Treat substitution syntax conservatively even when quoted. It is rare in
    # generated commands and far riskier than requiring an explicit approval.
    for substitution in ("`", "$(", "${", "<(", ">("):
        if substitution in command:
            return substitution
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return "invalid quoting"

    operators = {"&&", "||", ";", "|", ">", ">>", "<", "<<", "&"}
    for token in tokens:
        if token in operators:
            return token
    return None


def validate_command_structure(command: str, allow_compound: bool = False):
    """Validate whether a command can run without invoking a shell parser."""
    if not isinstance(command, str):
        return False, "Command must be a string"
    token = _matched_metacharacter(command)
    if token and not allow_compound:
        return False, f"Shell control token '{token}' requires explicit authorization"
    try:
        shlex.split(command)
    except ValueError as exc:
        return False, f"Invalid shell quoting: {exc}"
    return True, ""

def is_dangerous(command: str) -> bool:
    try:
        cmd_parts = shlex.split(command)
    except ValueError:
        return True
    if not cmd_parts:
        return False

    # Skip leading environment assignments and common command wrappers before
    # checking the executable that will actually run.
    while cmd_parts:
        first = cmd_parts[0]
        if "=" in first and not first.startswith(("/", "./")):
            cmd_parts.pop(0)
            continue
        if Path(first).name in {"env", "command", "nohup"}:
            cmd_parts.pop(0)
            continue
        break
    if not cmd_parts:
        return False

    base_cmd = Path(cmd_parts[0]).name
    if base_cmd in DANGEROUS_COMMANDS:
        return True
    cmd_lower = command.lower()
    dangerous_patterns = [
        "sudo ", "> /dev/", "| sh", "| bash", ":(){:|:&};:",
        # Indirect execution via sh/bash -c
        "sh -c ", "bash -c ",
        # Destructive git operations
        "reset --hard", "push --force", "push -f ",
        # find -delete
        " -delete",
    ]
    return any(p in cmd_lower for p in dangerous_patterns)

def shell(command: str, yolo: bool = False, timeout: int = 1800) -> str:
    """
    Execute a shell command. Returns combined stdout + stderr.

    Simple commands execute as an argv list without a shell parser. Commands
    containing shell control syntax (&&, pipes, redirects, substitutions, etc.)
    and dangerous commands require explicit approval unless yolo=True.

    Args:
        command: The shell command to execute
        yolo: Skip confirmation prompts
        timeout: Command timeout in seconds (default: 30 minutes)

    Returns:
        Command output or error message
    """
    command = str(command or "").strip()
    if not command:
        return "[ERROR] Empty shell command"

    valid, validation_error = validate_command_structure(command, allow_compound=True)
    if not valid:
        return f"[ERROR] {validation_error}"

    shell_token = _matched_metacharacter(command)
    should_confirm = bool(shell_token)

    if is_dangerous(command):
        warning(f"Potentially dangerous command: `{command}`")
        should_confirm = True
    elif AGENT_CONFIG["confirm_shell"] and not yolo:
        should_confirm = True

    if should_confirm and not yolo:
        if not ask_confirm(f"Run shell command: `{command}`?"):
            return "[CANCELLED] User declined to run command."

    try:
        if shell_token:
            # Compound commands require the shell and only reach this point
            # after confirmation (or an explicit yolo execution context).
            run_args = command
            use_shell = True
        else:
            valid, validation_error = validate_command_structure(command)
            if not valid:
                return f"[ERROR] {validation_error}"
            run_args = shlex.split(command)
            use_shell = False

        result = subprocess.run(
            run_args,
            shell=use_shell,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        rendered = output.strip() if output.strip() else "(no output)"
        if result.returncode != 0:
            return f"[ERROR] Command exited with status {result.returncode}\n{rendered}"
        return rendered
    except subprocess.TimeoutExpired:
        return f"[ERROR] Command timed out after {timeout}s"
    except Exception as e:
        return f"[ERROR] {e}"

def search_files(pattern: str, path: str = ".") -> str:
    """Search for files matching pattern. Uses subprocess list args to prevent injection."""
    try:
        root = Path.cwd().resolve()
        search_root = Path(path)
        if not search_root.is_absolute():
            search_root = root / search_root
        search_root = search_root.resolve()
        try:
            search_root.relative_to(root)
        except ValueError:
            return f"[ERROR] Search path is outside the workspace: {search_root}"
        if not search_root.is_dir():
            return f"[ERROR] Search path is not a directory: {search_root}"

        result = subprocess.run(
            ["find", str(search_root), "-name", str(pattern)],
            capture_output=True, text=True, timeout=15
        )
        lines = (result.stdout + result.stderr).strip().splitlines()
        lines = [l for l in lines if l.strip()][:50]
        if result.returncode != 0:
            detail = "\n".join(lines) if lines else "find failed"
            return f"[ERROR] File search exited with status {result.returncode}: {detail}"
        return "\n".join(lines) if lines else "(no matches)"
    except subprocess.TimeoutExpired:
        return "[ERROR] Search timed out"
    except FileNotFoundError:
        return "[ERROR] 'find' command not available"
    except Exception as e:
        return f"[ERROR] {e}"
