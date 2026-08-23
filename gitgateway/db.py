"""DynamoDB data access layer for GitGateway.

Provides CRUD operations for repos, groups, and access control
entries backed by DynamoDB tables.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import boto3

from gitgateway.models import AccessEntry, AccessLevel, BranchProtection, Group, Repo

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table

LOG = logging.getLogger(__name__)

TABLE_PREFIX = "gitgateway"


class DatabaseError(Exception):
    """Raised when a database operation fails."""


class NotFoundError(DatabaseError):
    """Raised when a requested resource is not found."""


class ConflictError(DatabaseError):
    """Raised when a resource already exists."""


def _get_table(table_suffix: str, dynamodb_resource: object = None) -> Table:
    """Get a DynamoDB table resource.

    Args:
        table_suffix: The suffix of the table name (e.g., 'repos').
        dynamodb_resource: Optional boto3 DynamoDB resource for testing.

    Returns:
        A DynamoDB Table resource.
    """
    if dynamodb_resource is None:
        dynamodb_resource = boto3.resource("dynamodb")
    table_name = f"{TABLE_PREFIX}-{table_suffix}"
    return dynamodb_resource.Table(table_name)


# --- Group Operations ---


def create_group(
    group: Group,
    dynamodb_resource: object = None,
) -> Group:
    """Create a new group.

    Args:
        group: The Group to create.
        dynamodb_resource: Optional boto3 DynamoDB resource for testing.

    Returns:
        The created Group.

    Raises:
        ConflictError: If the group already exists.
    """
    table = _get_table("repos", dynamodb_resource)
    try:
        table.put_item(
            Item={
                "PK": f"GROUP#{group.name}",
                "SK": "METADATA",
                **group.to_dict(),
            },
            ConditionExpression="attribute_not_exists(PK)",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException as exc:
        raise ConflictError(
            f"Group '{group.name}' already exists",
        ) from exc

    LOG.info("Created group: %s", group.name)
    return group


def get_group(
    name: str,
    dynamodb_resource: object = None,
) -> Group:
    """Get a group by name.

    Args:
        name: The group name.
        dynamodb_resource: Optional boto3 DynamoDB resource for testing.

    Returns:
        The Group.

    Raises:
        NotFoundError: If the group does not exist.
    """
    table = _get_table("repos", dynamodb_resource)
    response = table.get_item(
        Key={"PK": f"GROUP#{name}", "SK": "METADATA"},
    )
    item = response.get("Item")
    if not item:
        raise NotFoundError(f"Group '{name}' not found")
    return Group.from_dict(item)


def list_groups(dynamodb_resource: object = None) -> list[Group]:
    """List all groups.

    Args:
        dynamodb_resource: Optional boto3 DynamoDB resource for testing.

    Returns:
        List of all groups.
    """
    table = _get_table("repos", dynamodb_resource)
    response = table.scan(
        FilterExpression="SK = :sk",
        ExpressionAttributeValues={":sk": "METADATA"},
    )
    items = response.get("Items", [])
    return [Group.from_dict(item) for item in items]


def delete_group(
    name: str,
    dynamodb_resource: object = None,
) -> None:
    """Delete a group.

    Args:
        name: The group name.
        dynamodb_resource: Optional boto3 DynamoDB resource for testing.

    Raises:
        NotFoundError: If the group does not exist.
    """
    # Verify it exists first
    get_group(name, dynamodb_resource)
    table = _get_table("repos", dynamodb_resource)
    table.delete_item(
        Key={"PK": f"GROUP#{name}", "SK": "METADATA"},
    )
    LOG.info("Deleted group: %s", name)


# --- Repo Operations ---


def create_repo(
    repo: Repo,
    dynamodb_resource: object = None,
) -> Repo:
    """Create a new repository.

    Args:
        repo: The Repo to create.
        dynamodb_resource: Optional boto3 DynamoDB resource for testing.

    Returns:
        The created Repo.

    Raises:
        ConflictError: If the repository already exists.
    """
    table = _get_table("repos", dynamodb_resource)
    try:
        table.put_item(
            Item={
                "PK": f"GROUP#{repo.group}",
                "SK": f"REPO#{repo.name}",
                **repo.to_dict(),
            },
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException as exc:
        raise ConflictError(
            f"Repository '{repo.full_name}' already exists",
        ) from exc

    LOG.info("Created repo: %s", repo.full_name)
    return repo


def get_repo(
    group: str,
    name: str,
    dynamodb_resource: object = None,
) -> Repo:
    """Get a repository by group and name.

    Args:
        group: The group name.
        name: The repository name.
        dynamodb_resource: Optional boto3 DynamoDB resource for testing.

    Returns:
        The Repo.

    Raises:
        NotFoundError: If the repository does not exist.
    """
    table = _get_table("repos", dynamodb_resource)
    response = table.get_item(
        Key={"PK": f"GROUP#{group}", "SK": f"REPO#{name}"},
    )
    item = response.get("Item")
    if not item:
        raise NotFoundError(f"Repository '{group}/{name}' not found")
    return Repo.from_dict(item)


def list_repos(
    group: str | None = None,
    dynamodb_resource: object = None,
) -> list[Repo]:
    """List repositories, optionally filtered by group.

    Args:
        group: Optional group name to filter by.
        dynamodb_resource: Optional boto3 DynamoDB resource for testing.

    Returns:
        List of repositories.
    """
    table = _get_table("repos", dynamodb_resource)
    if group:
        response = table.query(
            KeyConditionExpression=(
                "PK = :pk AND begins_with(SK, :sk_prefix)"
            ),
            ExpressionAttributeValues={
                ":pk": f"GROUP#{group}",
                ":sk_prefix": "REPO#",
            },
        )
    else:
        response = table.scan(
            FilterExpression="begins_with(SK, :sk_prefix)",
            ExpressionAttributeValues={":sk_prefix": "REPO#"},
        )
    items = response.get("Items", [])
    return [Repo.from_dict(item) for item in items]


def delete_repo(
    group: str,
    name: str,
    dynamodb_resource: object = None,
) -> None:
    """Delete a repository.

    Args:
        group: The group name.
        name: The repository name.
        dynamodb_resource: Optional boto3 DynamoDB resource for testing.

    Raises:
        NotFoundError: If the repository does not exist.
    """
    # Verify it exists
    get_repo(group, name, dynamodb_resource)
    table = _get_table("repos", dynamodb_resource)
    table.delete_item(
        Key={"PK": f"GROUP#{group}", "SK": f"REPO#{name}"},
    )
    LOG.info("Deleted repo: %s/%s", group, name)


def update_repo(
    repo: Repo,
    dynamodb_resource: object = None,
) -> Repo:
    """Update a repository's metadata.

    Args:
        repo: The Repo with updated fields.
        dynamodb_resource: Optional boto3 DynamoDB resource for testing.

    Returns:
        The updated Repo.

    Raises:
        NotFoundError: If the repository does not exist.
    """
    # Verify it exists
    get_repo(repo.group, repo.name, dynamodb_resource)
    table = _get_table("repos", dynamodb_resource)
    table.put_item(
        Item={
            "PK": f"GROUP#{repo.group}",
            "SK": f"REPO#{repo.name}",
            **repo.to_dict(),
        },
    )
    LOG.info("Updated repo: %s", repo.full_name)
    return repo


# --- Access Control Operations ---


def set_access(
    entry: AccessEntry,
    dynamodb_resource: object = None,
) -> AccessEntry:
    """Set access for a user on a resource (group or repo).

    Args:
        entry: The AccessEntry to set.
        dynamodb_resource: Optional boto3 DynamoDB resource for testing.

    Returns:
        The created/updated AccessEntry.
    """
    table = _get_table("access", dynamodb_resource)
    sk_value = (
        f"REPO#{entry.resource}"
        if entry.is_repo_level
        else f"GROUP#{entry.resource}"
    )
    table.put_item(
        Item={
            "PK": f"USER#{entry.username}",
            "SK": sk_value,
            **entry.to_dict(),
        },
    )
    LOG.info(
        "Set access: %s -> %s = %s",
        entry.username,
        entry.resource,
        entry.level.value,
    )
    return entry


def get_access(
    username: str,
    resource: str,
    dynamodb_resource: object = None,
) -> AccessEntry | None:
    """Get access entry for a user on a specific resource.

    Args:
        username: The username.
        resource: The resource path (group or group/repo).
        dynamodb_resource: Optional boto3 DynamoDB resource for testing.

    Returns:
        The AccessEntry, or None if not found.
    """
    table = _get_table("access", dynamodb_resource)
    is_repo = "/" in resource
    sk_value = f"REPO#{resource}" if is_repo else f"GROUP#{resource}"
    response = table.get_item(
        Key={"PK": f"USER#{username}", "SK": sk_value},
    )
    item = response.get("Item")
    if not item:
        return None
    return AccessEntry.from_dict(item)


def revoke_access(
    username: str,
    resource: str,
    dynamodb_resource: object = None,
) -> None:
    """Revoke access for a user on a resource.

    Args:
        username: The username.
        resource: The resource path (group or group/repo).
        dynamodb_resource: Optional boto3 DynamoDB resource for testing.
    """
    table = _get_table("access", dynamodb_resource)
    is_repo = "/" in resource
    sk_value = f"REPO#{resource}" if is_repo else f"GROUP#{resource}"
    table.delete_item(
        Key={"PK": f"USER#{username}", "SK": sk_value},
    )
    LOG.info("Revoked access: %s -> %s", username, resource)


def list_user_access(
    username: str,
    dynamodb_resource: object = None,
) -> list[AccessEntry]:
    """List all access entries for a user.

    Args:
        username: The username.
        dynamodb_resource: Optional boto3 DynamoDB resource for testing.

    Returns:
        List of AccessEntry objects.
    """
    table = _get_table("access", dynamodb_resource)
    response = table.query(
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={":pk": f"USER#{username}"},
    )
    items = response.get("Items", [])
    return [AccessEntry.from_dict(item) for item in items]


def check_permission(
    username: str,
    group: str,
    repo: str | None = None,
    required_level: AccessLevel = AccessLevel.READONLY,
    dynamodb_resource: object = None,
) -> bool:
    """Check if a user has the required permission level.

    Access resolution:
    - Repo-level access overrides group-level access.
    - If both exist, the most permissive wins.

    Args:
        username: The username to check.
        group: The group name.
        repo: Optional repository name within the group.
        required_level: The minimum access level needed.
        dynamodb_resource: Optional boto3 DynamoDB resource for testing.

    Returns:
        True if the user has sufficient permission.
    """
    # Get group-level access
    group_access = get_access(username, group, dynamodb_resource)

    # Get repo-level access if repo is specified
    repo_access = None
    if repo:
        repo_access = get_access(
            username, f"{group}/{repo}", dynamodb_resource,
        )

    # Determine effective access level
    effective_level = None

    if repo_access:
        effective_level = repo_access.level
    if group_access:
        if effective_level is None:
            effective_level = group_access.level
        elif group_access.level > effective_level:
            effective_level = group_access.level

    if effective_level is None:
        return False

    return effective_level >= required_level


# --- Branch Protection Operations ---


def set_branch_protection(
    group: str,
    repo_name: str,
    protection: BranchProtection,
    dynamodb_resource: object = None,
) -> Repo:
    """Set branch protection rules for a repository.

    Args:
        group: The group name.
        repo_name: The repository name.
        protection: The BranchProtection rules to set.
        dynamodb_resource: Optional boto3 DynamoDB resource for testing.

    Returns:
        The updated Repo.

    Raises:
        NotFoundError: If the repository does not exist.
    """
    repo = get_repo(group, repo_name, dynamodb_resource)

    # Replace existing protection for the same branch or add new
    updated_protections = [
        bp for bp in repo.branch_protections
        if bp.branch != protection.branch
    ]
    updated_protections.append(protection)
    repo.branch_protections = updated_protections

    return update_repo(repo, dynamodb_resource)


def get_branch_protection(
    group: str,
    repo_name: str,
    branch: str,
    dynamodb_resource: object = None,
) -> BranchProtection | None:
    """Get branch protection rules for a specific branch.

    Args:
        group: The group name.
        repo_name: The repository name.
        branch: The branch name.
        dynamodb_resource: Optional boto3 DynamoDB resource for testing.

    Returns:
        The BranchProtection rules, or None if no protection is set.

    Raises:
        NotFoundError: If the repository does not exist.
    """
    repo = get_repo(group, repo_name, dynamodb_resource)
    for protection in repo.branch_protections:
        if protection.branch == branch:
            return protection
    return None


def remove_branch_protection(
    group: str,
    repo_name: str,
    branch: str,
    dynamodb_resource: object = None,
) -> Repo:
    """Remove branch protection rules for a specific branch.

    Args:
        group: The group name.
        repo_name: The repository name.
        branch: The branch name.
        dynamodb_resource: Optional boto3 DynamoDB resource for testing.

    Returns:
        The updated Repo.

    Raises:
        NotFoundError: If the repository does not exist.
    """
    repo = get_repo(group, repo_name, dynamodb_resource)
    repo.branch_protections = [
        bp for bp in repo.branch_protections
        if bp.branch != branch
    ]
    return update_repo(repo, dynamodb_resource)
