from orchestrator.idempotency import idempotency_key


def test_key_is_stable():
    a = idempotency_key("REQ-001", "dev", 1, ["h1", "h2"])
    b = idempotency_key("REQ-001", "dev", 1, ["h1", "h2"])
    assert a == b and len(a) == 64


def test_input_order_does_not_matter():
    a = idempotency_key("REQ-001", "dev", 1, ["h1", "h2"])
    b = idempotency_key("REQ-001", "dev", 1, ["h2", "h1"])
    assert a == b


def test_revision_changes_key():
    a = idempotency_key("REQ-001", "dev", 1, ["h1"])
    b = idempotency_key("REQ-001", "dev", 2, ["h1"])
    assert a != b


def test_agent_changes_key():
    assert idempotency_key("REQ-001", "qa", 1, ["h1"]) != idempotency_key("REQ-001", "dev", 1, ["h1"])
