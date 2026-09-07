"""Tests for gitgateway.db module."""

from __future__ import annotations

from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from gitgateway.db import (
    ConflictError,
    NotFoundError,
    _delete_all_items_with_pk,
    _delete_items_with_pk_prefix,
    _get_table,
    check_permission,
    create_group,
    create_repo,
    delete_group,
    delete_repo,
    get_access,
    get_branch_protection,
    get_group,
    get_repo,
    list_groups,
    list_repos,
    list_user_access,
    remove_branch_protection,
    revoke_access,
    set_access,
    set_branch_protection,
    update_repo,
)
from gitgateway.models import (
    AccessEntry,
    AccessLevel,
    BranchProtection,
    Group,
    Repo,
)


@pytest.fixture
def dynamodb():
    """Create mocked DynamoDB tables."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")

        # Create repos table
        resource.create_table(
            TableName="gitgateway-repos",
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

        # Create access table
        resource.create_table(
            TableName="gitgateway-access",
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


class TestGroupOperations:
    """Tests for group CRUD operations."""

    def test_create_group(self, dynamodb) -> None:
        """Creates a group successfully."""
        group = Group(name="myteam", description="Team", created_by="alice")
        result = create_group(group, dynamodb)
        assert result.name == "myteam"

    def test_create_duplicate_group_raises(self, dynamodb) -> None:
        """Raises ConflictError on duplicate group."""
        group = Group(name="myteam")
        create_group(group, dynamodb)
        with pytest.raises(ConflictError, match="already exists"):
            create_group(group, dynamodb)

    def test_get_group(self, dynamodb) -> None:
        """Gets an existing group."""
        create_group(Group(name="devs", description="Developers"), dynamodb)
        result = get_group("devs", dynamodb)
        assert result.name == "devs"
        assert result.description == "Developers"

    def test_get_nonexistent_group_raises(self, dynamodb) -> None:
        """Raises NotFoundError for missing group."""
        with pytest.raises(NotFoundError, match="not found"):
            get_group("nonexistent", dynamodb)

    def test_list_groups(self, dynamodb) -> None:
        """Lists all groups."""
        create_group(Group(name="team-a"), dynamodb)
        create_group(Group(name="team-b"), dynamodb)
        groups = list_groups(dynamodb)
        names = {g.name for g in groups}
        assert names == {"team-a", "team-b"}

    def test_list_groups_empty(self, dynamodb) -> None:
        """Returns empty list when no groups exist."""
        groups = list_groups(dynamodb)
        assert groups == []

    def test_delete_group(self, dynamodb) -> None:
        """Deletes an existing group."""
        create_group(Group(name="old-team"), dynamodb)
        delete_group("old-team", dynamodb)
        with pytest.raises(NotFoundError):
            get_group("old-team", dynamodb)

    def test_delete_nonexistent_group_raises(self, dynamodb) -> None:
        """Raises NotFoundError when deleting missing group."""
        with pytest.raises(NotFoundError):
            delete_group("ghost", dynamodb)


class TestRepoOperations:
    """Tests for repository CRUD operations."""

    def test_create_repo(self, dynamodb) -> None:
        """Creates a repository successfully."""
        repo = Repo(group="team", name="app", created_by="alice")
        result = create_repo(repo, dynamodb)
        assert result.full_name == "team/app"

    def test_create_duplicate_repo_raises(self, dynamodb) -> None:
        """Raises ConflictError on duplicate repo."""
        repo = Repo(group="team", name="app")
        create_repo(repo, dynamodb)
        with pytest.raises(ConflictError, match="already exists"):
            create_repo(repo, dynamodb)

    def test_get_repo(self, dynamodb) -> None:
        """Gets an existing repository."""
        create_repo(
            Repo(group="team", name="api", description="Backend"),
            dynamodb,
        )
        result = get_repo("team", "api", dynamodb)
        assert result.name == "api"
        assert result.description == "Backend"

    def test_get_nonexistent_repo_raises(self, dynamodb) -> None:
        """Raises NotFoundError for missing repo."""
        with pytest.raises(NotFoundError, match="not found"):
            get_repo("team", "nope", dynamodb)

    def test_list_repos_by_group(self, dynamodb) -> None:
        """Lists repos filtered by group."""
        create_repo(Repo(group="team", name="app1"), dynamodb)
        create_repo(Repo(group="team", name="app2"), dynamodb)
        create_repo(Repo(group="other", name="thing"), dynamodb)

        repos = list_repos("team", dynamodb)
        names = {r.name for r in repos}
        assert names == {"app1", "app2"}

    def test_list_repos_all(self, dynamodb) -> None:
        """Lists all repos across groups."""
        create_repo(Repo(group="team", name="app"), dynamodb)
        create_repo(Repo(group="other", name="lib"), dynamodb)

        repos = list_repos(None, dynamodb)
        names = {r.full_name for r in repos}
        assert names == {"team/app", "other/lib"}

    def test_list_repos_empty(self, dynamodb) -> None:
        """Returns empty list when no repos exist."""
        repos = list_repos("team", dynamodb)
        assert repos == []

    def test_delete_repo(self, dynamodb) -> None:
        """Deletes an existing repository."""
        create_repo(Repo(group="team", name="old"), dynamodb)
        delete_repo("team", "old", dynamodb)
        with pytest.raises(NotFoundError):
            get_repo("team", "old", dynamodb)

    def test_delete_nonexistent_repo_raises(self, dynamodb) -> None:
        """Raises NotFoundError when deleting missing repo."""
        with pytest.raises(NotFoundError):
            delete_repo("team", "ghost", dynamodb)

    def test_update_repo(self, dynamodb) -> None:
        """Updates a repository's metadata."""
        create_repo(
            Repo(group="team", name="app", description="v1"),
            dynamodb,
        )
        repo = get_repo("team", "app", dynamodb)
        repo.description = "v2"
        repo.ai_enabled = True
        update_repo(repo, dynamodb)

        updated = get_repo("team", "app", dynamodb)
        assert updated.description == "v2"
        assert updated.ai_enabled is True

    def test_update_nonexistent_repo_raises(self, dynamodb) -> None:
        """Raises NotFoundError when updating missing repo."""
        with pytest.raises(NotFoundError):
            update_repo(Repo(group="team", name="nope"), dynamodb)


class TestAccessOperations:
    """Tests for access control operations."""

    def test_set_access_group_level(self, dynamodb) -> None:
        """Sets group-level access."""
        entry = AccessEntry(
            username="alice",
            resource="team",
            level=AccessLevel.CONTRIBUTOR,
        )
        result = set_access(entry, dynamodb)
        assert result.level == AccessLevel.CONTRIBUTOR

    def test_set_access_repo_level(self, dynamodb) -> None:
        """Sets repo-level access."""
        entry = AccessEntry(
            username="bob",
            resource="team/app",
            level=AccessLevel.ADMIN,
        )
        set_access(entry, dynamodb)
        result = get_access("bob", "team/app", dynamodb)
        assert result is not None
        assert result.level == AccessLevel.ADMIN

    def test_get_access_nonexistent(self, dynamodb) -> None:
        """Returns None when access entry doesn't exist."""
        result = get_access("nobody", "team", dynamodb)
        assert result is None

    def test_revoke_access(self, dynamodb) -> None:
        """Revokes access for a user."""
        entry = AccessEntry(
            username="alice",
            resource="team",
            level=AccessLevel.ADMIN,
        )
        set_access(entry, dynamodb)
        revoke_access("alice", "team", dynamodb)
        result = get_access("alice", "team", dynamodb)
        assert result is None

    def test_revoke_repo_access(self, dynamodb) -> None:
        """Revokes repo-level access."""
        entry = AccessEntry(
            username="alice",
            resource="team/repo",
            level=AccessLevel.CONTRIBUTOR,
        )
        set_access(entry, dynamodb)
        revoke_access("alice", "team/repo", dynamodb)
        result = get_access("alice", "team/repo", dynamodb)
        assert result is None

    def test_list_user_access(self, dynamodb) -> None:
        """Lists all access entries for a user."""
        set_access(
            AccessEntry("alice", "team", AccessLevel.ADMIN),
            dynamodb,
        )
        set_access(
            AccessEntry("alice", "other/repo", AccessLevel.READONLY),
            dynamodb,
        )
        entries = list_user_access("alice", dynamodb)
        assert len(entries) == 2
        resources = {e.resource for e in entries}
        assert "team" in resources
        assert "other/repo" in resources

    def test_list_user_access_empty(self, dynamodb) -> None:
        """Returns empty list for user with no access."""
        entries = list_user_access("nobody", dynamodb)
        assert entries == []

    def test_overwrite_access(self, dynamodb) -> None:
        """Overwriting access updates the level."""
        set_access(
            AccessEntry("alice", "team", AccessLevel.READONLY),
            dynamodb,
        )
        set_access(
            AccessEntry("alice", "team", AccessLevel.ADMIN),
            dynamodb,
        )
        result = get_access("alice", "team", dynamodb)
        assert result is not None
        assert result.level == AccessLevel.ADMIN


class TestCheckPermission:
    """Tests for permission checking logic."""

    def test_no_access_returns_false(self, dynamodb) -> None:
        """User with no access entries is denied."""
        assert check_permission("alice", "team", "app", dynamodb_resource=dynamodb) is False

    def test_group_access_grants_repo_permission(self, dynamodb) -> None:
        """Group-level access grants access to repos in that group."""
        set_access(
            AccessEntry("alice", "team", AccessLevel.CONTRIBUTOR),
            dynamodb,
        )
        assert check_permission(
            "alice", "team", "app",
            required_level=AccessLevel.CONTRIBUTOR,
            dynamodb_resource=dynamodb,
        ) is True

    def test_group_access_insufficient_level(self, dynamodb) -> None:
        """Group access below required level is denied."""
        set_access(
            AccessEntry("alice", "team", AccessLevel.READONLY),
            dynamodb,
        )
        assert check_permission(
            "alice", "team", "app",
            required_level=AccessLevel.CONTRIBUTOR,
            dynamodb_resource=dynamodb,
        ) is False

    def test_repo_access_overrides_group(self, dynamodb) -> None:
        """Repo-level access is considered alongside group-level."""
        set_access(
            AccessEntry("alice", "team", AccessLevel.READONLY),
            dynamodb,
        )
        set_access(
            AccessEntry("alice", "team/app", AccessLevel.ADMIN),
            dynamodb,
        )
        assert check_permission(
            "alice", "team", "app",
            required_level=AccessLevel.ADMIN,
            dynamodb_resource=dynamodb,
        ) is True

    def test_most_permissive_wins(self, dynamodb) -> None:
        """The most permissive level between group and repo wins."""
        set_access(
            AccessEntry("alice", "team", AccessLevel.ADMIN),
            dynamodb,
        )
        set_access(
            AccessEntry("alice", "team/app", AccessLevel.READONLY),
            dynamodb,
        )
        # Admin at group level should win
        assert check_permission(
            "alice", "team", "app",
            required_level=AccessLevel.ADMIN,
            dynamodb_resource=dynamodb,
        ) is True

    def test_group_only_access_no_repo_specified(self, dynamodb) -> None:
        """Group access check without specifying a repo."""
        set_access(
            AccessEntry("alice", "team", AccessLevel.CONTRIBUTOR),
            dynamodb,
        )
        assert check_permission(
            "alice", "team",
            required_level=AccessLevel.READONLY,
            dynamodb_resource=dynamodb,
        ) is True

    def test_group_only_access_insufficient(self, dynamodb) -> None:
        """Group access below required with no repo specified."""
        set_access(
            AccessEntry("alice", "team", AccessLevel.READONLY),
            dynamodb,
        )
        assert check_permission(
            "alice", "team",
            required_level=AccessLevel.ADMIN,
            dynamodb_resource=dynamodb,
        ) is False


class TestBranchProtectionOperations:
    """Tests for branch protection CRUD."""

    def test_set_branch_protection(self, dynamodb) -> None:
        """Sets protection on a branch."""
        create_repo(Repo(group="team", name="app"), dynamodb)
        protection = BranchProtection(
            branch="main",
            required_approvals=2,
            required_status_checks=["ci"],
        )
        repo = set_branch_protection("team", "app", protection, dynamodb)
        assert len(repo.branch_protections) == 1
        assert repo.branch_protections[0].required_approvals == 2

    def test_update_existing_protection(self, dynamodb) -> None:
        """Replaces protection for the same branch."""
        create_repo(Repo(group="team", name="app"), dynamodb)
        set_branch_protection(
            "team", "app",
            BranchProtection(branch="main", required_approvals=1),
            dynamodb,
        )
        repo = set_branch_protection(
            "team", "app",
            BranchProtection(branch="main", required_approvals=3),
            dynamodb,
        )
        assert len(repo.branch_protections) == 1
        assert repo.branch_protections[0].required_approvals == 3

    def test_multiple_branches_protected(self, dynamodb) -> None:
        """Can protect multiple branches independently."""
        create_repo(Repo(group="team", name="app"), dynamodb)
        set_branch_protection(
            "team", "app",
            BranchProtection(branch="main"),
            dynamodb,
        )
        repo = set_branch_protection(
            "team", "app",
            BranchProtection(branch="develop"),
            dynamodb,
        )
        assert len(repo.branch_protections) == 2

    def test_get_branch_protection(self, dynamodb) -> None:
        """Gets protection for a specific branch."""
        create_repo(Repo(group="team", name="app"), dynamodb)
        set_branch_protection(
            "team", "app",
            BranchProtection(branch="main", required_approvals=2),
            dynamodb,
        )
        result = get_branch_protection("team", "app", "main", dynamodb)
        assert result is not None
        assert result.required_approvals == 2

    def test_get_branch_protection_nonexistent(self, dynamodb) -> None:
        """Returns None for unprotected branch."""
        create_repo(Repo(group="team", name="app"), dynamodb)
        result = get_branch_protection("team", "app", "main", dynamodb)
        assert result is None

    def test_get_branch_protection_repo_not_found(self, dynamodb) -> None:
        """Raises NotFoundError when repo doesn't exist."""
        with pytest.raises(NotFoundError):
            get_branch_protection("team", "nope", "main", dynamodb)

    def test_remove_branch_protection(self, dynamodb) -> None:
        """Removes protection from a branch."""
        create_repo(Repo(group="team", name="app"), dynamodb)
        set_branch_protection(
            "team", "app",
            BranchProtection(branch="main"),
            dynamodb,
        )
        repo = remove_branch_protection("team", "app", "main", dynamodb)
        assert len(repo.branch_protections) == 0

    def test_remove_nonexistent_protection(self, dynamodb) -> None:
        """Removing non-existent protection is a no-op."""
        create_repo(Repo(group="team", name="app"), dynamodb)
        repo = remove_branch_protection("team", "app", "main", dynamodb)
        assert len(repo.branch_protections) == 0

    def test_set_protection_on_missing_repo_raises(self, dynamodb) -> None:
        """Raises NotFoundError when repo doesn't exist."""
        with pytest.raises(NotFoundError):
            set_branch_protection(
                "team", "nope",
                BranchProtection(branch="main"),
                dynamodb,
            )

    def test_remove_protection_on_missing_repo_raises(self, dynamodb) -> None:
        """Raises NotFoundError when repo doesn't exist."""
        with pytest.raises(NotFoundError):
            remove_branch_protection("team", "nope", "main", dynamodb)


class TestGetTable:
    """Tests for _get_table helper."""

    @mock_aws
    def test_uses_default_boto3_resource(self) -> None:
        """Falls back to boto3.resource when no resource provided."""
        # Create table in mocked AWS
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="gitgateway-repos",
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
        # Call without passing a resource — should use boto3 default
        table = _get_table("repos")
        assert table.table_name == "gitgateway-repos"


    def test_get_branch_protection_different_branch(self, dynamodb) -> None:
        """Returns None when repo has protections but not for the requested branch."""
        create_repo(Repo(group="team", name="app"), dynamodb)
        set_branch_protection(
            "team", "app",
            BranchProtection(branch="develop", required_approvals=1),
            dynamodb,
        )
        # Request protection for 'main' which doesn't exist
        result = get_branch_protection("team", "app", "main", dynamodb)
        assert result is None


class TestDeleteHelpers:
    """Tests for the private delete-loop helpers."""

    def test_delete_all_items_with_pk(self, dynamodb) -> None:
        """Deletes every item under a partition key."""
        table = dynamodb.Table("gitgateway-repos")
        table.put_item(Item={"PK": "REPO#team/app", "SK": "EVENT#1"})
        table.put_item(Item={"PK": "REPO#team/app", "SK": "EVENT#2"})

        _delete_all_items_with_pk(table, "REPO#team/app")

        remaining = table.query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": "REPO#team/app"},
        )["Items"]
        assert remaining == []

    def test_delete_items_with_pk_prefix(self, dynamodb) -> None:
        """Deletes items whose PK starts with a prefix."""
        table = dynamodb.Table("gitgateway-repos")
        table.put_item(Item={"PK": "TREE#team/app#main", "SK": "FILE#a"})
        table.put_item(Item={"PK": "TREE#team/app#feat", "SK": "FILE#b"})

        _delete_items_with_pk_prefix(table, "TREE#team/app")

        leftover = table.scan(
            FilterExpression="begins_with(PK, :p)",
            ExpressionAttributeValues={":p": "TREE#team/app"},
        )["Items"]
        assert leftover == []


class TestDeleteRepoTreeCleanup:
    """Cover the tree-cache cleanup branch of delete_repo."""

    def test_delete_repo_removes_tree_cache(self, dynamodb) -> None:
        """delete_repo also clears TREE# entries for the repo."""
        create_group(Group(name="team"), dynamodb)
        create_repo(Repo(group="team", name="app"), dynamodb)
        table = dynamodb.Table("gitgateway-repos")
        table.put_item(Item={
            "PK": "TREE#team/app#main", "SK": "FILE#README.md",
        })

        delete_repo("team", "app", dynamodb)

        leftover = table.scan(
            FilterExpression="begins_with(PK, :p)",
            ExpressionAttributeValues={":p": "TREE#team/app"},
        )["Items"]
        assert leftover == []

    def test_delete_repo_tree_cleanup_failure_ignored(self, dynamodb) -> None:
        """A failure clearing the tree cache does not fail delete_repo."""
        create_group(Group(name="team"), dynamodb)
        create_repo(Repo(group="team", name="app"), dynamodb)

        with patch(
            "gitgateway.db._delete_items_with_pk_prefix",
            side_effect=RuntimeError("boom"),
        ):
            # Should complete without raising despite the cleanup failure.
            delete_repo("team", "app", dynamodb)

        with pytest.raises(NotFoundError):
            get_repo("team", "app", dynamodb)


class TestListPagination:
    """Cover the LastEvaluatedKey pagination loops in list_* functions."""

    @staticmethod
    def _paging_resource(pages: list[dict], op: str):
        """Build a fake resource whose table.<op> returns paged results.

        Args:
            pages: Sequence of response dicts to return in order.
            op: Either 'query' or 'scan'.
        """
        from unittest.mock import MagicMock

        table = MagicMock()
        getattr(table, op).side_effect = pages
        resource = MagicMock()
        resource.Table.return_value = table
        return resource

    def test_list_groups_paginates(self) -> None:
        """list_groups follows LastEvaluatedKey across pages."""
        pages = [
            {
                "Items": [{"PK": "GROUP#a", "SK": "METADATA", "name": "a"}],
                "LastEvaluatedKey": {"PK": "GROUP#a", "SK": "METADATA"},
            },
            {
                "Items": [{"PK": "GROUP#b", "SK": "METADATA", "name": "b"}],
            },
        ]
        resource = self._paging_resource(pages, "scan")
        groups = list_groups(resource)
        assert {g.name for g in groups} == {"a", "b"}

    def test_list_repos_group_paginates(self) -> None:
        """list_repos(group=...) follows LastEvaluatedKey via query."""
        pages = [
            {
                "Items": [{
                    "PK": "GROUP#team", "SK": "REPO#one", "name": "one",
                    "group": "team",
                }],
                "LastEvaluatedKey": {"PK": "GROUP#team", "SK": "REPO#one"},
            },
            {
                "Items": [{
                    "PK": "GROUP#team", "SK": "REPO#two", "name": "two",
                    "group": "team",
                }],
            },
        ]
        resource = self._paging_resource(pages, "query")
        repos = list_repos("team", resource)
        assert {r.name for r in repos} == {"one", "two"}

    def test_list_repos_all_paginates(self) -> None:
        """list_repos() (no group) follows LastEvaluatedKey via scan."""
        pages = [
            {
                "Items": [{
                    "PK": "GROUP#team", "SK": "REPO#one", "name": "one",
                    "group": "team",
                }],
                "LastEvaluatedKey": {"PK": "GROUP#team", "SK": "REPO#one"},
            },
            {
                "Items": [{
                    "PK": "GROUP#other", "SK": "REPO#two", "name": "two",
                    "group": "other",
                }],
            },
        ]
        resource = self._paging_resource(pages, "scan")
        repos = list_repos(None, resource)
        assert {r.name for r in repos} == {"one", "two"}
