import re
import shutil
import subprocess


EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$"
)

ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")


def _clean_holehe_output(output: str, email: str) -> str:
    output = ANSI_PATTERN.sub("", output)

    found = []
    rate_limited = []

    for raw_line in output.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("[+]"):
            item = line[3:].strip()
            if item.startswith("Email used"):
                continue
            found.append(item)

        elif line.startswith("[x]"):
            item = line[3:].strip()
            if item.startswith("Rate limit"):
                continue
            rate_limited.append(item)

    lines = [f"Email checked: {email}", ""]

    if found:
        lines.append(f"Registered accounts found: {len(found)}")
        lines.extend(f"✓ {site}" for site in found)
    else:
        lines.append("No supported websites confirmed this email as registered.")

    if rate_limited:
        lines.append("")
        lines.append(f"Rate-limited checks: {len(rate_limited)}")
        lines.extend(f"⚠ {site}" for site in rate_limited)

    lines.append("")
    lines.append(
        "Note: Holehe results are not absolute proof. Sites can block checks, "
        "change their recovery systems, or return false results."
    )

    return "\n".join(lines)


def tool_holehe(args):
    email = str(args.get("email", "")).strip()
    only_used = bool(args.get("only_used", True))

    if not EMAIL_PATTERN.fullmatch(email):
        return "Holehe error: provide a valid email address."

    holehe_path = shutil.which("holehe")
    if not holehe_path:
        return "Holehe error: Holehe is not installed in Kuza's environment."

    command = [holehe_path, email, "--no-clear"]

    if only_used:
        command.append("--only-used")

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "Holehe error: scan timed out after 180 seconds."
    except Exception as exc:
        return f"Holehe error: {exc}"

    output = result.stdout.strip() or result.stderr.strip()

    if not output:
        return "Holehe finished but returned no visible results."

    return _clean_holehe_output(output, email)
