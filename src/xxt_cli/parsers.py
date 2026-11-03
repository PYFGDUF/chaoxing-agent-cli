"""Pure HTML parsers, adapted from xxt-homework-fetcher's selectors (MIT)."""
from __future__ import annotations
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit
from bs4 import BeautifulSoup


def identity(url: str, kind: str = "work") -> str:
    """Keep course/class/work/answer identity; discard rotating signatures."""
    parts = urlsplit(url)
    query = {k.lower(): v for k, v in parse_qsl(parts.query)}
    keys = ("courseid", "clazzid", "classid") if kind == "course" else (
        "courseid", "clazzid", "classid", "workid", "answerid", "taskid", "id")
    values = {("clazzid" if k == "classid" else k): query[k] for k in keys if k in query}
    canonical = urlencode(sorted(values.items())) if values else parts.netloc + parts.path + parts.query
    return kind + "_" + hashlib.sha256(canonical.encode()).hexdigest()[:20]


def soup(html):
    return BeautifulSoup(html, "html.parser")


def rich(node, base: str) -> str:
    if node is None:
        return ""
    clone = soup(str(node))
    for el in clone.select('script, style, .katex-mathml, [aria-hidden="true"]'):
        el.decompose()
    for el in clone.select("img"):
        src = el.get("data-src") or el.get("src", "")
        alt = re.sub(r"[\[\]()\n]", "", el.get("alt", "图片"))
        el.replace_with(f" ![{alt}]({urljoin(base, src)}) " if src else "")
    # Preserve preformatted code and block boundaries.
    for el in clone.select("br"):
        el.replace_with("\n")
    for el in clone.select("p, div, pre, li"):
        el.append("\n")
    text = clone.get_text()
    return re.sub(r"\n[ \t]*\n+", "\n\n", text).strip()


def courses(html: str, base: str) -> list[dict]:
    root = soup(html)
    result = {}
    for card in root.select('[id^="course_"], .course-list li, #courseList li'):
        a = card.select_one('h3 a[href], a[href*="stucoursemiddle"], a[href*="mycourse/stu"]')
        if a is None:
            continue
        url = urljoin(base, a.get("href", ""))
        if "stucoursemiddle" not in url and "mycourse/stu" not in url:
            continue
        title = a.get("title") or a.get_text(" ", strip=True)
        teacher = card.select(".course-info p")
        item = {"id": identity(url, "course"), "title": title, "url": url,
                "teacher": teacher[-1].get_text(" ", strip=True) if teacher else "",
                "ended": "课程已结束" in card.get_text()}
        result[item["id"]] = item
    return list(result.values())


def homeworks(html: str, base: str) -> dict:
    root = soup(html)
    items, reviews = {}, {}
    for el in root.select('[data*="work/"], a[href*="work/"]'):
        url = urljoin(base, el.get("data") or el.get("href", ""))
        path = urlsplit(url).path.lower()
        if not any(x in path for x in ("/task", "/view", "/do", "/phone", "/edit", "/eval-list")):
            continue
        title_el = el.select_one(".overHidden2, .overHidden, .work-title, .title")
        title = (title_el or el).get_text(" ", strip=True)
        status = el.select_one(".status")
        timing = el.select_one(".time")
        item = {"id": identity(url), "title": title, "url": url,
                "status": status.get_text(" ", strip=True) if status else "unknown",
                "time_text": timing.get_text(" ", strip=True) if timing else None}
        (reviews if "/eval-list" in path else items)[item["id"]] = item
    for key, item in items.items():
        item["peer_review"] = reviews.get(key)
    text = root.get_text(" ", strip=True)
    total = None
    for pattern in (r"(?:共|总计)\s*(\d+)\s*(?:份|个|项)", r"\b\d+\s*/\s*(\d+)\b"):
        m = re.search(pattern, text)
        if m:
            total = int(m.group(1))
            break
    return {"items": list(items.values()), "reported_total": total,
            "peer_review_count": len(reviews)}


def questions(html: str, base: str) -> list[dict]:
    root = soup(html)
    nodes = root.select(".questionLi, .singleQuesId, .TiMu, .question-item")
    result = []
    for node in nodes:
        if node.select_one(".questionLi, .singleQuesId, .TiMu, .question-item"):
            continue
        title_el = node.select_one(".mark_name, .qtContent, .TiMuTitle, .stem, .question-title, h3, h4")
        if title_el is None:
            continue
        title = rich(title_el, base)
        options = []
        for selector in (".qtDetail li", ".options li", ".answer-list li", ".option-item", ".answer_option"):
            options = [rich(el, base) for el in node.select(selector)]
            if options:
                break
        correct = rich(node.select_one(".rightAnswerContent, .right-answer, .correct-answer"), base)
        student = rich(node.select_one(".stuAnswerContent, .student-answer"), base)
        if not student:
            student = "；".join(el.get("value", "") if el.name == "input" else el.get_text()
                               for el in node.select('textarea, input[type="text"], input:checked'))
        analysis = rich(node.select_one(".analysis, .answer-analysis, .analysisContent"), base)
        qtype = next((t for t in ("多选题", "单选题", "判断题", "填空题", "简答题", "论述题", "计算题")
                      if t in title), "unknown")
        qid = node.get("data-questionid") or node.get("id") or node.get("data")
        result.append({"id": str(qid) if qid else None, "index": len(result) + 1,
                       "type": qtype, "title": title, "options": options,
                       "correct_answer": correct or None, "student_answer": student or None,
                       "analysis": analysis or None,
                       "attachments": [{"title": a.get_text(" ", strip=True),
                                        "url": urljoin(base, a["href"])}
                                       for a in node.select('a[href]') if a["href"].startswith(("http", "/"))]})
    return result


def markdown(data: dict) -> str:
    lines = ["# " + data.get("title", "作业"), ""]
    for q in data.get("questions", []):
        lines += [f'## {q["index"]}. {q["type"]}', "", q["title"], ""]
        lines += [o + "\n" for o in q["options"]]
        for key, label in (("correct_answer", "正确答案"), ("student_answer", "我的答案"), ("analysis", "解析")):
            if q.get(key):
                lines += [f'**{label}**\n\n{q[key]}', ""]
        for a in q.get("attachments", []):
            lines += [f'[{a["title"]}]({a["url"]})', ""]
    return "\n".join(lines)
