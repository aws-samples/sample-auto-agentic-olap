"""Redshift Data API result handling."""

from __future__ import annotations

from typing import Any

from app.redshift.client import RedshiftDataApi


class PagedClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_statement_result(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if "NextToken" not in kwargs:
            return {
                "ColumnMetadata": [{"name": "brand"}, {"name": "amount"}],
                "Records": [[{"stringValue": "A"}, {"longValue": 100}]],
                "NextToken": "page-2",
            }
        return {
            "ColumnMetadata": [{"name": "brand"}, {"name": "amount"}],
            "Records": [[{"stringValue": "B"}, {"longValue": 60}]],
        }


def test_query_records_reads_every_result_page(monkeypatch) -> None:
    api = RedshiftDataApi.__new__(RedshiftDataApi)
    api.client = PagedClient()
    monkeypatch.setattr(api, "execute", lambda sql, timeout_seconds=None: {"Id": "s1"})

    result = api.query_records("select 1")

    assert result == {
        "columns": ["brand", "amount"],
        "rows": [
            {"brand": "A", "amount": 100},
            {"brand": "B", "amount": 60},
        ],
        "row_count": 2,
    }
    assert api.client.calls == [{"Id": "s1"}, {"Id": "s1", "NextToken": "page-2"}]
