import base64
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("github_api", Path(__file__).resolve().parents[1] / "scripts/api.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class Fixture:
    def __init__(self):
        self.calls = []
        self.fail_after_write = False
        self.written = False

    def __call__(self, endpoint, method="GET", payload=None):
        self.calls.append((endpoint, method, payload))
        if method != "GET":
            self.written = True
            return None, {}
        if self.written and self.fail_after_write:
            raise api.Failure("offline")
        if endpoint == "user":
            return {"id": 1, "login": "fixture"}, {}
        if endpoint.startswith("user/orgs?"):
            return [{"login": "kryonlabs", "description": "Native applications"}], {}
        if endpoint.startswith(("orgs/", "user/repos?", "user/starred?", "search/repositories?")):
            values = [{"full_name": "kryonlabs/example", "stargazers_count": 42, "forks_count": 3, "language": "Ziran", "private": False, "description": "An example"}]
            return {"items": values} if endpoint.startswith("search/") else values, {"link": '<https://api.github.com/' + endpoint.split('?')[0] + '?page=2>; rel="next"'}
        if endpoint == "repos/kryonlabs/example":
            return {"full_name": "kryonlabs/example", "language": "Ziran", "description": "An example", "stargazers_count": 42}, {}
        if endpoint.endswith("/languages"):
            return {"Ziran": 75, "CSS": 25}, {}
        if endpoint.startswith("user/starred/"):
            return True, {}
        if "/readme" in endpoint or "/contents/src/app.zi" in endpoint:
            return {"size": 14, "encoding": "base64", "content": base64.b64encode(b"# Native app\n").decode()}, {}
        if "/contents/" in endpoint:
            return [{"type": "file", "name": f"file-{i}", "path": f"file-{i}", "size": 3} for i in range(70)], {}
        if "/issues/7/comments" in endpoint:
            return [{"user": {"login": "reader"}, "body": "A complete comment"}], {}
        if "/issues?" in endpoint:
            return [{"number": 7, "title": "Fix scrolling", "state": "open", "comments": 1, "user": {"login": "author"}}], {}
        if "/issues/7" in endpoint:
            return {"title": "Fix scrolling", "state": "open", "body": "Full issue text", "user": {"login": "author"}}, {}
        if "/pulls/7/files" in endpoint:
            return [{"filename": "src/app.zi", "patch": "+ scrolling fixed"}], {"link": '<https://api.github.com/repos/kryonlabs/example/pulls/7/files?page=2>; rel="next"'}
        if "/pulls/7" in endpoint:
            return {"title": "Fix scrolling", "state": "open", "body": "Full PR text", "user": {"login": "author"}, "head": {"label": "fixture:scroll"}, "base": {"label": "fixture:master"}}, {}
        if "/commits/abcdef" in endpoint:
            return {"commit": {"message": "Fix scrolling"}, "files": [{"filename": "src/app.zi", "patch": "+ fixed"}]}, {}
        if "/branches?" in endpoint:
            return [{"name": "feature/ui", "protected": False}], {}
        if "/actions/runs/7/jobs" in endpoint:
            return {"jobs": [{"name": "Tests", "conclusion": "success", "steps": [{"name": "Unit tests", "conclusion": "success"}]}]}, {}
        if "/actions/runs/7" in endpoint:
            return {"display_title": "Tests", "name": "CI"}, {}
        if endpoint.startswith("notifications?"):
            return [{"id": "9", "unread": True, "repository": {"full_name": "kryonlabs/example"}, "subject": {"title": "Fix scrolling", "url": "https://api.github.com/repos/kryonlabs/example/issues/7"}}], {}
        raise AssertionError(endpoint)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.client = api.Client(self.fixture)

    def test_organizations_and_repository_pagination(self):
        response = self.client.read({"view": "organizations"})
        self.assertEqual(response["rows"][0]["kind"], "org")
        response = self.client.read({"view": "repositories", "org": "kryonlabs"})
        self.assertTrue(response["next"])
        self.assertIn("42 stars", response["rows"][0]["metadata"])
        self.assertIn("Ziran", response["rows"][0]["metadata"])
        self.assertEqual(response["rows"][0]["kind"], "repo")

    def test_repository_overview_has_languages_readme_and_star_state(self):
        response = self.client.read({"view": "overview", "repo": "kryonlabs/example"})
        self.assertIn("Ziran   75.0%", response["body"])
        self.assertIn("# Native app", response["body"])
        self.assertTrue(response["starred"])
        self.assertTrue(response["can_star"])

    def test_files_are_read_in_client_and_directories_have_all_pages(self):
        request = {"view": "code", "repo": "kryonlabs/example"}
        response = self.client.read(request)
        self.assertEqual(len(response["rows"]), 30)
        self.assertTrue(response["next"])
        self.assertEqual(len(self.client.read(request | {"page": 3})["rows"]), 10)
        response = self.client.read(request | {"path": "src/app.zi", "view": "file", "ref": "feature/a b"})
        self.assertEqual(response["body"], "# Native app\n")
        self.assertIn("ref=feature%2Fa+b", self.fixture.calls[-1][0])

    def test_notifications_open_native_issue_with_comments(self):
        response = self.client.read({"view": "notifications"})
        target = response["rows"][0]
        self.assertEqual((target["kind"], target["target"]), ("issue", "7"))
        response = self.client.read({"view": target["kind"], "item": target["target"], "repo": target["repo"]})
        self.assertIn("Full issue text", response["body"])
        self.assertIn("A complete comment", response["body"])

    def test_read_navigation_never_writes(self):
        for view in ("organizations", "repositories", "starred", "notifications"):
            self.client.read({"view": view})
        self.assertTrue(all(method == "GET" for _, method, _ in self.fixture.calls))

    def test_pr_changes_commits_branches_and_workflow_jobs(self):
        request = {"repo": "kryonlabs/example", "item": "7"}
        response = self.client.read(request | {"view": "pull"})
        self.assertIn("Full PR text", response["body"])
        self.assertIn("+ scrolling fixed", response["body"])
        self.assertTrue(response["next"], "PR file pagination must survive an unpaged comment list")
        response = self.client.read(request | {"view": "commit", "item": "abcdef"})
        self.assertIn("+ fixed", response["body"])
        response = self.client.read(request | {"view": "branches"})
        self.assertEqual(response["rows"][0]["target"], "feature/ui")
        response = self.client.read(request | {"view": "run"})
        self.assertIn("Unit tests · success", response["body"])

    def test_explicit_comment_and_successful_write_survives_refresh_failure(self):
        self.fixture.fail_after_write = True
        result = self.client.read({"view": "issue", "repo": "kryonlabs/example", "item": "7", "mutation": "comment", "text": "Hello\nworld"})
        self.assertEqual(result["state"], "ready")
        self.assertIn("Comment posted", result["message"])
        writes = [v for v in self.fixture.calls if v[1] != "GET"]
        self.assertEqual(writes, [("repos/kryonlabs/example/issues/7/comments", "POST", {"body": "Hello\nworld"})])

    def test_invalid_routes_cannot_change_destination_or_execute_commands(self):
        for request in ({"repo": "x/../y"}, {"org": "https://evil.test"}, {"repo": "x/y; rm"}, {"page": -1}, {"repo": "x/y", "view": "file", "path": "../secret"}):
            with self.assertRaises(api.Failure):
                self.client.read(request)
        with self.assertRaises(api.Failure):
            api.more({"link": '<https://evil.test/user/orgs?page=2>; rel="next"'}, "user/orgs?page=1")

    def test_gh_credentials_and_errors_do_not_cross_transport(self):
        raw = subprocess.CompletedProcess([], 1, "", "SECRET TOKEN")
        with patch.object(api.subprocess, "run", return_value=raw):
            with self.assertRaises(api.Failure) as caught:
                api.call("user")
        self.assertNotIn("SECRET", str(caught.exception))

    def test_missing_star_is_normal(self):
        raw = subprocess.CompletedProcess([], 1, 'HTTP/2.0 404 Not Found\nContent-Type: application/json\n\n{"message":"Not Found"}', "")
        with patch.object(api.subprocess, "run", return_value=raw):
            self.assertFalse(api.call("user/starred/kryonlabs/example")[0])


if __name__ == "__main__":
    unittest.main()
