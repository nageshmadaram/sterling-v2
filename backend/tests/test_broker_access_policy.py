"""Regression guard must inventory legacy call sites but block new direct access."""
from scripts import audit_broker_access as audit


def test_auditor_records_actual_file_function_and_risk():
    fake = """
async def read(client):
    return await client.get_quote(['NSE:INFY'])

async def danger(client):
    return await client.place_order(symbol='NIFTY', size=1)
"""
    records = audit.scan([("backend/app/services/strategy_x.py", fake)])
    assert len(records) == 2
    assert records[0]["file"] == "backend/app/services/strategy_x.py"
    assert records[0]["function"] == "read"
    assert records[0]["classification"] == "legacy_read_exception"
    assert records[1]["function"] == "danger"
    assert records[1]["classification"] == "legacy_broker_write_exception"
    assert records[1]["risk"] == "high"


def test_new_direct_read_and_write_are_flagged_not_grandfathered():
    baseline = audit.scan([("backend/app/services/test_source.py",
                            "async def old(c):\n    return await c.get_quote([])\n")])
    new = audit.scan([("backend/app/services/test_source.py",
                       "async def old(c):\n    return await c.get_quote([])\n"
                       "async def sneaky(c):\n    return await c.place_order(size=1)\n")])
    new_calls = audit.new_unreviewed(baseline, new)
    assert len(new_calls) == 1
    assert new_calls[0]["method"] == "place_order"


def test_new_provider_sdk_import_or_raw_url_fails_review():
    raw = audit.scan([("backend/app/agents/trade_agent.py",
                       "from kiteconnect import KiteConnect\n"
                       "URL = 'https://api.kite.trade/orders'\n")])
    assert len(raw) == 2
    assert {x["kind"] for x in raw} == {"sdk_import", "provider_http"}
    assert all(x["classification"] == "forbidden_direct_access" for x in raw)
    assert len(audit.new_unreviewed([], raw)) == 2


def test_read_contract_is_approved_only_inside_adapter_facade():
    code = "async def q(client):\n    return await client.get_quote(['NSE:INFY'])\n"
    approved = audit.scan([("backend/app/services/exchanges/normalized_read.py", code)])
    unapproved = audit.scan([("backend/app/api/v1/endpoints/new.py", code)])
    assert approved[0]["classification"] == "approved_normalized_read_service"
    assert unapproved[0]["classification"] == "legacy_route_read_exception"
    assert not audit.new_unreviewed([], approved)
    assert len(audit.new_unreviewed([], unapproved)) == 1


def test_registry_sql_writes_outside_service_flagged():
    code = "def bad():\n    query = 'UPDATE platform_strategies SET state=\\'active\\''\n"
    rows = audit.scan([("backend/app/services/foo.py", code)])
    assert any(r["kind"] == "sql_write" and r["classification"] == "forbidden_direct_access"
               for r in rows)


def test_source_audit_distinguishes_static_url_from_outbound_transport():
    records = audit.scan([
        ("backend/app/core/config.py",
         "API_URL = 'https://api.kite.trade'\n"),
        ("backend/study/broker_probe.py",
         "from kiteconnect import KiteConnect\n"),
        ("backend/scripts/validate_registry_postgres.py",
         "q = 'DELETE FROM platform_strategy_versions'\n"),
    ])
    by_file = {r["file"]: r for r in records}
    assert by_file["backend/app/core/config.py"]["classification"] == "static_provider_configuration"
    assert by_file["backend/study/broker_probe.py"]["classification"] == "legacy_offline_sdk_exception"
    assert by_file["backend/study/broker_probe.py"]["risk"] == "high"
    assert by_file["backend/scripts/validate_registry_postgres.py"]["classification"] == "disposable_database_test_exception"
    assert len(audit.new_unreviewed([], records)) == 3  # even new exceptions require review
