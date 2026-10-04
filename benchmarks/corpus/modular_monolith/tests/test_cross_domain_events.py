from monolith.app import MonolithApplication


def test_full_checkout_event_flow():
    app = MonolithApplication()
    acc = app.identity.register_account("shopper@example.com", "Jane Doe")
    assert acc.id.startswith("acc-")

    result = app.checkout_order(acc.id, "prod-1", 2)
    assert result["status"] == "COMPLETED"
    assert result["amount"] == 259.2  # (120 * 2) * 1.08 = 259.2

    # Check notification dispatch
    assert any(
        "Jane Doe" in msg or "shopper@example.com" in msg for msg in app.notifications.sent_emails
    )
    # Check shipping queue
    assert len(app.shipping.shipments) > 0
