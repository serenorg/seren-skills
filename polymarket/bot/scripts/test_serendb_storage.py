"""
Unit tests for SerenDBStorage project bootstrap routing.
"""

import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent))

from serendb_storage import SerenDBStorage


def _response(payload):
    response = Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


class TestSerenDBStorageRoutes:
    def test_setup_database_uses_projects_routes_for_bootstrap(self):
        session = Mock()
        seren = Mock()
        seren.gateway_url = "https://api.serendb.com"
        seren.session = session

        query_urls = []

        def get_side_effect(url, timeout):
            assert timeout == 10
            if url == "https://api.serendb.com/projects":
                return _response([])
            if url == "https://api.serendb.com/projects/project-123":
                return _response({"data": {"id": "project-123", "name": "polymarket-bot"}})
            if url == "https://api.serendb.com/projects/project-123/branches":
                return _response([{"id": "branch-main", "name": "main"}])
            raise AssertionError(f"Unexpected GET {url}")

        def post_side_effect(url, json, timeout):
            if url == "https://api.serendb.com/projects":
                assert timeout == 30
                assert json == {"name": "polymarket-bot", "region": "aws-us-east-2"}
                return _response({"data": {"id": "project-123"}})
            if url == "https://api.serendb.com/projects/project-123/branches/branch-main/query":
                assert timeout == 30
                assert "query" in json
                query_urls.append(url)
                return _response({"data": {"rows": [], "changes": 0}})
            raise AssertionError(f"Unexpected POST {url}")

        session.get.side_effect = get_side_effect
        session.post.side_effect = post_side_effect

        storage = SerenDBStorage(seren)

        assert storage.setup_database() is True
        assert storage.project_id == "project-123"
        assert storage.branch_id == "branch-main"
        assert query_urls

    def test_execute_sql_uses_projects_query_endpoint_and_unwraps_data(self):
        session = Mock()
        seren = Mock()
        seren.gateway_url = "https://api.serendb.com"
        seren.session = session

        session.post.return_value = _response({"data": {"rows": [{"count": 1}]}})

        storage = SerenDBStorage(seren)
        storage.project_id = "project-123"
        storage.branch_id = "branch-main"

        result = storage._execute_sql("SELECT 1 AS count")

        assert result == {"rows": [{"count": 1}]}
        session.post.assert_called_once_with(
            "https://api.serendb.com/projects/project-123/branches/branch-main/query",
            json={"query": "SELECT 1 AS count"},
            timeout=30,
        )
