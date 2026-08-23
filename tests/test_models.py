"""Tests for gitgateway.models module."""

from __future__ import annotations

from gitgateway.models import AccessEntry, AccessLevel, BranchProtection, Group, Repo


class TestAccessLevel:
    """Tests for AccessLevel enum comparisons."""

    def test_readonly_less_than_contributor(self) -> None:
        """Readonly is less than contributor."""
        assert AccessLevel.READONLY < AccessLevel.CONTRIBUTOR

    def test_contributor_less_than_admin(self) -> None:
        """Contributor is less than admin."""
        assert AccessLevel.CONTRIBUTOR < AccessLevel.ADMIN

    def test_admin_greater_than_readonly(self) -> None:
        """Admin is greater than readonly."""
        assert AccessLevel.ADMIN > AccessLevel.READONLY

    def test_same_level_ge(self) -> None:
        """Same level is greater than or equal."""
        assert AccessLevel.ADMIN >= AccessLevel.ADMIN

    def test_same_level_le(self) -> None:
        """Same level is less than or equal."""
        assert AccessLevel.READONLY <= AccessLevel.READONLY

    def test_not_gt_same(self) -> None:
        """Same level is not strictly greater."""
        assert not (AccessLevel.ADMIN > AccessLevel.ADMIN)

    def test_not_lt_same(self) -> None:
        """Same level is not strictly less."""
        assert not (AccessLevel.ADMIN < AccessLevel.ADMIN)

    def test_ge_returns_not_implemented_for_other_types(self) -> None:
        """Comparison with non-AccessLevel returns NotImplemented."""
        result = AccessLevel.ADMIN.__ge__("not_an_access_level")
        assert result is NotImplemented

    def test_gt_returns_not_implemented_for_other_types(self) -> None:
        """Comparison with non-AccessLevel returns NotImplemented."""
        result = AccessLevel.ADMIN.__gt__(42)
        assert result is NotImplemented

    def test_le_returns_not_implemented_for_other_types(self) -> None:
        """Comparison with non-AccessLevel returns NotImplemented."""
        result = AccessLevel.ADMIN.__le__(None)
        assert result is NotImplemented

    def test_lt_returns_not_implemented_for_other_types(self) -> None:
        """Comparison with non-AccessLevel returns NotImplemented."""
        result = AccessLevel.ADMIN.__lt__([])
        assert result is NotImplemented


class TestBranchProtection:
    """Tests for BranchProtection dataclass."""

    def test_default_values(self) -> None:
        """Default protection has sensible defaults."""
        bp = BranchProtection()
        assert bp.branch == "main"
        assert bp.required_approvals == 0
        assert bp.force_push_allowed is False
        assert bp.require_linear_history is False
        assert bp.required_status_checks == []

    def test_to_dict(self) -> None:
        """Serializes to dictionary correctly."""
        bp = BranchProtection(
            branch="develop",
            required_approvals=2,
            force_push_allowed=True,
            required_status_checks=["ci", "lint"],
        )
        result = bp.to_dict()
        assert result == {
            "branch": "develop",
            "required_approvals": 2,
            "force_push_allowed": True,
            "require_linear_history": False,
            "required_status_checks": ["ci", "lint"],
        }

    def test_from_dict(self) -> None:
        """Deserializes from dictionary correctly."""
        data = {
            "branch": "release",
            "required_approvals": 3,
            "force_push_allowed": False,
            "require_linear_history": True,
            "required_status_checks": ["tests"],
        }
        bp = BranchProtection.from_dict(data)
        assert bp.branch == "release"
        assert bp.required_approvals == 3
        assert bp.require_linear_history is True

    def test_from_dict_with_defaults(self) -> None:
        """Handles missing keys with defaults."""
        bp = BranchProtection.from_dict({})
        assert bp.branch == "main"
        assert bp.required_approvals == 0

    def test_roundtrip(self) -> None:
        """Serialization/deserialization round trip is lossless."""
        bp = BranchProtection(
            branch="main",
            required_approvals=1,
            force_push_allowed=False,
            require_linear_history=True,
            required_status_checks=["sast", "tests"],
        )
        assert BranchProtection.from_dict(bp.to_dict()) == bp


class TestGroup:
    """Tests for Group dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to dictionary correctly."""
        group = Group(name="myteam", description="My team", created_by="alice")
        result = group.to_dict()
        assert result == {
            "name": "myteam",
            "description": "My team",
            "created_by": "alice",
        }

    def test_from_dict(self) -> None:
        """Deserializes from dictionary correctly."""
        data = {"name": "devs", "description": "Developers", "created_by": "bob"}
        group = Group.from_dict(data)
        assert group.name == "devs"
        assert group.description == "Developers"
        assert group.created_by == "bob"

    def test_from_dict_with_defaults(self) -> None:
        """Handles missing keys with defaults."""
        group = Group.from_dict({})
        assert group.name == ""
        assert group.description == ""


class TestRepo:
    """Tests for Repo dataclass."""

    def test_full_name(self) -> None:
        """Full name is group/name."""
        repo = Repo(group="team", name="project")
        assert repo.full_name == "team/project"

    def test_to_dict(self) -> None:
        """Serializes to dictionary correctly."""
        repo = Repo(
            group="team",
            name="app",
            description="An app",
            ai_enabled=True,
            branch_protections=[BranchProtection(branch="main")],
        )
        result = repo.to_dict()
        assert result["group"] == "team"
        assert result["name"] == "app"
        assert result["ai_enabled"] is True
        assert len(result["branch_protections"]) == 1

    def test_from_dict(self) -> None:
        """Deserializes from dictionary correctly."""
        data = {
            "group": "team",
            "name": "api",
            "description": "Backend API",
            "default_branch": "develop",
            "created_by": "charlie",
            "ai_enabled": True,
            "sast_enabled": False,
            "fork_of": "other/repo",
            "branch_protections": [
                {"branch": "main", "required_approvals": 2},
            ],
        }
        repo = Repo.from_dict(data)
        assert repo.group == "team"
        assert repo.name == "api"
        assert repo.default_branch == "develop"
        assert repo.ai_enabled is True
        assert repo.sast_enabled is False
        assert repo.fork_of == "other/repo"
        assert len(repo.branch_protections) == 1
        assert repo.branch_protections[0].required_approvals == 2

    def test_from_dict_with_defaults(self) -> None:
        """Handles missing keys with defaults."""
        repo = Repo.from_dict({})
        assert repo.default_branch == "main"
        assert repo.sast_enabled is True
        assert repo.branch_protections == []


class TestAccessEntry:
    """Tests for AccessEntry dataclass."""

    def test_is_repo_level_true(self) -> None:
        """Detects repo-level resource correctly."""
        entry = AccessEntry(
            username="alice",
            resource="team/project",
            level=AccessLevel.CONTRIBUTOR,
        )
        assert entry.is_repo_level is True

    def test_is_repo_level_false(self) -> None:
        """Detects group-level resource correctly."""
        entry = AccessEntry(
            username="alice",
            resource="team",
            level=AccessLevel.READONLY,
        )
        assert entry.is_repo_level is False

    def test_group_name_from_repo_resource(self) -> None:
        """Extracts group name from repo-level resource."""
        entry = AccessEntry(
            username="bob",
            resource="mygroup/myrepo",
            level=AccessLevel.ADMIN,
        )
        assert entry.group_name == "mygroup"

    def test_group_name_from_group_resource(self) -> None:
        """Extracts group name from group-level resource."""
        entry = AccessEntry(
            username="bob",
            resource="mygroup",
            level=AccessLevel.ADMIN,
        )
        assert entry.group_name == "mygroup"

    def test_to_dict(self) -> None:
        """Serializes to dictionary correctly."""
        entry = AccessEntry(
            username="alice",
            resource="team/repo",
            level=AccessLevel.CONTRIBUTOR,
        )
        result = entry.to_dict()
        assert result == {
            "username": "alice",
            "resource": "team/repo",
            "level": "contributor",
        }

    def test_from_dict(self) -> None:
        """Deserializes from dictionary correctly."""
        data = {
            "username": "bob",
            "resource": "devs",
            "level": "admin",
        }
        entry = AccessEntry.from_dict(data)
        assert entry.username == "bob"
        assert entry.resource == "devs"
        assert entry.level == AccessLevel.ADMIN

    def test_from_dict_defaults(self) -> None:
        """Handles missing keys with defaults."""
        entry = AccessEntry.from_dict({})
        assert entry.username == ""
        assert entry.level == AccessLevel.READONLY
