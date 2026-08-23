"""Semantic code search for GitGateway.

Uses Gemini embedding model to generate embeddings for code chunks,
stores them in DynamoDB, and supports natural language search queries.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field

import boto3
import requests as http_requests

from gitgateway.ai import AIConfig

LOG = logging.getLogger(__name__)

TABLE_PREFIX = "gitgateway"
EMBEDDING_MODEL = "text-embedding-004"


class SearchError(Exception):
    """Raised when a search operation fails."""


@dataclass
class CodeChunk:
    """A chunk of code with its embedding.

    Attributes:
        file_path: Path to the file.
        chunk_index: Index of the chunk within the file.
        content: The code content.
        embedding: The embedding vector.
        group: Repository group.
        repo: Repository name.
    """

    file_path: str = ""
    chunk_index: int = 0
    content: str = ""
    embedding: list[float] = field(default_factory=list)
    group: str = ""
    repo: str = ""

    def to_dict(self) -> dict:
        """Serialize to dictionary for DynamoDB.

        Returns:
            Dictionary representation.
        """
        return {
            "file_path": self.file_path,
            "chunk_index": self.chunk_index,
            "content": self.content,
            "embedding": json.dumps(self.embedding),
            "group": self.group,
            "repo": self.repo,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CodeChunk:
        """Deserialize from DynamoDB dictionary.

        Args:
            data: Dictionary from DynamoDB.

        Returns:
            A CodeChunk instance.
        """
        embedding_raw = data.get("embedding", "[]")
        if isinstance(embedding_raw, str):
            embedding = json.loads(embedding_raw)
        else:
            embedding = list(embedding_raw)

        return cls(
            file_path=data.get("file_path", ""),
            chunk_index=int(data.get("chunk_index", 0)),
            content=data.get("content", ""),
            embedding=embedding,
            group=data.get("group", ""),
            repo=data.get("repo", ""),
        )


@dataclass
class SearchResult:
    """A search result with relevance score.

    Attributes:
        file_path: Path to the matched file.
        content: The matched code chunk.
        score: Similarity score (0.0-1.0).
    """

    file_path: str = ""
    content: str = ""
    score: float = 0.0

    def to_dict(self) -> dict:
        """Serialize to dictionary.

        Returns:
            Dictionary representation.
        """
        return {
            "file_path": self.file_path,
            "content": self.content,
            "score": self.score,
        }


def _get_table(dynamodb_resource: object = None):
    """Get the embeddings DynamoDB table.

    Args:
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        A DynamoDB Table resource.
    """
    if dynamodb_resource is None:
        dynamodb_resource = boto3.resource("dynamodb")
    return dynamodb_resource.Table(f"{TABLE_PREFIX}-embeddings")


def generate_embedding(
    text: str,
    config: AIConfig,
) -> list[float]:
    """Generate an embedding vector for text using Gemini.

    Args:
        text: The text to embed.
        config: AI configuration with API key.

    Returns:
        The embedding vector as a list of floats.

    Raises:
        SearchError: If the embedding request fails.
    """
    if not config.api_key:
        raise SearchError("Gemini API key not configured")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{EMBEDDING_MODEL}:embedContent?key={config.api_key}"
    )

    payload = {
        "content": {"parts": [{"text": text}]},
    }

    try:
        response = http_requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
    except http_requests.RequestException as exc:
        raise SearchError(f"Embedding request failed: {exc}") from exc

    data = response.json()
    embedding = data.get("embedding", {}).get("values", [])
    if not embedding:
        raise SearchError("No embedding returned from API")

    return embedding


def store_chunk(
    chunk: CodeChunk,
    dynamodb_resource: object = None,
) -> None:
    """Store a code chunk with its embedding in DynamoDB.

    Args:
        chunk: The CodeChunk to store.
        dynamodb_resource: Optional boto3 DynamoDB resource.
    """
    table = _get_table(dynamodb_resource)
    table.put_item(
        Item={
            "PK": f"REPO#{chunk.group}/{chunk.repo}",
            "SK": f"FILE#{chunk.file_path}#{chunk.chunk_index}",
            **chunk.to_dict(),
        },
    )


def get_repo_chunks(
    group: str,
    repo: str,
    dynamodb_resource: object = None,
) -> list[CodeChunk]:
    """Get all indexed code chunks for a repository.

    Args:
        group: The group name.
        repo: The repository name.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        List of CodeChunk objects.
    """
    table = _get_table(dynamodb_resource)
    response = table.query(
        KeyConditionExpression=(
            "PK = :pk AND begins_with(SK, :prefix)"
        ),
        ExpressionAttributeValues={
            ":pk": f"REPO#{group}/{repo}",
            ":prefix": "FILE#",
        },
    )
    return [
        CodeChunk.from_dict(item)
        for item in response.get("Items", [])
    ]


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        vec_a: First vector.
        vec_b: Second vector.

    Returns:
        Cosine similarity score (-1.0 to 1.0).
    """
    if len(vec_a) != len(vec_b) or not vec_a:
        return 0.0

    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    magnitude_a = math.sqrt(sum(a * a for a in vec_a))
    magnitude_b = math.sqrt(sum(b * b for b in vec_b))

    if magnitude_a == 0.0 or magnitude_b == 0.0:
        return 0.0

    return dot_product / (magnitude_a * magnitude_b)


def search_code(
    group: str,
    repo: str,
    query_embedding: list[float],
    top_k: int = 5,
    dynamodb_resource: object = None,
) -> list[SearchResult]:
    """Search code chunks by embedding similarity.

    Args:
        group: The group name.
        repo: The repository name.
        query_embedding: The query embedding vector.
        top_k: Number of results to return.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        List of SearchResult objects sorted by score (descending).
    """
    chunks = get_repo_chunks(group, repo, dynamodb_resource)

    scored: list[tuple[float, CodeChunk]] = []
    for chunk in chunks:
        if not chunk.embedding:
            continue
        score = cosine_similarity(query_embedding, chunk.embedding)
        scored.append((score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)

    return [
        SearchResult(
            file_path=chunk.file_path,
            content=chunk.content,
            score=score,
        )
        for score, chunk in scored[:top_k]
    ]
