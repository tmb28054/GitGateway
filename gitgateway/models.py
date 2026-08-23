"""Data models for GitGateway.

Defines dataclasses for all domain entities stored in DynamoDB.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AccessLevel(Enum):
    """Access levels for group and repository permissions."""

    READONLY = "readonly"
    CONTRIBUTOR = "contributor"
    ADMIN = "admin"

    def __ge__(self, other: object) -> bool:
        """Compare access levels by privilege."""
        if not isinstance(other, AccessLevel):
            return NotImplemented
        order = [AccessLevel.READONLY, AccessLevel.CONTRIBUTOR, AccessLevel.ADMIN]
        return order.index(self) >= order.index(other)

    def __gt__(self, other: object) -> bool:
        """Compare access levels by privilege."""
        if not isinstance(other, AccessLevel):
            return NotImplemented
        order = [AccessLevel.READONLY, AccessLevel.CONTRIBUTOR, AccessLevel.ADMIN]
        return order.index(self) > order.index(other)

    def __le__(self, other: object) -> bool:
        """Compare access levels by privilege."""
        if not isinstance(other, AccessLevel):
            return NotImplemented
        order = [AccessLevel.READONLY, AccessLevel.CONTRIBUTOR, AccessLevel.ADMIN]
        return order.index(self) <= order.index(other)

    def __lt__(self, other: object) -> bool:
        """Compare access levels by privilege."""
        if not isinstance(other, AccessLevel):
            return NotImplemented
        order = [AccessLevel.READONLY, AccessLevel.CONTRIBUTOR, AccessLevel.ADMIN]
        return order.index(self) < order.index(other)


@dataclass
class BranchProtection:
    """Branch protection rules for a repository.

    Attributes:
        branch: The branch name this protection applies to.
        required_approvals: Number of approvals required before merge.
        force_push_allowed: Whether force-push is permitted.
        require_linear_history: Whether linear history is enforced.
        required_status_checks: List of status check names that must pass.
    """

    branch: str = "main"
    required_approvals: int = 0
    force_push_allowed: bool = False
    require_linear_history: bool = False
    required_status_checks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to a dictionary for DynamoDB storage.

        Returns:
            Dictionary representation of the branch protection.
        """
        return {
            "branch": self.branch,
            "required_approvals": self.required_approvals,
            "force_push_allowed": self.force_push_allowed,
            "require_linear_history": self.require_linear_history,
            "required_status_checks": self.required_status_checks,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BranchProtection:
        """Deserialize from a DynamoDB dictionary.

        Args:
            data: Dictionary from DynamoDB.

        Returns:
            A BranchProtection instance.
        """
        return cls(
            branch=data.get("branch", "main"),
            required_approvals=int(data.get("required_approvals", 0)),
            force_push_allowed=bool(data.get("force_push_allowed", False)),
            require_linear_history=bool(
                data.get("require_linear_history", False),
            ),
            required_status_checks=list(
                data.get("required_status_checks", []),
            ),
        )


@dataclass
class Group:
    """A project group that repositories belong to.

    Attributes:
        name: The group identifier.
        description: Optional description of the group.
        created_by: Username of the group creator.
    """

    name: str
    description: str = ""
    created_by: str = ""

    def to_dict(self) -> dict:
        """Serialize to a dictionary for DynamoDB storage.

        Returns:
            Dictionary representation of the group.
        """
        return {
            "name": self.name,
            "description": self.description,
            "created_by": self.created_by,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Group:
        """Deserialize from a DynamoDB dictionary.

        Args:
            data: Dictionary from DynamoDB.

        Returns:
            A Group instance.
        """
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            created_by=data.get("created_by", ""),
        )


@dataclass
class Repo:
    """A Git repository.

    Attributes:
        group: The group this repository belongs to.
        name: The repository name.
        description: Optional description.
        default_branch: The default branch name.
        created_by: Username of the repo creator.
        ai_enabled: Whether AI features are enabled.
        sast_enabled: Whether SAST scanning is enabled.
        fork_of: If this is a fork, the source as 'group/repo'.
        branch_protections: List of branch protection rules.
    """

    group: str
    name: str
    description: str = ""
    default_branch: str = "main"
    created_by: str = ""
    ai_enabled: bool = False
    sast_enabled: bool = True
    fork_of: str = ""
    branch_protections: list[BranchProtection] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        """Return the full repository name as group/name."""
        return f"{self.group}/{self.name}"

    def to_dict(self) -> dict:
        """Serialize to a dictionary for DynamoDB storage.

        Returns:
            Dictionary representation of the repo.
        """
        return {
            "group": self.group,
            "name": self.name,
            "description": self.description,
            "default_branch": self.default_branch,
            "created_by": self.created_by,
            "ai_enabled": self.ai_enabled,
            "sast_enabled": self.sast_enabled,
            "fork_of": self.fork_of,
            "branch_protections": [
                bp.to_dict() for bp in self.branch_protections
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Repo:
        """Deserialize from a DynamoDB dictionary.

        Args:
            data: Dictionary from DynamoDB.

        Returns:
            A Repo instance.
        """
        return cls(
            group=data.get("group", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            default_branch=data.get("default_branch", "main"),
            created_by=data.get("created_by", ""),
            ai_enabled=bool(data.get("ai_enabled", False)),
            sast_enabled=bool(data.get("sast_enabled", True)),
            fork_of=data.get("fork_of", ""),
            branch_protections=[
                BranchProtection.from_dict(bp)
                for bp in data.get("branch_protections", [])
            ],
        )


@dataclass
class AccessEntry:
    """An access control entry mapping a user to a resource.

    Attributes:
        username: The user this access applies to.
        resource: The resource path (group name or group/repo).
        level: The access level granted.
    """

    username: str
    resource: str
    level: AccessLevel

    @property
    def is_repo_level(self) -> bool:
        """Return True if this is a repo-level access entry."""
        return "/" in self.resource

    @property
    def group_name(self) -> str:
        """Return the group name from the resource."""
        return self.resource.split("/")[0]

    def to_dict(self) -> dict:
        """Serialize to a dictionary for DynamoDB storage.

        Returns:
            Dictionary representation of the access entry.
        """
        return {
            "username": self.username,
            "resource": self.resource,
            "level": self.level.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AccessEntry:
        """Deserialize from a DynamoDB dictionary.

        Args:
            data: Dictionary from DynamoDB.

        Returns:
            An AccessEntry instance.
        """
        return cls(
            username=data.get("username", ""),
            resource=data.get("resource", ""),
            level=AccessLevel(data.get("level", "readonly")),
        )
