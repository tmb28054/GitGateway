"""Dulwich-based Git Smart HTTP backend for GitGateway.

Pure Python implementation of the Git Smart HTTP protocol using
Dulwich. Handles info/refs, git-upload-pack, and git-receive-pack
without requiring a git binary.
"""
# pylint: disable=too-many-locals,too-many-branches,too-many-statements
# pylint: disable=too-many-positional-arguments,unused-import,unused-argument
# pylint: disable=unused-variable

from __future__ import annotations

import io
import logging
import os  # noqa: F401
import tempfile
from pathlib import Path

from dulwich.objects import Blob, Commit, Tree  # noqa: F401
from dulwich.repo import Repo
from dulwich.server import (  # noqa: F401
    DictBackend,
    ReceivePackHandler,
    UploadPackHandler,
)
from dulwich.web import (  # noqa: F401
    HTTPGitApplication,
    get_info_refs,
    handle_service_request,
)

from gitgateway.storage import (
    sync_repo_to_local,
    sync_repo_to_s3,
    repo_exists,
)

LOG = logging.getLogger(__name__)

DEFAULT_BUCKET = "gitgateway-repos"


class GitBackendError(Exception):
    """Raised when a git backend operation fails."""


def handle_git_http_request(
    method: str,
    path_info: str,
    query_string: str,
    body: bytes,
    group: str,
    repo_name: str,
    content_type: str = "",
    bucket: str = DEFAULT_BUCKET,
) -> tuple[int, str, bytes]:
    """Handle a Git Smart HTTP request using Dulwich.

    Syncs the bare repo from S3, handles the git protocol request,
    then syncs any changes back to S3.

    Args:
        method: HTTP method (GET or POST).
        path_info: Path after the repo (e.g., /info/refs).
        query_string: Query string.
        body: Request body bytes.
        group: Repository group.
        repo_name: Repository name.
        content_type: Request Content-Type.
        bucket: S3 bucket name.

    Returns:
        Tuple of (status_code, content_type, response_body).
    """
    local_base = Path(tempfile.gettempdir()) / "gitgateway"
    local_base.mkdir(parents=True, exist_ok=True)
    local_path = local_base / group / f"{repo_name}.git"

    # Sync repo from S3 or init fresh
    try:
        if repo_exists(group, repo_name, bucket=bucket):
            sync_repo_to_local(
                group, repo_name, bucket=bucket, local_base=local_base,
            )
            # Verify Dulwich can open it
            Repo(str(local_path))
        else:
            # Init a new bare repo with Dulwich directly
            local_path.mkdir(parents=True, exist_ok=True)
            Repo.init_bare(str(local_path))
            # Sync the new bare repo to S3
            sync_repo_to_s3(local_path, group, repo_name, bucket=bucket)
    except Exception as exc:  # pylint: disable=broad-except
        # If sync failed or repo corrupt, init fresh
        LOG.warning("Repo init/sync issue, reinitializing: %s", exc)
        if local_path.exists():
            import shutil  # pylint: disable=import-outside-toplevel
            shutil.rmtree(local_path)
        local_path.mkdir(parents=True, exist_ok=True)
        Repo.init_bare(str(local_path))
        sync_repo_to_s3(local_path, group, repo_name, bucket=bucket)

    # Determine service
    service = ""
    if query_string:
        for param in query_string.split("&"):
            if param.startswith("service="):
                service = param[8:]
                break

    is_receive = (
        service == "git-receive-pack"
        or "git-receive-pack" in path_info
    )

    try:
        if "/info/refs" in path_info:
            status, ctype, resp_body = _handle_info_refs(
                local_path, service,
            )
        elif "git-upload-pack" in path_info:
            status, ctype, resp_body = _handle_upload_pack(
                local_path, body,
            )
        elif "git-receive-pack" in path_info:
            status, ctype, resp_body = _handle_receive_pack(
                local_path, body,
            )
        else:
            return 404, "text/plain", b"Not found"
    except Exception as exc:  # pylint: disable=broad-except
        LOG.error("Git backend error: %s", exc)
        return 500, "text/plain", f"Internal error: {exc}".encode()

    # Sync back to S3 after receive-pack (push)
    if is_receive and status == 200:
        try:
            sync_repo_to_s3(local_path, group, repo_name, bucket=bucket)
        except Exception as exc:  # pylint: disable=broad-except
            LOG.error("Failed to sync after push: %s", exc)

        # Index repo tree into DynamoDB for the web UI
        try:
            _index_repo_tree(local_path, group, repo_name)
        except Exception as exc:  # pylint: disable=broad-except
            LOG.error("Failed to index repo tree: %s", exc)

    return status, ctype, resp_body


def _handle_info_refs(
    repo_path: Path, service: str,
) -> tuple[int, str, bytes]:
    """Handle GET /info/refs?service=<service>.

    Args:
        repo_path: Path to bare repo.
        service: The git service name.

    Returns:
        Tuple of (status, content_type, body).
    """
    repo = Repo(str(repo_path))

    if service == "git-upload-pack":
        ctype = "application/x-git-upload-pack-advertisement"
    elif service == "git-receive-pack":
        ctype = "application/x-git-receive-pack-advertisement"
    else:
        return 400, "text/plain", b"Invalid service"

    # Build pkt-line response
    output = io.BytesIO()

    # Service announcement
    line = f"# service={service}\n"
    pkt = f"{len(line) + 4:04x}{line}".encode()
    output.write(pkt)
    output.write(b"0000")  # flush

    # List refs
    refs = repo.get_refs()
    capabilities = b"report-status delete-refs ofs-delta side-band-64k"

    first = True
    for ref_name, sha in sorted(refs.items()):
        if isinstance(ref_name, str):
            ref_name = ref_name.encode()
        if isinstance(sha, str):
            sha = sha.encode()

        if first:
            line_data = sha + b" " + ref_name + b"\x00" + capabilities + b"\n"
            first = False
        else:
            line_data = sha + b" " + ref_name + b"\n"

        pkt_line = f"{len(line_data) + 4:04x}".encode() + line_data
        output.write(pkt_line)

    if first:
        # Empty repo — send capabilities with zero-id
        zero_id = b"0" * 40
        line_data = (
            zero_id + b" capabilities^{}\x00" + capabilities + b"\n"
        )
        pkt_line = f"{len(line_data) + 4:04x}".encode() + line_data
        output.write(pkt_line)

    output.write(b"0000")  # flush

    return 200, ctype, output.getvalue()


def _handle_upload_pack(
    repo_path: Path, body: bytes,
) -> tuple[int, str, bytes]:
    """Handle POST /git-upload-pack (fetch/clone).

    Args:
        repo_path: Path to bare repo.
        body: Request body with wants/haves.

    Returns:
        Tuple of (status, content_type, body).
    """
    from dulwich.pack import (  # pylint: disable=import-outside-toplevel,unused-import
        write_pack_data,
    )
    from dulwich.object_store import (  # pylint: disable=import-outside-toplevel,unused-import
        MemoryObjectStore,
    )

    repo = Repo(str(repo_path))

    # Parse wants from request
    wants = []
    lines = body.split(b"\n")
    for line in lines:
        line = line.strip()
        if not line or line == b"0000":
            continue
        # Strip pkt-line length prefix (4 hex chars)
        if len(line) > 4 and line[:4].isalnum():
            line = line[4:]
        if line.startswith(b"want "):
            sha = line[5:45].decode()
            wants.append(sha.encode())
        elif line == b"done":
            break

    if not wants:
        # Client just wants refs (already handled by info/refs)
        return 200, "application/x-git-upload-pack-result", b"0000"

    # Generate pack with requested objects
    output = io.BytesIO()
    # NAK
    output.write(b"0008NAK\n")

    # Get all objects reachable from wants
    object_store = repo.object_store

    try:
        pack_data = io.BytesIO()
        objects_to_send = []
        for want_sha in wants:
            try:
                obj = object_store[want_sha]
                objects_to_send.append(obj)
                # Walk the tree for commits
                if obj.type_name == b"commit":
                    _collect_objects(object_store, obj, objects_to_send)
            except KeyError:
                continue

        # Deduplicate objects by SHA — the same object can be reached
        # from multiple wants or re-added while walking, and a pack must
        # not contain the same object twice.
        seen_shas: set[bytes] = set()
        unique_objects = []
        for obj in objects_to_send:
            sha = obj.id
            if sha in seen_shas:
                continue
            seen_shas.add(sha)
            unique_objects.append(obj)
        objects_to_send = unique_objects

        if objects_to_send:
            # Write pack
            entries = [
                (obj.type_name, obj.as_raw_string())
                for obj in objects_to_send
            ]
            from dulwich.pack import (  # pylint: disable=import-outside-toplevel,unused-import
                write_pack_header, write_pack_object,
                compute_file_sha,
            )
            import struct  # pylint: disable=import-outside-toplevel
            import hashlib  # pylint: disable=import-outside-toplevel

            num_objects = len(entries)
            # Pack header
            pack_data.write(b"PACK")
            pack_data.write(struct.pack(">I", 2))  # version
            pack_data.write(struct.pack(">I", num_objects))

            for type_name, raw in entries:
                type_num = {
                    b"commit": 1, b"tree": 2,
                    b"blob": 3, b"tag": 4,
                }[type_name]
                _write_pack_object_simple(pack_data, type_num, raw)

            # Pack checksum
            pack_data.seek(0)
            sha = hashlib.sha1(pack_data.read()).digest()  # nosec
            pack_data.write(sha)

            # Sideband pack data
            pack_data.seek(0)
            pack_bytes = pack_data.read()

            # Send as sideband channel 1
            chunk_size = 65515
            for i in range(0, len(pack_bytes), chunk_size):
                chunk = pack_bytes[i:i + chunk_size]
                sideband = b"\x01" + chunk
                pkt = f"{len(sideband) + 4:04x}".encode() + sideband
                output.write(pkt)

        output.write(b"0000")  # flush
    except Exception as exc:  # pylint: disable=broad-except
        LOG.error("upload-pack error: %s", exc)
        return 200, "application/x-git-upload-pack-result", b"0008NAK\n0000"

    return 200, "application/x-git-upload-pack-result", output.getvalue()


def _handle_receive_pack(
    repo_path: Path, body: bytes,
) -> tuple[int, str, bytes]:
    """Handle POST /git-receive-pack (push).

    Args:
        repo_path: Path to bare repo.
        body: Request body with ref updates and pack data.

    Returns:
        Tuple of (status, content_type, body).
    """
    from dulwich.pack import (  # pylint: disable=import-outside-toplevel,unused-import
        PackData, Pack,
    )

    repo = Repo(str(repo_path))

    # Parse the request: ref updates followed by pack data
    # Format: pkt-lines with "old new refname" then PACK data
    ref_updates = []
    pack_start = 0

    i = 0
    while i < len(body):
        # Read pkt-line length
        if i + 4 > len(body):
            break
        length_str = body[i:i + 4]
        try:
            length = int(length_str, 16)
        except ValueError:
            break

        if length == 0:
            i += 4
            break  # flush — pack data follows

        line = body[i + 4:i + length]
        i += length

        # Parse ref update line
        line = line.strip(b"\n")
        # Remove capabilities from first line
        if b"\x00" in line:
            line = line.split(b"\x00")[0]

        parts = line.split(b" ", 2)
        if len(parts) == 3:
            old_sha, new_sha, ref_name = parts
            ref_updates.append((
                old_sha.decode(),
                new_sha.decode(),
                ref_name.decode(),
            ))

    # The rest is pack data
    pack_data = body[i:]

    # Apply pack if present
    if pack_data and pack_data[:4] == b"PACK":
        try:
            # Write pack to repo
            pack_file = io.BytesIO(pack_data)
            repo.object_store.add_thin_pack(pack_file.read, None)
        except Exception as exc:  # pylint: disable=broad-except
            LOG.error("Failed to apply pack: %s", exc)
            # Return error report
            output = _build_report_status(
                ref_updates, error=str(exc),
            )
            return (
                200, "application/x-git-receive-pack-result", output,
            )

    # Apply ref updates
    for old_sha, new_sha, ref_name in ref_updates:
        zero = "0" * 40
        if new_sha == zero:
            # Delete ref
            if isinstance(ref_name, str):
                ref_name_bytes = ref_name.encode()
            else:
                ref_name_bytes = ref_name
            del repo.refs[ref_name_bytes]
        else:
            if isinstance(ref_name, str):
                ref_name_bytes = ref_name.encode()
            else:
                ref_name_bytes = ref_name
            if isinstance(new_sha, str):
                new_sha_bytes = new_sha.encode()
            else:
                new_sha_bytes = new_sha
            repo.refs[ref_name_bytes] = new_sha_bytes

    # Build success report
    output = _build_report_status(ref_updates)
    return 200, "application/x-git-receive-pack-result", output


def _build_report_status(
    ref_updates: list[tuple[str, str, str]],
    error: str = "",
) -> bytes:
    """Build a receive-pack report-status response.

    Args:
        ref_updates: List of (old, new, refname) tuples.
        error: Optional error message.

    Returns:
        Pkt-line encoded report status wrapped in sideband.
    """
    # Build the status lines
    status_lines = io.BytesIO()

    if error:
        line = f"unpack {error}\n".encode()
    else:
        line = b"unpack ok\n"
    status_lines.write(f"{len(line) + 4:04x}".encode() + line)

    for _, _, ref_name in ref_updates:
        if error:
            line = f"ng {ref_name} {error}\n".encode()
        else:
            line = f"ok {ref_name}\n".encode()
        status_lines.write(f"{len(line) + 4:04x}".encode() + line)

    status_lines.write(b"0000")

    # Wrap in sideband channel 1
    output = io.BytesIO()
    data = status_lines.getvalue()
    sideband = b"\x01" + data
    pkt = f"{len(sideband) + 4:04x}".encode() + sideband
    output.write(pkt)
    output.write(b"0000")

    return output.getvalue()


def _collect_objects(object_store, commit_obj, objects_list):
    """Recursively collect objects from a commit.

    Args:
        object_store: The repo's object store.
        commit_obj: A commit object.
        objects_list: List to append objects to.
    """
    seen = {o.id for o in objects_list}

    # Add tree
    tree_sha = commit_obj.tree
    if tree_sha not in seen:
        try:
            tree = object_store[tree_sha]
            objects_list.append(tree)
            seen.add(tree_sha)
            _collect_tree_objects(object_store, tree, objects_list, seen)
        except KeyError:
            pass

    # Walk parents (limited depth for performance)
    for parent_sha in commit_obj.parents[:5]:
        if parent_sha not in seen:
            try:
                parent = object_store[parent_sha]
                objects_list.append(parent)
                seen.add(parent_sha)
                _collect_objects(object_store, parent, objects_list)
            except KeyError:
                pass


def _collect_tree_objects(object_store, tree_obj, objects_list, seen):
    """Collect blob and sub-tree objects from a tree.

    Args:
        object_store: The repo's object store.
        tree_obj: A tree object.
        objects_list: List to append to.
        seen: Set of already-seen object IDs.
    """
    for item in tree_obj.items():
        sha = item.sha
        if sha in seen:
            continue
        seen.add(sha)
        try:
            obj = object_store[sha]
            objects_list.append(obj)
            if obj.type_name == b"tree":
                _collect_tree_objects(
                    object_store, obj, objects_list, seen,
                )
        except KeyError:
            pass


def _write_pack_object_simple(f, type_num, data):
    """Write a pack object in the simplest format.

    Args:
        f: File-like object to write to.
        type_num: Git object type number (1-4).
        data: Raw uncompressed object data.
    """
    import zlib  # pylint: disable=import-outside-toplevel

    size = len(data)
    # First byte: type (3 bits) + size (4 bits)
    c = (type_num << 4) | (size & 0x0F)
    size >>= 4
    while size:
        c |= 0x80
        f.write(bytes([c]))
        c = size & 0x7F
        size >>= 7
    f.write(bytes([c]))
    # Compressed data
    f.write(zlib.compress(data))


def _index_repo_tree(repo_path: Path, group: str, repo_name: str) -> None:
    """Index the repository tree into DynamoDB for the web UI.

    Walks the tree at each branch HEAD and writes file entries
    to the gitgateway-repos table with TREE# prefix.

    Args:
        repo_path: Path to the bare repo.
        group: Group name.
        repo_name: Repository name.
    """
    import boto3  # pylint: disable=import-outside-toplevel

    repo = Repo(str(repo_path))
    table = boto3.resource("dynamodb").Table("gitgateway-repos")

    refs = repo.get_refs()
    for ref_name, sha in refs.items():
        if isinstance(ref_name, bytes):
            ref_name = ref_name.decode()
        if isinstance(sha, bytes):
            sha = sha.decode()

        # Only index branches (refs/heads/*)
        if not ref_name.startswith("refs/heads/"):
            continue

        branch = ref_name[len("refs/heads/"):]

        # Register branch
        table.put_item(Item={
            "PK": f"BRANCHES#{group}/{repo_name}",
            "SK": f"BRANCH#{branch}",
            "branch_name": branch,
        })

        # Get commit and tree
        try:
            commit_obj = repo.object_store[sha.encode()]
            if commit_obj.type_name != b"commit":
                continue
            commit_message = commit_obj.message.decode(
                errors="replace",
            ).split("\n")[0]
            commit_author = commit_obj.author.decode(
                errors="replace",
            ).split(" <")[0]
        except (KeyError, Exception):  # pylint: disable=broad-except
            continue

        # Walk the tree and index files
        try:
            tree_sha = commit_obj.tree
            _index_tree_recursive(
                repo.object_store, table, group, repo_name, branch,
                tree_sha, "", commit_message, commit_author,
            )
        except Exception as exc:  # pylint: disable=broad-except
            LOG.error("Error indexing tree for %s: %s", branch, exc)

        # Index commit history for this branch
        try:
            _index_commit_history(
                repo.object_store, table, group, repo_name, branch,
                sha.encode(),
            )
        except Exception as exc:  # pylint: disable=broad-except
            LOG.error("Error indexing commits for %s: %s", branch, exc)


def _index_tree_recursive(
    object_store,
    table,
    group: str,
    repo_name: str,
    branch: str,
    tree_sha,
    prefix: str,
    commit_message: str,
    commit_author: str,
) -> None:
    """Recursively index a tree into DynamoDB.

    Args:
        object_store: Dulwich object store.
        table: DynamoDB table resource.
        group: Group name.
        repo_name: Repo name.
        branch: Branch name.
        tree_sha: SHA of the tree object.
        prefix: Current path prefix.
        commit_message: Last commit message (simplified).
        commit_author: Last commit author.
    """
    try:
        tree_obj = object_store[tree_sha]
    except KeyError:
        return

    for item in tree_obj.items():
        name = item.path.decode(errors="replace")
        full_path = f"{prefix}{name}" if not prefix else f"{prefix}/{name}"
        mode = item.mode

        # Determine type: directory (040000) or file
        is_dir = (mode & 0o170000) == 0o040000

        if is_dir:
            # Recurse into subdirectory
            _index_tree_recursive(
                object_store, table, group, repo_name, branch,
                item.sha, full_path, commit_message, commit_author,
            )
        else:
            # Read file content (limit to 1MB for DynamoDB)
            content = ""
            try:
                blob = object_store[item.sha]
                raw = blob.as_raw_string()
                if len(raw) < 1_000_000:
                    content = raw.decode(errors="replace")
            except (KeyError, Exception):  # pylint: disable=broad-except
                pass

            table.put_item(Item={
                "PK": f"TREE#{group}/{repo_name}#{branch}",
                "SK": f"FILE#{full_path}",
                "path": full_path,
                "name": name,
                "type": "file",
                "content": content,
                "last_commit_message": commit_message,
                "last_commit_author": commit_author,
            })


def _index_commit_history(
    object_store,
    table,
    group: str,
    repo_name: str,
    branch: str,
    head_sha: bytes,
    max_commits: int = 200,
) -> None:
    """Walk commit history and store in DynamoDB.

    Stores commits with COMMITS# prefix, sorted by timestamp.

    Args:
        object_store: Dulwich object store.
        table: DynamoDB table resource.
        group: Group name.
        repo_name: Repository name.
        branch: Branch name.
        head_sha: SHA of the HEAD commit.
        max_commits: Maximum commits to index.
    """
    visited = set()
    queue = [head_sha]
    count = 0

    while queue and count < max_commits:
        sha = queue.pop(0)
        if sha in visited:
            continue
        visited.add(sha)

        try:
            commit = object_store[sha]
            if commit.type_name != b"commit":
                continue
        except KeyError:
            continue

        sha_str = sha.decode() if isinstance(sha, bytes) else sha
        message = commit.message.decode(errors="replace").strip()
        subject = message.split("\n")[0]
        author_raw = commit.author.decode(errors="replace")
        author_name = author_raw.split(" <")[0]
        author_email = ""
        if "<" in author_raw and ">" in author_raw:
            author_email = author_raw.split("<")[1].split(">")[0]

        # Use commit time for sort order (zero-padded for lexicographic sort)
        commit_time = commit.commit_time
        time_key = f"{9999999999 - commit_time:010d}"

        table.put_item(Item={
            "PK": f"COMMITS#{group}/{repo_name}#{branch}",
            "SK": f"COMMIT#{time_key}#{sha_str[:12]}",
            "sha": sha_str,
            "message": subject,
            "full_message": message,
            "author": author_name,
            "author_email": author_email,
            "timestamp": int(commit_time),
        })

        count += 1

        # Add parents to queue
        for parent_sha in commit.parents:
            if parent_sha not in visited:
                queue.append(parent_sha)
