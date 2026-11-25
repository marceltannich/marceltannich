#!/usr/bin/env python3
"""Refresh the generated sections of README.md.

Fills the blocks between <!-- NAME:START --> and <!-- NAME:END --> with:
- POSTS: latest blog posts from the tannich.com REST API
- TALKS: latest talks from the tannich.com REST API (talks post type)
- STATS: counts of public PRs, code reviews and issues in other people's
         projects, plus total GitHub contributions in the last year
- PRS:   recent public pull requests in other people's projects

Standard library only, so the workflow needs no third-party packages.
Only public pull requests are counted and listed (`is:public`).
"""

import datetime
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request

USER = "marceltannich"
# Public repositories outside my own account and my own organisations.
SCOPE = "is:public -user:marceltannich -org:tannich-consulting -org:theproject99 -org:DebugConsole-dev"
SITE = "https://www.tannich.com"
README = os.path.join(os.path.dirname(__file__), "..", "README.md")
UA = "github-profile-readme-updater"


def fetch(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def clean(text):
    text = html.unescape(text or "").strip()
    # Markdown link text: keep brackets from breaking the link.
    return text.replace("[", "(").replace("]", ")")


def rest_items(endpoint, limit, with_date):
    """Latest entries from the WordPress REST API."""
    url = f"{SITE}/wp-json/wp/v2/{endpoint}?" + urllib.parse.urlencode(
        {"per_page": limit, "_fields": "title,link,date", "status": "publish"}
    )
    items = []
    for entry in json.loads(fetch(url)):
        title = clean(entry.get("title", {}).get("rendered"))
        link = entry.get("link", "")
        if not title or not link:
            continue
        when = ""
        if with_date and entry.get("date"):
            when = datetime.date.fromisoformat(entry["date"][:10]).strftime("%d %b %Y")
        items.append((title, link, when))
    return items


def render_feed(items):
    if not items:
        return None
    return "\n".join(
        f"- [{t}]({l})" + (f" · {w}" if w else "") for t, l, w in items
    )


def gh_search(query, per_page):
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = "https://api.github.com/search/issues?" + urllib.parse.urlencode(
        {"q": query, "sort": "updated", "order": "desc", "per_page": per_page}
    )
    return json.loads(fetch(url, headers))


def render_prs(limit=6):
    base = f"author:{USER} is:pr {SCOPE}"
    recent = gh_search(base, limit)
    merged = gh_search(base + " is:merged", 100)

    lines = []
    for pr in recent.get("items", []):
        repo = pr["repository_url"].split("/repos/", 1)[1]
        if pr.get("pull_request", {}).get("merged_at"):
            status = "merged"
        elif pr["state"] == "open":
            status = "open"
        else:
            status = "closed"
        lines.append(f"- [{clean(pr['title'])}]({pr['html_url']}) · `{repo}` · {status}")
    if not lines:
        return None

    return "\n".join(lines)


def count(query):
    return gh_search(query, 1).get("total_count", 0)


def contributions_last_year():
    """Total contributions from the GitHub contribution calendar.

    GraphQL needs a token; without one (local runs) the figure is left out.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return None
    query = '{user(login:"%s"){contributionsCollection{contributionCalendar{totalContributions}}}}' % USER
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query}).encode(),
        headers={"User-Agent": UA, "Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["totalContributions"]


def plural(n, one, many):
    return f"**{n}** {one if n == 1 else many}"


def render_stats():
    merged = count(f"author:{USER} is:pr is:merged {SCOPE}")
    opened = count(f"author:{USER} is:pr is:open {SCOPE}")
    reviews = count(f"reviewed-by:{USER} -author:{USER} is:pr {SCOPE}")
    issues = count(f"author:{USER} is:issue {SCOPE}")
    line = " · ".join([
        plural(merged, "merged pull request", "merged pull requests"),
        plural(opened, "open pull request", "open pull requests"),
        plural(reviews, "code review", "code reviews"),
        plural(issues, "issue reported", "issues reported"),
    ])
    total = None
    try:
        total = contributions_last_year()
    except Exception as exc:
        print(f"STATS: contributions skipped, {exc}", file=sys.stderr)
    if total:
        line += "\n\n" + plural(total, "contribution", "contributions") + " on GitHub in the last year."
    return line


def replace_block(text, name, body):
    pattern = re.compile(
        rf"(<!-- {name}:START -->\n).*?(<!-- {name}:END -->)", re.S
    )
    if not pattern.search(text):
        # A section is switched off by removing its markers from README.md.
        return text
    return pattern.sub(lambda m: m.group(1) + body + "\n" + m.group(2), text)


def main():
    with open(README, encoding="utf-8") as fh:
        original = fh.read()
    text = original

    sections = {
        "POSTS": lambda: render_feed(rest_items("posts", 5, with_date=True)),
        # Talks show no date: the post date is not the event date.
        "TALKS": lambda: render_feed(rest_items("talks", 3, with_date=False)),
        "STATS": render_stats,
        "PRS": render_prs,
    }
    for name, build in sections.items():
        if f"<!-- {name}:START -->" not in text:
            continue  # section not used in README.md
        try:
            body = build()
        except Exception as exc:  # keep the last good content on any failure
            print(f"{name}: skipped, {exc}", file=sys.stderr)
            continue
        if body:
            text = replace_block(text, name, body)
        else:
            print(f"{name}: no items, kept existing content", file=sys.stderr)

    if text != original:
        with open(README, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("README.md updated")
    else:
        print("README.md unchanged")


if __name__ == "__main__":
    main()
