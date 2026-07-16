from __future__ import annotations

from datetime import datetime, timezone

from app.models import ContextCreate


class FakeFetcher:
    def fetch(self, url: str):
        from app.services.safe_source_fetcher import FetchedPage

        return FetchedPage(
            url=url,
            title="ICML 2026 Official Schedule",
            publisher="ICML",
            text="ICML 2026 takes place in Seoul. The official schedule is published here.",
            content_type="text/html",
            retrieved_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
            bytes_read=100,
            cache_allowed=True,
        )


def test_discovery_persists_original_page_with_freshness(isolated_home):
    from app.db import connect, init_db
    from app.services import contexts, trip_acquisition
    from app.services.source_acquisition import SourceAcquisitionOrchestrator
    from app.services.source_queries import PublicTripFields, generate_public_trip_queries
    from app.services.source_search import FakeSearchProvider, SearchHit

    init_db()
    conn = connect()
    trip = contexts.create(
        conn,
        ContextCreate(name="ICML 2026", context_type="conference"),
    )
    brief = {"event": "ICML 2026", "destination": "Seoul"}
    import json

    conn.execute(
        "UPDATE contexts SET trip_brief=? WHERE context_id=?",
        (json.dumps(brief), trip["context_id"]),
    )
    queries = generate_public_trip_queries(
        PublicTripFields(event="ICML 2026", destination="Seoul")
    ).queries
    provider = FakeSearchProvider(
        {
            query: [
                SearchHit(
                    url="https://icml.cc/Conferences/2026",
                    title="ICML 2026",
                    publisher="ICML",
                )
            ]
            for query in queries
        }
    )
    result = trip_acquisition.discover_trip(
        conn,
        trip["context_id"],
        orchestrator=SourceAcquisitionOrchestrator(
            provider=provider, fetcher=FakeFetcher()
        ),
    )
    assert result["sources"]
    source = contexts.list_sources(conn, trip["context_id"])[0]
    assert source["publisher"] == "ICML"
    assert source["freshness_class"] == "event_current"
    assert source["retrieved_at"] is not None
    assert source["content"].startswith("ICML 2026 takes place")
    assert conn.execute("SELECT COUNT(*) AS n FROM search_runs").fetchone()["n"] == 1
    conn.close()
