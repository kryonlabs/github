#!/usr/bin/env python3
"""GitHub transport. Credentials stay in gh; the native client owns the UI."""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlencode, urlsplit

PAGE_SIZE = 30
API_VERSION = "2026-03-10"


class Failure(Exception):
    pass


def text(value, limit=4096):
    return "".join(c for c in str(value or "") if c in "\n\t" or (c >= " " and c != "\x7f"))[:limit]


def slug(value, repository=False):
    pattern = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+" if repository else r"[A-Za-z0-9_.-]+"
    if not isinstance(value, str) or len(value) > 256 or not re.fullmatch(pattern, value) or any(p in (".", "..") for p in value.split("/")):
        raise Failure("Invalid GitHub destination.")
    return value


def call(endpoint, method="GET", payload=None):
    if not endpoint.startswith(("user", "orgs/", "repos/", "notifications", "search/repositories")) or ":" in endpoint:
        raise Failure("Invalid GitHub API destination.")
    args = ["gh", "api", "--hostname", "github.com", "--method", method, "--include",
            "-H", "Accept: application/vnd.github+json", "-H", f"X-GitHub-Api-Version: {API_VERSION}", endpoint]
    if payload is not None:
        args += ["--input", "-"]
    try:
        result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                                capture_output=True, text=True, timeout=25)
    except FileNotFoundError:
        raise Failure("Install GitHub CLI and sign in with gh auth login.") from None
    except (OSError, subprocess.TimeoutExpired):
        raise Failure("GitHub could not be reached. Check your connection and retry." if method == "GET" else
                      "The request outcome is unknown. Refresh and check GitHub before submitting again.") from None
    header, separator, body = result.stdout.replace("\r\n", "\n").partition("\n\n")
    status = re.match(r"HTTP/\S+ (\d{3})", header)
    if not status or not separator:
        raise Failure("GitHub connection unavailable. Check gh auth status and retry.")
    code = int(status[1])
    headers = dict((k.lower(), v.strip()) for line in header.splitlines()[1:] for k, sep, v in [line.partition(":")] if sep)
    if code == 401:
        raise Failure("GitHub sign-in expired. Run gh auth login to reconnect.")
    if code in (403, 429):
        raise Failure("GitHub rate limit reached. Try again later." if headers.get("x-ratelimit-remaining") == "0" or code == 429 else
                      "GitHub access denied. Check repository, organization or notification permissions.")
    if code == 404 and method == "GET" and endpoint.startswith("user/starred/"):
        return False, headers
    if code == 204 and method == "GET" and endpoint.startswith("user/starred/"):
        return True, headers
    if code >= 400:
        raise Failure("This GitHub item is unavailable or the request was rejected.")
    try:
        return json.loads(body) if body.strip() else None, headers
    except ValueError:
        raise Failure("GitHub returned an invalid response.") from None


def more(headers, endpoint):
    for link in headers.get("link", "").split(","):
        match = re.search(r'<([^>]+)>;\s*rel="next"', link)
        if match:
            url = urlsplit(match[1])
            if url.scheme != "https" or url.netloc != "api.github.com" or url.path != "/" + endpoint.split("?")[0]:
                raise Failure("GitHub returned an unexpected pagination link.")
            return True
    return False


def row(title, subtitle="", metadata="", kind="", target="", repo=""):
    return dict(title=text(title, 512), subtitle=text(subtitle, 1024), metadata=text(metadata, 1024),
                kind=kind, target=text(target, 2048), repo=repo)


def repo_row(repo):
    return row(repo["full_name"], repo.get("description"),
               f"{repo.get('stargazers_count', 0)} stars   {repo.get('forks_count', 0)} forks   {repo.get('language') or 'No language'}   "
               f"{'Private' if repo.get('private') else 'Public'}" + (" · Archived" if repo.get("archived") else ""),
               "repo", repo["full_name"], repo["full_name"])


def matches(repo, query):
    words = query.casefold().split()
    haystack = " ".join(str(repo.get(k) or "") for k in ("full_name", "description", "language")).casefold()
    return all(word in haystack for word in words)


class Client:
    def __init__(self, api=call):
        self.api = api

    def read(self, request):
        view = request.get("view", "organizations")
        page = request.get("page", 1)
        if type(page) is not int or not 1 <= page <= 100000:
            raise Failure("Invalid page.")
        query = request.get("query", "")
        if not isinstance(query, str) or len(query) > 200:
            raise Failure("Search must be at most 200 characters.")
        repo = slug(request["repo"], True) if request.get("repo") else ""
        org = slug(request["org"]) if request.get("org") else ""
        ref = request.get("ref", "")
        if not isinstance(ref, str) or len(ref) > 256 or any(ord(c) < 32 for c in ref):
            raise Failure("Invalid branch.")
        params = {"per_page": PAGE_SIZE, "page": page}
        user, _ = self.api("user")
        if not isinstance(user, dict) or not user.get("login"):
            raise Failure("GitHub account could not be verified.")
        result = dict(state="ready", message="", account=text(user["login"], 100), view=view,
                      title="GitHub", subtitle="", body="", rows=[], next=False, previous=page > 1,
                      can_star=False, starred=False, number=0, summary={}, languages=[])
        mutation = request.get("mutation", "")
        if mutation:
            return self.mutate(request, repo, mutation, result)
        headers = {}
        endpoint = ""
        if view == "organizations":
            endpoint = "user/orgs?" + urlencode(params)
            values, headers = self.api(endpoint)
            result.update(title="Organizations", subtitle="Choose an organization to explore its repositories.")
            result["rows"] = [row(v["login"], v.get("description"), "Organization", "org", v["login"]) for v in values]
        elif view in ("repositories", "starred"):
            resource = "user/starred" if view == "starred" else f"orgs/{org}/repos" if org else "user/repos"
            if query:
                # Filter the actual accessible collection, including private
                # organization/collaborator repos. GitHub's global search is
                # not a search of either this account's repos or its stars.
                values = []
                upstream_page = 1
                needed = page * PAGE_SIZE + 1
                while len(values) < needed:
                    scanned = resource + "?" + urlencode({"per_page": 100, "page": upstream_page, "sort": "updated"})
                    candidates, scan_headers = self.api(scanned)
                    values.extend(v for v in candidates if matches(v, query))
                    if not more(scan_headers, scanned):
                        break
                    upstream_page += 1
                result["next"] = len(values) > page * PAGE_SIZE
                values = values[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
            else:
                endpoint = resource + "?" + urlencode(params | {"sort": "updated"})
                values, headers = self.api(endpoint)
            result.update(title=org or ("Starred repositories" if view == "starred" else "Repositories"),
                          subtitle="Filtered by “" + text(query, 200) + "”" if query else "Updated recently · select a repository to explore it.")
            if org and view == "repositories":
                try:
                    profile, _ = self.api(f"orgs/{org}")
                    result["title"] = text(profile.get("name") or org)
                    result["subtitle"] = " · ".join(filter(None, (org, text(profile.get("description")),
                        str(profile["public_repos"]) + " public repositories" if "public_repos" in profile else "")))
                except Failure:
                    pass
            result["rows"] = [repo_row(v) for v in values]
        elif view == "notifications":
            endpoint = "notifications?" + urlencode(params | {"all": "true"})
            values, headers = self.api(endpoint)
            result.update(title="Notifications", subtitle="Your inbox · select a thread to read it here.")
            for v in values:
                subject = v.get("subject") or {}
                full_name = (v.get("repository") or {}).get("full_name", "")
                url = urlsplit(subject.get("url") or "")
                match = re.fullmatch(r"/repos/[\w.-]+/[\w.-]+/(issues|pulls|commits)/([\w.-]+)", url.path)
                kind, target = ("notification", str(v.get("id", "")))
                if url.netloc == "api.github.com" and match:
                    kind, target = {"issues": "issue", "pulls": "pull", "commits": "commit"}[match[1]], match[2]
                result["rows"].append(row(subject.get("title"), full_name,
                    ("Unread" if v.get("unread") else "Read") + " · " + text(v.get("reason")), kind, target, full_name))
        elif view == "overview" and repo:
            value, _ = self.api(f"repos/{repo}")
            languages, _ = self.api(f"repos/{repo}/languages")
            total = sum(languages.values()) or 1
            result["summary"] = dict(description=text(value.get("description")), stars=value.get("stargazers_count", 0),
                forks=value.get("forks_count", 0), issues=value.get("open_issues_count", 0),
                branch=text(value.get("default_branch")), license=text((value.get("license") or {}).get("spdx_id") or "No license", 100))
            result["languages"] = [dict(name=text(name, 100), percent=round(size * 100 / total, 2))
                for name, size in sorted(languages.items(), key=lambda item: -item[1])]
            result.update(title=repo, subtitle=repo_row(value)["metadata"], can_star=True,
                          body="")
            starred, _ = self.api(f"user/starred/{repo}")
            result["starred"] = bool(starred)
            try:
                readme, _ = self.api(f"repos/{repo}/readme" + ("?" + urlencode({"ref": ref}) if ref else ""))
                result["body"] = decode_file(readme)
            except Failure:
                result["body"] = "No readable README available."
        elif view in ("code", "file") and repo:
            path = request.get("path", "")
            if not isinstance(path, str) or len(path) > 2048 or any(p == ".." for p in path.split("/")):
                raise Failure("Invalid file path.")
            endpoint = f"repos/{repo}/contents/{quote(path, safe='/')}" + ("?" + urlencode({"ref": ref}) if ref else "")
            values, _ = self.api(endpoint)
            result.update(title=path or repo, subtitle=repo + (" · " + ref if ref else " · default branch"))
            if isinstance(values, list):
                values.sort(key=lambda v: (v.get("type") != "dir", v.get("name", "").casefold()))
                result["rows"] = [row(v["name"], v["path"], "Directory" if v["type"] == "dir" else f"{v.get('size', 0):,} bytes", v["type"], v["path"], repo)
                                  for v in values[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]]
                result["next"] = len(values) > page * PAGE_SIZE
            else:
                result.update(body=decode_file(values), previous=False)
        elif view in ("issues", "pulls", "commits", "branches", "releases", "actions") and repo:
            resource = "actions/runs" if view == "actions" else view
            extra = {"sha": ref} if view == "commits" and ref else {"state": "open"} if view in ("issues", "pulls") else {}
            endpoint = f"repos/{repo}/{resource}?" + urlencode(params | extra)
            values, headers = self.api(endpoint)
            if view == "actions":
                values = values["workflow_runs"]
            result.update(title=repo, subtitle=view.capitalize() + (" · " + ref if ref else ""))
            for v in values:
                if view in ("issues", "pulls"):
                    # /issues includes PRs; label and open them accurately.
                    kind = "pull" if view == "pulls" or "pull_request" in v else "issue"
                    result["rows"].append(row(f"#{v['number']}  {v['title']}", text((v.get("user") or {}).get("login")),
                                              f"{kind.capitalize()} · {v.get('state', '')} · {v.get('comments', 0)} comments", kind, str(v["number"]), repo))
                elif view == "commits":
                    commit = v.get("commit") or {}
                    result["rows"].append(row(commit.get("message", "").split("\n")[0], (commit.get("author") or {}).get("name"), v["sha"][:12], "commit", v["sha"], repo))
                elif view == "branches":
                    result["rows"].append(row(v["name"], "Protected" if v.get("protected") else "Branch", "", "branch", v["name"], repo))
                elif view == "releases":
                    result["rows"].append(row(v.get("name") or v["tag_name"], v.get("published_at"), v["tag_name"], "release", str(v["id"]), repo))
                else:
                    result["rows"].append(row(v.get("display_title") or v["name"], v.get("head_branch"), v.get("conclusion") or v.get("status"), "run", str(v["id"]), repo))
        elif view in ("issue", "pull", "commit", "release", "run") and repo:
            item = slug(str(request.get("item", "")))
            if view != "commit" and not item.isdigit():
                raise Failure("Invalid item number.")
            resource = {"issue": "issues", "pull": "pulls", "commit": "commits", "release": "releases", "run": "actions/runs"}[view]
            endpoint = f"repos/{repo}/{resource}/{item}" + ("?" + urlencode(params) if view == "commit" else "")
            value, headers = self.api(endpoint)
            result.update(title=value.get("title") or value.get("name") or value.get("tag_name") or value.get("display_title") or item,
                          subtitle=repo + " · " + view.capitalize(), number=int(item) if item.isdigit() else 0)
            if view in ("issue", "pull"):
                result["body"] = f"#{item} · {value.get('state', '')} · {(value.get('user') or {}).get('login', '')}\n\n" + text(value.get("body"), 120000)
                endpoint = f"repos/{repo}/issues/{item}/comments?" + urlencode(params)
                comments, headers = self.api(endpoint)
                result["body"] += "\n\nComments\n" + "\n\n".join(text((c.get("user") or {}).get("login")) + "\n" + text(c.get("body"), 20000) for c in comments)
                if view == "pull":
                    result["body"] += f"\n\n{(value.get('head') or {}).get('label', '')} → {(value.get('base') or {}).get('label', '')}\n"
                    files_endpoint = f"repos/{repo}/pulls/{item}/files?" + urlencode(params)
                    files, files_headers = self.api(files_endpoint)
                    result["next"] = more(files_headers, files_endpoint)
                    result["body"] += "\nChanges\n" + "\n\n".join(text(f.get("filename")) + "\n" + text(f.get("patch"), 20000) for f in files)
            elif view == "commit":
                result["body"] = text((value.get("commit") or {}).get("message"), 12000) + "\n\n" + "\n\n".join(text(f.get("filename")) + "\n" + text(f.get("patch"), 20000) for f in value.get("files", []))
            elif view == "release":
                result["body"] = text(value.get("body"), 120000)
            else:
                endpoint = f"repos/{repo}/actions/runs/{item}/jobs?" + urlencode(params)
                jobs, headers = self.api(endpoint)
                result["body"] = "\n\n".join(text(j.get("name")) + " · " + text(j.get("conclusion") or j.get("status")) + "\n" +
                    "\n".join(text(s.get("name")) + " · " + text(s.get("conclusion") or s.get("status")) for s in j.get("steps", [])) for j in jobs.get("jobs", []))
        else:
            raise Failure("Choose an organization or repository to begin.")
        if endpoint and view not in ("code", "file"):
            result["next"] = result["next"] or more(headers, endpoint)
        if len(result["body"]) > 240000:
            result["body"] = result["body"][:240000] + "\n\nPreview limit reached. Use the next page for more comments or changes."
        return result

    def mutate(self, request, repo, mutation, result):
        if mutation in ("star", "unstar") and repo:
            self.api(f"user/starred/{repo}", "PUT" if mutation == "star" else "DELETE")
        elif mutation in ("comment", "create_issue") and repo:
            body = request.get("text", "")
            if not isinstance(body, str) or not body.strip() or len(body) > 60000:
                raise Failure("Write a message before submitting.")
            if mutation == "comment":
                item = request.get("item", "")
                if not str(item).isdigit():
                    raise Failure("Invalid issue number.")
                self.api(f"repos/{repo}/issues/{item}/comments", "POST", {"body": body})
            else:
                title = request.get("title", "")
                if not isinstance(title, str) or not title.strip() or len(title) > 256:
                    raise Failure("Enter an issue title.")
                self.api(f"repos/{repo}/issues", "POST", {"title": title, "body": body})
        elif mutation == "read_notification":
            item = str(request.get("item", ""))
            if not item.isdigit():
                raise Failure("Invalid notification.")
            self.api(f"notifications/threads/{item}", "PATCH")
        else:
            raise Failure("Unsupported GitHub action.")
        following = dict(request)
        following.pop("mutation", None)
        following["starred"] = mutation == "star" if mutation in ("star", "unstar") else request.get("starred", False)
        confirmation = {"star": "Repository starred", "unstar": "Star removed", "comment": "Comment posted", "create_issue": "Issue created", "read_notification": "Notification marked as read"}[mutation]
        try:
            result = self.read(following)
        except Failure:
            # A successful write stays successful even if its follow-up read
            # fails. Never invite a duplicate comment or issue submission.
            result["message"] = confirmation + ". Refresh to reload this view."
            return result
        result["message"] = confirmation
        return result


def decode_file(value):
    if value.get("encoding") != "base64" or value.get("size", 0) > 180000:
        return "This file is too large for the text preview, or is not available as text."
    try:
        data = base64.b64decode(value.get("content", ""), validate=False)
        if b"\0" in data:
            return "Binary file · text preview unavailable."
        return data.decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return "File preview unavailable."


def main():
    os.umask(0o077)
    destination = Path(sys.argv[2])
    try:
        request = json.loads(Path(sys.argv[1]).read_text())
        result = Client().read(request)
    except Failure as exc:
        result = dict(state="error", message=str(exc), rows=[])
    except Exception:
        result = dict(state="error", message="GitHub could not load this view. Refresh to retry.", rows=[])
    fd, filename = tempfile.mkstemp(dir=destination.parent)
    with os.fdopen(fd, "w") as output:
        json.dump(result, output, ensure_ascii=False, separators=(",", ":"))
    os.replace(filename, destination)


if __name__ == "__main__":
    main()
