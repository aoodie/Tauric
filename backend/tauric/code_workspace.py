"""Apply exact-context code edits to an immutable workspace, never the running engine."""

import ast
import hashlib
import re

from fastapi import HTTPException


def stage(proposal):
    from .assistant import file_hash, source_path

    if not proposal.patch:
        raise HTTPException(422, "Provide a unified diff to create a code workspace.")
    originals = {}
    for ref in proposal.files:
        path = source_path(ref.path)
        if not ref.sha256 or file_hash(path) != ref.sha256:
            raise HTTPException(
                409, "Read each file's current source checksum before staging changes."
            )
        originals[ref.path] = path.read_text()
    lines = proposal.patch.splitlines(keepends=True)
    index, changed = 0, {}
    while index < len(lines):
        if lines[index].startswith(("diff --git ", "index ")) or not lines[index].strip():
            index += 1
            continue
        if (
            not lines[index].startswith("--- a/")
            or index + 1 >= len(lines)
            or not lines[index + 1].startswith("+++ b/")
        ):
            raise HTTPException(422, "Use ordinary unified diffs for existing declared files.")
        name = lines[index][6:].strip()
        if name != lines[index + 1][6:].strip() or name not in originals or name in changed:
            raise HTTPException(422, "Every patch target must match one declared source file.")
        original = originals[name].splitlines(keepends=True)
        result, cursor, hunks = [], 0, 0
        index += 2
        while index < len(lines) and lines[index].startswith("@@ "):
            header = re.fullmatch(
                r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[^\n]*\n?", lines[index]
            )
            if not header:
                raise HTTPException(422, "Invalid patch hunk header.")
            old_start, old_count = int(header[1]), int(header[2] or 1)
            new_start, new_count = int(header[3]), int(header[4] or 1)
            start = old_start - 1 if old_count else old_start
            if start < cursor or start > len(original):
                raise HTTPException(409, "Patch hunks overlap or exceed the current source.")
            result.extend(original[cursor:start])
            if (new_start - 1 if new_count else new_start) != len(result):
                raise HTTPException(422, "Patch new-line positions are inconsistent.")
            cursor = start
            removed = added = 0
            index += 1
            while index < len(lines) and not lines[index].startswith(
                ("@@ ", "--- a/", "diff --git ")
            ):
                line = lines[index]
                if line.startswith("\\ No newline"):
                    raise HTTPException(
                        422, "Use newline-terminated source files in a staged patch."
                    )
                prefix, content = line[:1], line[1:]
                if prefix not in {" ", "+", "-"}:
                    raise HTTPException(422, "Invalid unified diff line.")
                if prefix != "+":
                    if cursor >= len(original) or original[cursor] != content:
                        raise HTTPException(409, "Patch context differs from the current source.")
                    cursor += 1
                    removed += 1
                if prefix != "-":
                    result.append(content)
                    added += 1
                index += 1
            if removed != old_count or added != new_count:
                raise HTTPException(422, "Patch hunk counts do not match its contents.")
            hunks += 1
        if not hunks:
            raise HTTPException(422, "A staged file needs at least one exact-context hunk.")
        result.extend(original[cursor:])
        content = "".join(result)
        if content == originals[name]:
            raise HTTPException(422, "The staged patch must change the source.")
        if (
            len(content.encode()) > 150000
            or sum(len(v["content"].encode()) for v in changed.values()) + len(content.encode())
            > 300000
        ):
            raise HTTPException(
                422, "Keep the staged workspace below 300 KB with files below 150 KB."
            )
        syntax = "release_validation_required"
        if name.endswith(".py"):
            try:
                ast.parse(content, filename=name)
            except (SyntaxError, ValueError, RecursionError):
                raise HTTPException(
                    422, "The staged Python changes fail syntax validation."
                ) from None
            syntax = "python_syntax_passed"
        changed[name] = {
            "content": content,
            "base_sha256": hashlib.sha256(originals[name].encode()).hexdigest(),
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "syntax_validation": syntax,
        }
    if set(changed) != set(originals):
        raise HTTPException(422, "Each declared file must have an applied patch.")
    return {
        "workspace_files": changed,
        "code_changes_applied": True,
        "application_scope": "isolated_immutable_workspace",
        "runtime_code_changed": False,
        "validation": "exact_source_checksums_and_patch_context_verified",
        "tests_executed": False,
        "approval": "tauric_release_checks_required",
        "required_release_checks": [
            "Source review",
            "Relevant tests and full release checks",
            "Verified immutable images",
            "Recovery backup and deployment verification",
        ],
        "authority": "code_change_workspace; cannot change live policy, credentials, native evidence or the running engine",
    }
