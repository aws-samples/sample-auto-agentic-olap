from __future__ import annotations

import itertools
import os
import time
from contextlib import suppress
from functools import lru_cache
from typing import Any

import boto3


@lru_cache(maxsize=1)
def _shared_client(region: str):
    """One boto3 client for the process. Creating a client per request re-loads
    the ECS task-role credentials and adds seconds of cold-start latency."""
    return boto3.client("redshift-data", region_name=region)


# Redshift Serverless compiles each new query shape on a remote, scalable
# compilation service and warms compute on demand. The FIRST time a given SQL
# shape runs on a cold/idle workgroup this can take several MINUTES (observed
# 200-500s) even though the actual execution is milliseconds. MetricFlow emits
# a fresh SQL string per metric/dimension combination, so cache misses (=cold
# compiles) are common. The default here must therefore be generous; override
# with REDSHIFT_QUERY_TIMEOUT if the ALB/client idle timeouts allow more.
try:
    DEFAULT_QUERY_TIMEOUT = int(os.getenv("REDSHIFT_QUERY_TIMEOUT", "300"))
except ValueError:
    DEFAULT_QUERY_TIMEOUT = 300


class RedshiftTimeout(TimeoutError):
    """Raised when a statement is cancelled after exceeding the timeout.

    Carries the statement id and elapsed seconds so callers can surface a
    clear, actionable message instead of a bare stack trace."""

    def __init__(self, statement_id: str, elapsed: float) -> None:
        self.statement_id = statement_id
        self.elapsed = elapsed
        super().__init__(
            f"Redshift statement timed out after {elapsed:.0f}s and was cancelled "
            f"({statement_id}). This is almost always Redshift Serverless cold-start / "
            f"first-time query compilation, not a data-size problem \u2014 the same query "
            f"usually returns in seconds once its plan is cached. Retry in a moment, or "
            f"raise REDSHIFT_QUERY_TIMEOUT."
        )


class RedshiftDataApi:
    def __init__(self) -> None:
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self.workgroup = os.getenv("REDSHIFT_WORKGROUP", "medical-dev-wg")
        self.database = os.getenv("REDSHIFT_DATABASE", "medical_dw")
        self.secret_arn = os.getenv("REDSHIFT_SECRET_ARN")
        if not self.secret_arn:
            raise RuntimeError("REDSHIFT_SECRET_ARN is required to query Redshift")
        self.client = _shared_client(self.region)

    def execute(self, sql: str, timeout_seconds: int | None = None) -> dict[str, Any]:
        if timeout_seconds is None:
            timeout_seconds = DEFAULT_QUERY_TIMEOUT
        response = self.client.execute_statement(
            WorkgroupName=self.workgroup,
            Database=self.database,
            SecretArn=self.secret_arn,
            Sql=sql,
        )
        statement_id = response["Id"]
        started = time.monotonic()
        deadline = started + timeout_seconds
        # Poll quickly at first (most queries finish in <2s), then back off, so
        # a fast query returns fast but a cold one does not busy-wait.
        intervals = itertools.chain([0.25, 0.25, 0.5, 0.5], itertools.repeat(1.0))
        while time.monotonic() < deadline:
            status = self.client.describe_statement(Id=statement_id)
            if status["Status"] == "FINISHED":
                return status
            if status["Status"] in {"FAILED", "ABORTED"}:
                raise RuntimeError(status.get("Error", status["Status"]))
            time.sleep(next(intervals))
        # Timed out: cancel the statement so it does not keep running on Redshift
        # and pile up (accumulated stuck statements saturate the workgroup).
        with suppress(Exception):
            self.client.cancel_statement(Id=statement_id)
        raise RedshiftTimeout(statement_id, time.monotonic() - started)

    def _result_pages(self, statement_id: str):  # type: ignore[no-untyped-def]
        token = None
        while True:
            kwargs = {"Id": statement_id}
            if token is not None:
                kwargs["NextToken"] = token
            result = self.client.get_statement_result(**kwargs)
            yield result
            token = result.get("NextToken")
            if not token:
                return

    def query(self, sql: str, timeout_seconds: int | None = None) -> list[list[Any]]:
        status = self.execute(sql, timeout_seconds=timeout_seconds)
        rows: list[list[Any]] = []
        for result in self._result_pages(status["Id"]):
            for record in result.get("Records", []):
                rows.append([self._field_value(field) for field in record])
        return rows

    @staticmethod
    def _field_value(field: dict[str, Any]) -> Any:
        """Redshift Data API wraps every cell as {typeKey: value}; unwrap it.

        A SQL NULL arrives as {"isNull": true}, which we map to Python None.
        """
        if not field or field.get("isNull"):
            return None
        return next(iter(field.values()))

    def query_records(
        self, sql: str, timeout_seconds: int | None = None
    ) -> dict[str, Any]:
        """Run a query and return all Data API pages as JSON-ready records."""
        status = self.execute(sql, timeout_seconds=timeout_seconds)
        columns: list[str] = []
        rows: list[dict[str, Any]] = []
        for result in self._result_pages(status["Id"]):
            if not columns:
                columns = [col["name"] for col in result.get("ColumnMetadata", [])]
            for record in result.get("Records", []):
                values = [self._field_value(field) for field in record]
                rows.append(dict(zip(columns, values, strict=True)))
        return {"columns": columns, "rows": rows, "row_count": len(rows)}
