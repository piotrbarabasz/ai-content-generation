"""Conservative evidence of editorial leakage; no literary quality scoring."""

import re


def narration_issues(text, *, title="", purposes=()):
    value = " ".join(text.split())
    normalized = value.casefold().strip(" .:—–-")
    issues = []
    if not value or not any(c.isalpha() for c in value):
        issues.append("Missing spoken prose.")
    if title and normalized == title.casefold().strip(" .:—–-"):
        issues.append("Text contains only the section title.")
    # Only exact copied fields, optionally prefixed by the known title.
    without_title = re.sub(r"^" + re.escape(title) + r"\s*[—–:\-]\s*", "", value, flags=re.I) if title else value
    if any(without_title.casefold().strip(" .") == " ".join(p.split()).casefold().strip(" .")
           for p in purposes if p and p.strip()):
        issues.append("Text copies a Video Plan purpose instead of narrating it.")
    if re.search(r"(?im)^\s*#{1,6}\s+|^\s*(?:narrator|voice[ -]?over|visuals?|editor|sfx|stage direction)\s*:", text):
        issues.append("Markdown heading or production label in spoken text.")
    instruction_text = re.sub(r"^[^.!?\n]{1,140}\s+[—–]\s+", "", value, count=1)
    if re.search(r"(?i)^(?:" + re.escape(title) + r"\s*[—–:\-]\s*)?"
                 r"(?:explain how|show the audience|pose (?:the|a|this|central) (?:central )?(?:counterfactual )?question|"
                 r"establish the stakes|immersive opening showing)\b(?!\s*\?)", instruction_text):
        issues.append("Text begins with an explicit editorial instruction.")
    if re.search(r"(?i)\[(?:cut to|fade (?:in|out)|show |music|sfx|visual|pause\b)", text):
        issues.append("Production stage direction in spoken text.")
    return tuple(issues)
