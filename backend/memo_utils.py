#!/usr/bin/env python3
"""Memo extraction helpers for Star Office backend.

Reads and sanitizes daily memo content from memory/*.md for the yesterday-memo API.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import re


def get_yesterday_date_str() -> str:
    """Return yesterday's date as YYYY-MM-DD."""
    yesterday = datetime.now() - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")


def sanitize_content(text: str) -> str:
    """Redact PII and sensitive patterns (OpenID, paths, IPs, email, phone) for safe display."""
    text = re.sub(r'ou_[a-f0-9]+', '[用户]', text)
    text = re.sub(r'user_id="[^"]+"', 'user_id="[隐藏]"', text)
    text = re.sub(r'/root/[^"\s]+', '[路径]', text)
    text = re.sub(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', '[IP]', text)

    text = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', '[邮箱]', text)
    text = re.sub(r'1[3-9]\d{9}', '[手机号]', text)

    return text


def extract_memo_from_file(file_path: str) -> str:
    """Extract a compact, display-safe summary from a daily memory markdown file."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        lines = content.strip().split("\n")

        noise_patterns = [
            r"^\*\*Session Key\*\*:",
            r"^\*\*Session ID\*\*:",
            r"^\*\*Source\*\*:",
            r"^user:",
            r"^sender:",
            r"^Conversation info",
            r"^Sender \(untrusted metadata\):",
            r'^"message_id":',
            r'^"sender_id":',
            r'^"timestamp":',
            r'^"sender":',
            r'^"label":',
            r'^"id":',
            r'^"name":',
            r'^"username":',
            r'^https?://',
            r'^```',
            r'^[\{\}\[\],]+$',
        ]

        def is_noise(line: str) -> bool:
            s = line.strip()
            if not s:
                return True
            if s.startswith("# Session:"):
                return True
            for p in noise_patterns:
                if re.match(p, s, re.IGNORECASE):
                    return True
            return False

        def normalize_text(text: str) -> str:
            text = text.strip()
            text = re.sub(r"^\[\[.*?\]\]\s*", "", text)
            text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
            text = re.sub(r"`([^`]+)`", r"\1", text)
            text = re.sub(r"\s+", " ", text)
            text = sanitize_content(text)
            return text.strip(" ：:;；，,。.!！?？")

        def seems_meta(text: str) -> bool:
            bad_keywords = [
                "如果你要",
                "如果你愿意",
                "我可以继续",
                "说明一下",
                "当前拿到的是",
                "正文全文没有",
                "你回我一句",
                "你一句话",
            ]
            return any(k in text for k in bad_keywords)

        assistant_lines = []
        bullet_points = []
        section_lines: dict[str, list[str]] = {}
        current_section = ""

        for raw in lines:
            line = raw.strip()

            if line.startswith("## "):
                current_section = normalize_text(line[3:])
                section_lines.setdefault(current_section, [])
                continue

            if line.startswith("assistant:"):
                line = normalize_text(line[len("assistant:"):].strip())
                if line and not is_noise(line) and len(line) >= 12:
                    assistant_lines.append(line)
                continue

            if is_noise(line):
                continue
            if line.startswith("#"):
                continue
            if line.startswith("- "):
                point = normalize_text(line[2:].strip())
                if point and not seems_meta(point):
                    bullet_points.append(point)
                    if current_section:
                        section_lines.setdefault(current_section, []).append(point)
            else:
                point = normalize_text(line)
                if point and len(point) >= 12 and not seems_meta(point):
                    bullet_points.append(point)
                    if current_section:
                        section_lines.setdefault(current_section, []).append(point)

        def score_summary(text: str) -> int:
            score = 0
            if any(k in text for k in ["总结", "摘要", "核心", "本质", "重点", "实现"]):
                score += 4
            if any(k in text for k in ["好了", "本质上", "这篇文章", "主要是", "核心摘要"]):
                score += 3
            if any(k in text for k in ["我先把", "我把", "继续扒", "读完给你", "先给你"]):
                score -= 3
            score -= max(0, len(text) - 42) // 12
            return score

        preferred_summary = ""

        one_liner_sections = [name for name in section_lines if "一句话版" in name]
        for sec in one_liner_sections:
            for item in section_lines.get(sec, []):
                if 16 <= len(item) <= 88:
                    preferred_summary = item
                    break
            if preferred_summary:
                break

        if not preferred_summary:
            ranked = sorted(
                [x for x in assistant_lines + bullet_points if 18 <= len(x) <= 88 and not seems_meta(x)],
                key=score_summary,
                reverse=True,
            )
            if ranked:
                preferred_summary = ranked[0]

        if not preferred_summary and assistant_lines:
            preferred_summary = max(assistant_lines, key=score_summary)[:88]

        if not preferred_summary:
            return "昨日节奏平稳，暂无可展示的小记。"

        detail_points = []
        seen = {preferred_summary}
        process_prefixes = ("我先把", "我把", "继续", "读完给你", "先给你", "我先给你")

        preferred_detail_pool = []
        for sec_name in ["文章想表达的重点", "核心摘要"]:
            for actual_name, items in section_lines.items():
                if sec_name in actual_name:
                    preferred_detail_pool.extend(items)

        candidate_pool = preferred_detail_pool + assistant_lines + bullet_points
        for candidate in candidate_pool:
            candidate = candidate.strip()
            if not candidate or candidate in seen:
                continue
            if seems_meta(candidate):
                continue
            if candidate.startswith(process_prefixes):
                continue
            if len(candidate) > 42:
                candidate = candidate[:39] + "..."
            if len(candidate) < 8:
                continue
            detail_points.append(candidate)
            seen.add(candidate)
            if len(detail_points) >= 2:
                break

        result = [preferred_summary]
        for point in detail_points:
            result.append(f"· {point}")

        return "\n".join(result).strip()

    except Exception as e:
        print(f"extract_memo_from_file failed: {e}")
        return "昨日记录暂时加载失败。"
