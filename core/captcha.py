from dataclasses import dataclass
from enum import Enum
from typing import Optional


class CaptchaStatus(str, Enum):
    CLEAR = "clear"
    CAPTCHA_REQUIRED = "captcha_required"
    MANUAL_REQUIRED = "manual_required"


@dataclass
class CaptchaResult:
    status: CaptchaStatus
    provider: str
    message: str
    resume_url: Optional[str] = None


CAPTCHA_MARKERS = (
    "captcha",
    "recaptcha",
    "hcaptcha",
    "turnstile",
    "verify you are human",
    "are you a robot",
    "security challenge",
    "challenge-platform",
)


def detect_captcha(
    provider: str,
    page_text: str = "",
    page_html: str = "",
    current_url: Optional[str] = None,
) -> CaptchaResult:
    combined = f"{page_text}\n{page_html}".lower()

    marker = next(
        (item for item in CAPTCHA_MARKERS if item in combined),
        None,
    )

    if marker:
        return CaptchaResult(
            status=CaptchaStatus.MANUAL_REQUIRED,
            provider=provider,
            message=f"CAPTCHA detected: {marker}",
            resume_url=current_url,
        )

    return CaptchaResult(
        status=CaptchaStatus.CLEAR,
        provider=provider,
        message="No CAPTCHA detected.",
        resume_url=current_url,
    )
