"""Tests for gitgateway.ai_search module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from gitgateway.ai import AIConfig
from gitgateway.ai_search import (
    CodeChunk,
    SearchError,
    SearchResult,
    _get_table,
    cosine_similarity,
    generate_embedding,
    get_repo_chunks,
    search_code,
    store_chunk,
)


@pytest.fixture
def dynamodb():
    """Create mocked DynamoDB table for embeddings."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="gitgateway-embeddings",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield resource


class TestCodeChunk:
    """Tests for CodeChunk dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to dictionary."""
        chunk = CodeChunk(
            file_path="app.py", chunk_index=0,
            content="def main(): pass",
            embedding=[0.1, 0.2, 0.3],
            group="t", repo="r",
        )
        result = chunk.to_dict()
        assert result["file_path"] == "app.py"
        assert json.loads(result["embedding"]) == [0.1, 0.2, 0.3]

    def test_from_dict_string_embedding(self) -> None:
        """Deserializes embedding from JSON string."""
        data = {
            "file_path": "x.py", "chunk_index": 1,
            "content": "code", "embedding": "[0.5, 0.6]",
        }
        chunk = CodeChunk.from_dict(data)
        assert chunk.embedding == [0.5, 0.6]

    def test_from_dict_list_embedding(self) -> None:
        """Handles embedding already as list."""
        data = {"embedding": [0.1, 0.2]}
        chunk = CodeChunk.from_dict(data)
        assert chunk.embedding == [0.1, 0.2]

    def test_from_dict_defaults(self) -> None:
        """Handles missing keys."""
        chunk = CodeChunk.from_dict({})
        assert chunk.file_path == ""
        assert chunk.embedding == []


class TestSearchResult:
    """Tests for SearchResult dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to dictionary."""
        sr = SearchResult(file_path="a.py", content="code", score=0.95)
        result = sr.to_dict()
        assert result["score"] == 0.95


class TestGetTable:
    """Tests for _get_table helper."""

    @mock_aws
    def test_default_resource(self) -> None:
        """Uses default boto3 resource."""
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="gitgateway-embeddings",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table = _get_table()
        assert table.table_name == "gitgateway-embeddings"


class TestGenerateEmbedding:
    """Tests for generate_embedding function."""

    def test_generates_embedding(self) -> None:
        """Calls API and returns embedding vector."""
        config = AIConfig(api_key="key")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "embedding": {"values": [0.1, 0.2, 0.3]},
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("gitgateway.ai_search.http_requests.post", return_value=mock_resp):
            result = generate_embedding("search query", config)

        assert result == [0.1, 0.2, 0.3]

    def test_no_api_key_raises(self) -> None:
        """Raises when API key is missing."""
        config = AIConfig(api_key="")
        with pytest.raises(SearchError, match="not configured"):
            generate_embedding("query", config)

    def test_request_failure_raises(self) -> None:
        """Raises on HTTP error."""
        config = AIConfig(api_key="key")
        import requests  # pylint: disable=import-outside-toplevel

        with patch(
            "gitgateway.ai_search.http_requests.post",
            side_effect=requests.RequestException("timeout"),
        ):
            with pytest.raises(SearchError, match="failed"):
                generate_embedding("query", config)

    def test_empty_embedding_raises(self) -> None:
        """Raises when API returns no embedding."""
        config = AIConfig(api_key="key")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"embedding": {"values": []}}
        mock_resp.raise_for_status = MagicMock()

        with patch("gitgateway.ai_search.http_requests.post", return_value=mock_resp):
            with pytest.raises(SearchError, match="No embedding"):
                generate_embedding("query", config)


class TestStoreAndGetChunks:
    """Tests for store_chunk and get_repo_chunks."""

    def test_store_and_retrieve(self, dynamodb) -> None:
        """Stores and retrieves code chunks."""
        chunk = CodeChunk(
            file_path="app.py", chunk_index=0,
            content="def main(): pass",
            embedding=[0.1, 0.2],
            group="team", repo="app",
        )
        store_chunk(chunk, dynamodb)

        chunks = get_repo_chunks("team", "app", dynamodb)
        assert len(chunks) == 1
        assert chunks[0].file_path == "app.py"
        assert chunks[0].embedding == [0.1, 0.2]

    def test_empty_repo(self, dynamodb) -> None:
        """Returns empty list for repo with no chunks."""
        chunks = get_repo_chunks("team", "empty", dynamodb)
        assert chunks == []


class TestCosineSimilarity:
    """Tests for cosine_similarity function."""

    def test_identical_vectors(self) -> None:
        """Identical vectors have similarity 1.0."""
        assert cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self) -> None:
        """Orthogonal vectors have similarity 0.0."""
        assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)

    def test_opposite_vectors(self) -> None:
        """Opposite vectors have similarity -1.0."""
        assert cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0)

    def test_different_lengths(self) -> None:
        """Different length vectors return 0.0."""
        assert cosine_similarity([1, 2], [1, 2, 3]) == 0.0

    def test_empty_vectors(self) -> None:
        """Empty vectors return 0.0."""
        assert cosine_similarity([], []) == 0.0

    def test_zero_vector(self) -> None:
        """Zero vector returns 0.0."""
        assert cosine_similarity([0, 0], [1, 1]) == 0.0


class TestSearchCode:
    """Tests for search_code function."""

    def test_returns_sorted_results(self, dynamodb) -> None:
        """Returns results sorted by similarity."""
        # Store chunks with different embeddings
        store_chunk(
            CodeChunk(
                file_path="close.py", chunk_index=0,
                content="very relevant",
                embedding=[0.9, 0.1, 0.0],
                group="t", repo="r",
            ),
            dynamodb,
        )
        store_chunk(
            CodeChunk(
                file_path="far.py", chunk_index=0,
                content="not relevant",
                embedding=[0.0, 0.0, 1.0],
                group="t", repo="r",
            ),
            dynamodb,
        )

        results = search_code(
            "t", "r", [1.0, 0.0, 0.0], top_k=2,
            dynamodb_resource=dynamodb,
        )
        assert len(results) == 2
        assert results[0].file_path == "close.py"
        assert results[0].score > results[1].score

    def test_respects_top_k(self, dynamodb) -> None:
        """Returns at most top_k results."""
        for i in range(5):
            store_chunk(
                CodeChunk(
                    file_path=f"f{i}.py", chunk_index=0,
                    content="code",
                    embedding=[float(i), 0.0],
                    group="t", repo="r",
                ),
                dynamodb,
            )

        results = search_code(
            "t", "r", [1.0, 0.0], top_k=3,
            dynamodb_resource=dynamodb,
        )
        assert len(results) == 3

    def test_skips_empty_embeddings(self, dynamodb) -> None:
        """Skips chunks with empty embeddings."""
        store_chunk(
            CodeChunk(
                file_path="no_emb.py", chunk_index=0,
                content="code", embedding=[],
                group="t", repo="r",
            ),
            dynamodb,
        )
        store_chunk(
            CodeChunk(
                file_path="has_emb.py", chunk_index=0,
                content="code", embedding=[1.0, 0.0],
                group="t", repo="r",
            ),
            dynamodb,
        )

        results = search_code(
            "t", "r", [1.0, 0.0], top_k=5,
            dynamodb_resource=dynamodb,
        )
        assert len(results) == 1
        assert results[0].file_path == "has_emb.py"
