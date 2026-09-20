def test_list_contexts_defaults(client, auth_headers):
    res = client.get("/v1/contexts", headers=auth_headers(["fees.gateway", "tutoring.payments"]))
    assert res.status_code == 200
    items = res.json()["items"]
    keys = {item["contextKey"] for item in items}
    assert "school_invoice" in keys
    assert "tutoring_session" in keys
    invoice = next(i for i in items if i["contextKey"] == "school_invoice")
    assert invoice["enabled"] is True
    assert invoice["featuresSatisfied"] is True


def test_school_cannot_mutate_payment_context(client, auth_headers):
    admin_headers = auth_headers(["fees.gateway"], roles=["director"])
    res = client.patch(
        "/v1/contexts/school_invoice",
        headers=admin_headers,
        json={"enabled": False},
    )
    assert res.status_code == 403
    assert "control platform" in res.json()["detail"].lower()


def test_school_cannot_mutate_gateway(client, auth_headers):
    admin_headers = auth_headers(["fees.gateway"], roles=["bursar"])
    res = client.patch(
        "/v1/gateways/paystack",
        headers=admin_headers,
        json={"enabled": False},
    )
    assert res.status_code == 403
    assert "control platform" in res.json()["detail"].lower()
