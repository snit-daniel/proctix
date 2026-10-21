"""Local document consistency checks. These do NOT authenticate an issuer or certify a genuine ID."""

from datetime import date
import os
import re
import shutil
import subprocess
import unicodedata


def check_digit(value):
    values = {str(i): i for i in range(10)} | {chr(65 + i): 10 + i for i in range(26)} | {"<": 0}
    if any(c not in values for c in value):
        raise ValueError("Invalid MRZ character")
    return str(sum(values[c] * (7, 3, 1)[i % 3] for i, c in enumerate(value)) % 10)


def mrz_checks(text, today=None):
    today = today or date.today()
    lines = [re.sub(r"\s", "", line.upper()) for line in text.splitlines()]
    result = {"mrz_detected": False, "mrz_supported": False, "checksum_consistent": None, "expired": None}
    for i, line in enumerate(lines):
        if len(line) == 44 and line.startswith("P") and i + 1 < len(lines) and len(lines[i + 1]) == 44:
            second = lines[i + 1]
            pairs = [
                (second[:9], second[9]),
                (second[13:19], second[19]),
                (second[21:27], second[27]),
                (second[28:42], second[42]),
                (second[:10] + second[13:20] + second[21:43], second[43]),
            ]
            expiry, names, kind = second[21:27], line[5:], "TD3 passport"
        elif (
            len(line) == 30
            and line[:1] in ("I", "A", "C")
            and i + 2 < len(lines)
            and len(lines[i + 1]) == len(lines[i + 2]) == 30
        ):
            second, names = lines[i + 1], lines[i + 2]
            pairs = [
                (line[5:14], line[14]),
                (second[:6], second[6]),
                (second[8:14], second[14]),
                (line[5:] + second[:7] + second[8:15] + second[18:29], second[29]),
            ]
            expiry, kind = second[8:14], "TD1 identity card"
        else:
            continue
        result.update(mrz_detected=True, mrz_supported=True, format=kind)
        try:
            result["checksum_consistent"] = all(
                check_digit(data) == (digit if digit != "<" else "0") for data, digit in pairs
            )
            year, month, day = int(expiry[:2]), int(expiry[2:4]), int(expiry[4:6])
            # Expiry dates are interpreted in the nearest 100-year window.
            current = today.year // 100 * 100 + year
            if current > today.year + 50:
                current -= 100
            if current < today.year - 50:
                current += 100
            result["expired"] = date(current, month, day) < today
        except (ValueError, KeyError):
            result["checksum_consistent"] = False
            result["date_readable"] = False
        return result, names.replace("<", " ")
    return result, ""


def name_tokens(value):
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().upper()
    return set(re.findall(r"[A-Z]{2,}", normalized))


def document_appearance(text, checks, expected_name="", portrait=None, side="id"):
    """Bounded English text/portrait heuristics, not a trained authenticity classifier.

    Return only cue names and outcomes, never OCR text or extracted personal data.
    Missing language/model support is inconclusive, not a negative identity verdict.
    """
    readable = len(re.findall(r"[A-Za-z0-9]", text)) >= 30
    categories = {
        "passport": r"\bPASSPORT\b",
        "residency_id": r"\b(?:RESIDEN(?:CE|T|CY)\s+(?:CARD|PERMIT|ID)|EMIRATES\s+ID)\b",
        "student_id": r"\bSTUDENT\s+(?:IDENTITY|IDENTIFICATION|ID|CARD)|\b(?:UNIVERSITY|COLLEGE|SCHOOL)\s+(?:IDENTITY|IDENTIFICATION|ID)\b",
        "identity_card": r"\b(?:IDENTITY\s+CARD|IDENTIFICATION\s+CARD|NATIONAL\s+ID|ID\s+CARD|DRIV(?:ER.?S|ING)\s+LICEN[CS]E)\b",
    }
    category = next((k for k, pattern in categories.items() if re.search(pattern, text, re.I)), None)
    if checks["mrz_detected"]:
        category = "passport" if checks["format"] == "TD3 passport" else "identity_card"
    patterns = {
        "name_field": r"\b(?:NAME|SURNAME|GIVEN\s+NAMES?)\b",
        "document_number_field": r"\b(?:(?:PASSPORT|DOCUMENT|CARD|IDENTITY|ID|STUDENT|REGISTRATION|ENROLLMENT|LICEN[CS]E)[ \t]*(?:NO\.?|NUMBER|#)|(?:NO\.?|NUMBER)[ \t]*[:#])[ \t]*[:#]?[ \t]*[A-Z0-9][A-Z0-9/-]{3,31}\b",
        "date_field": r"\b(?:DATE\s+OF\s+BIRTH|DOB|BIRTH|EXPIR(?:Y|ATION|ES)|VALID\s+(?:UNTIL|THRU|TO)|ISSUE(?:D|\s+DATE))\b",
        "issuer_field": r"\b(?:AUTHORITY|NATIONALITY|CITIZENSHIP|UNIVERSITY|COLLEGE|SCHOOL|MINISTRY|GOVERNMENT|DEPARTMENT)\b",
    }
    cues = [key for key, pattern in patterns.items() if re.search(pattern, text, re.I)]
    if checks["mrz_detected"]:
        cues.append("machine_readable_zone")
    expected = name_tokens(expected_name)
    name_found = bool(expected) and expected.issubset(name_tokens(text))
    portrait = portrait or {}
    portrait_present = None
    if portrait.get("status") == "assessed":
        portrait_present = portrait.get("face_count") == 1 or bool(portrait.get("document_portrait_selected"))
    structured = bool(category) and (
        checks.get("checksum_consistent") is True
        or (len(cues) >= 3 and "name_field" in cues and "document_number_field" in cues)
    )
    if side == "id_back":
        outcome, reason = (
            "supporting_side_only",
            "This is a supporting side. Upload the portrait side as your main ID photo.",
        )
    elif not readable:
        outcome, reason = (
            "inconclusive",
            "Text is unreadable or the document language is not supported by English OCR. Retake a clear, full-document photo.",
        )
    elif not category:
        outcome, reason = (
            "no_id_features_found",
            "No supported ID heading or machine-readable layout was found. Upload the full passport photo page, residency card, student ID, or another photo ID.",
        )
    elif not structured:
        outcome, reason = (
            "inconclusive",
            "An ID heading was found, but not enough document fields. Include the full document with its name and document number visible.",
        )
    elif portrait_present is False:
        outcome, reason = (
            "inconclusive",
            "Document fields were found, but no unambiguous portrait. Upload the photo side with a clear portrait.",
        )
    elif portrait_present is None or not portrait.get("comparison_usable", portrait.get("quality_ok")):
        outcome, reason = (
            "inconclusive",
            "Document fields were found, but portrait quality could not be confirmed. Use a sharper, well-lit photo.",
        )
    elif expected and not name_found:
        outcome, reason = (
            "inconclusive",
            "ID features and a portrait were found, but the registered name could not be read. Check the name and retake without glare; spelling and OCR may differ.",
        )
    elif checks.get("checksum_consistent") is False:
        outcome, reason = (
            "inconclusive",
            "ID features were found, but the machine-readable data is inconsistent. Retake a clearer photo.",
        )
    else:
        outcome, reason = (
            "id_features_found",
            "The image has readable ID features and a clear portrait. This is an appearance check, not proof of a genuine document.",
        )
    return {
        "document_appearance": outcome,
        "appearance_guidance": reason,
        "document_category_hint": category,
        "document_cues": cues,
        "portrait_present": portrait_present,
        "appearance_method": "English OCR field heuristics plus visible-portrait quality; not an authenticity model",
    }


def assess_document_data(data, expected_name="", *, reader=None, today=None, portrait=None, side="id"):
    result = {
        "authenticity": "not_verified",
        "issuer_verified": False,
        "interpretation": "OCR and MRZ checks can find inconsistencies but can be copied onto a forged image. No issuing authority or signed chip was authenticated.",
    }
    if reader is None:
        executable = shutil.which("tesseract")
        if not executable:
            return {**result, "status": "unavailable", "ocr_error": "not_installed", "reason": "Local OCR is not installed."}

        def reader(payload):
            readings = []
            expected = name_tokens(expected_name)
            for mode in (6, 11):
                command = subprocess.run(
                    [executable, "stdin", "stdout", "--psm", str(mode), "-l", "eng"],
                    input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=5, env={**os.environ, "OMP_THREAD_LIMIT": "1"}, check=True,
                )
                reading = command.stdout[:32000].decode("utf-8", "replace")
                readings.append(reading)
                if expected and expected.issubset(name_tokens(reading)):
                    return reading
            # Keep OCR layouts separate: matching must succeed within one reading.
            return max(readings, key=lambda text: (
                len(expected & name_tokens(text)), len(re.findall(r"[A-Za-z0-9]", text))
            ))

    try:
        text = reader(data)
    except subprocess.TimeoutExpired:
        return {**result, "status": "unavailable", "ocr_error": "timeout", "reason": "Document text reading timed out."}
    except (OSError, subprocess.SubprocessError):
        return {
            **result, "status": "unavailable", "ocr_error": "failed",
            "reason": "The document text reader failed to process this image.",
        }

    checks, names = mrz_checks(text, today)
    expected = name_tokens(expected_name)
    visible_tokens = name_tokens(text)
    name_match = bool(expected) and (
        expected.issubset(name_tokens(names)) or expected.issubset(visible_tokens)
    )
    name_check = (
        "matched" if name_match else
        "unsupported_expected_name" if not expected else
        "no_readable_text" if not visible_tokens else
        "partial_match" if expected & visible_tokens else
        "name_not_found"
    )
    keywords = bool(
        re.search(r"\b(PASSPORT|IDENTITY|IDENTIFICATION|EMIRATES|NATIONAL|STUDENT)\b", text, re.I)
    )
    status = "checks_completed" if checks["mrz_detected"] else "limited_checks"
    return {
        **result,
        **checks,
        **document_appearance(text, checks, expected_name, portrait, side),
        "status": status,
        "document_text_detected": keywords,
        "text_readable": len(re.findall(r"[A-Za-z0-9]", text)) >= 30,
        "registered_name_found": name_match if expected else None,
        "name_check": name_check,
        "matched_name_parts": len(expected & visible_tokens),
        "expected_name_parts": len(expected),
        "raw_text_stored": False,
        "scope": "TD1/TD3 MRZ and English OCR only; document type and authenticity are not certified.",
    }
