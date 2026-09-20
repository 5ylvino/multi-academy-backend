from app.providers.pinecone.client import PineconeClient


def test_namespace_for_tenant() -> None:
    client = PineconeClient()
    assert client.namespace_for("abc-123") == "tenant:abc-123"
