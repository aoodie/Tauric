import json
import math
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .defaults import PANELS, default_layout
from .models import (
    AuditEvent,
    LayoutVersion,
    Owner,
    SettingDefinition,
    SettingHead,
    SettingVersion,
    Workspace,
    identifier,
)
from .schemas import AuditView, ResourceRef, SettingDefinitionView, SettingView, WorkspaceView


def audit(db, owner, action, resource_type, resource_id, reason, details):
    event = AuditEvent(
        owner_id=owner.id,
        actor=owner.username,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        reason=reason,
        details=details,
    )
    db.add(event)
    return event


def definition(db: Session, key: str) -> SettingDefinitionView:
    stored = db.get(SettingDefinition, key)
    if not stored:
        raise HTTPException(404, "This setting does not exist.")
    return SettingDefinitionView.model_validate(stored.definition)


def setting_head(db, owner, key, scope, lock=False):
    query = select(SettingHead).where(
        SettingHead.owner_id == owner.id, SettingHead.key == key, SettingHead.scope == scope
    )
    if lock:
        db.execute(
            insert(SettingHead)
            .values(id=identifier(), owner_id=owner.id, key=key, scope=scope, revision=0)
            .on_conflict_do_nothing(index_elements=["owner_id", "key", "scope"])
        )
        query = query.with_for_update()
    return db.scalar(query)


def current_setting(db, head):
    if not head or head.revision == 0:
        return None
    return db.scalar(
        select(SettingVersion).where(
            SettingVersion.head_id == head.id, SettingVersion.revision == head.revision
        )
    )


def setting_view(db, owner, key):
    spec = definition(db, key)
    global_head = setting_head(db, owner, key, "global")
    user_head = setting_head(db, owner, key, "user")
    global_version = current_setting(db, global_head)
    user_version = current_setting(db, user_head)
    global_value = (
        spec.default if not global_version or global_version.inherited else global_version.value
    )
    overridden = bool(user_version and not user_version.inherited)
    global_overridden = bool(global_version and not global_version.inherited)
    source = "user" if overridden else "global" if global_overridden else "default"
    return SettingView(
        definition=spec,
        effective_value=user_version.value if overridden else global_value,
        source_scope=source,
        source_revision=user_head.revision
        if overridden
        else global_head.revision
        if global_overridden
        else 0,
        global_value=global_value,
        global_revision=global_head.revision if global_head else 0,
        user_revision=user_head.revision if user_head else 0,
        overridden=overridden,
    )


def validate_setting(spec, scope, value, inherited):
    if scope not in spec.scopes:
        raise HTTPException(422, "This setting cannot be changed at that scope.")
    if inherited:
        if value is not None:
            raise HTTPException(422, "An inherited setting must not include a value.")
        return
    if spec.kind == "boolean":
        valid = type(value) is bool
    elif spec.kind == "integer":
        valid = (
            type(value) is int
            and (spec.minimum is None or value >= spec.minimum)
            and (spec.maximum is None or value <= spec.maximum)
        )
    elif spec.kind == "enum":
        valid = type(value) is str and value in spec.options
    else:
        valid = type(value) is str and len(value) < 100
        if valid:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                valid = False
    if not valid:
        raise HTTPException(422, f"Choose a valid value for {spec.label.lower()}.")


def change_setting(
    db, owner, key, scope, value, inherited, expected_revision, reason, restored_from=None
):
    spec = definition(db, key)
    validate_setting(spec, scope, value, inherited)
    head = setting_head(db, owner, key, scope, lock=True)
    if head.revision != expected_revision:
        raise HTTPException(
            409, "This setting changed in another tab. Reload its latest value before saving."
        )
    previous = current_setting(db, head)
    head.revision += 1
    version = SettingVersion(
        head_id=head.id,
        revision=head.revision,
        value=value,
        inherited=inherited,
        actor=owner.id,
        reason=reason,
    )
    db.add(version)
    audit(
        db,
        owner,
        "setting.restored"
        if restored_from
        else "setting.inherited"
        if inherited
        else "setting.changed",
        "setting",
        key,
        reason,
        {
            "scope": scope,
            "revision": head.revision,
            "previous_value": previous.value if previous else None,
            "previous_inherited": previous.inherited if previous else True,
            "value": value,
            "inherited": inherited,
            "restored_from": restored_from,
        },
    )
    db.commit()
    return setting_view(db, owner, key)


def get_workspace(db, owner, workspace_id, lock=False):
    query = select(Workspace).where(Workspace.id == workspace_id, Workspace.owner_id == owner.id)
    if lock:
        query = query.with_for_update()
    workspace = db.scalar(query)
    if not workspace:
        raise HTTPException(404, "This workspace does not exist.")
    return workspace


def workspace_view(db, workspace):
    version = db.scalar(
        select(LayoutVersion).where(
            LayoutVersion.workspace_id == workspace.id, LayoutVersion.revision == workspace.revision
        )
    )
    return WorkspaceView(
        id=workspace.id, name=workspace.name, revision=workspace.revision, layout=version.layout
    )


def validate_layout(layout):
    try:
        encoded = json.dumps(layout, allow_nan=False)
    except (ValueError, TypeError):
        raise HTTPException(422, "The layout contains invalid values.") from None
    if len(encoded.encode()) > 65536:
        raise HTTPException(422, "The layout is too large.")
    if (
        set(layout) - {"global", "borders", "layout", "subLayouts"}
        or not isinstance(layout.get("global", {}), dict)
        or layout.get("borders", []) != []
        or layout.get("subLayouts", {}) != {}
    ):
        raise HTTPException(422, "This layout format is not supported.")
    if layout.get("global", {}).get("tabEnablePopout"):
        raise HTTPException(422, "Separate browser windows are not supported yet.")
    seen = set()
    count = 0

    def visit(node, depth, parent=None):
        nonlocal count
        count += 1
        if count > 80 or depth > 12 or not isinstance(node, dict):
            raise HTTPException(422, "The layout has too many nested panels.")
        kind = node.get("type")
        if kind not in {"row", "tabset", "tab"}:
            raise HTTPException(422, "This panel type is not supported.")
        if (
            (parent is None and kind != "row")
            or (parent == "tabset" and kind != "tab")
            or (parent == "row" and kind not in {"row", "tabset"})
        ):
            raise HTTPException(422, "The layout structure is invalid.")
        node_id = node.get("id")
        if not isinstance(node_id, str) or not 1 <= len(node_id) <= 100 or node_id in seen:
            raise HTTPException(422, "Each layout panel must have a unique identifier.")
        seen.add(node_id)
        weight = node.get("weight", 100)
        if (
            type(weight) not in {int, float}
            or not math.isfinite(weight)
            or not 0 < weight <= 1_000_000
        ):
            raise HTTPException(422, "Panel sizes must be positive numbers.")
        if kind == "tab":
            if (
                node.get("component") not in PANELS
                or not isinstance(node.get("name"), str)
                or len(node["name"]) > 80
                or "children" in node
            ):
                raise HTTPException(422, "This panel is not available in the workstation.")
        else:
            children = node.get("children")
            if not isinstance(children, list):
                raise HTTPException(422, "The layout is missing its panel list.")
            if kind == "tabset":
                selected = node.get("selected", 0)
                if type(selected) is not int or (children and not 0 <= selected < len(children)):
                    raise HTTPException(422, "The selected panel is invalid.")
            for child in children:
                visit(child, depth + 1, kind)

    visit(layout.get("layout"), 0)


def create_workspace(db, owner, name, clone_from=None):
    layout = (
        workspace_view(db, get_workspace(db, owner, clone_from)).layout
        if clone_from
        else default_layout()
    )
    workspace = Workspace(id=identifier(), owner_id=owner.id, name=name, revision=1)
    db.add(workspace)
    db.flush()
    db.add(
        LayoutVersion(
            workspace_id=workspace.id,
            revision=1,
            layout=layout,
            actor=owner.id,
            reason="Cloned saved workspace" if clone_from else "Created workspace",
        )
    )
    audit(
        db,
        owner,
        "workspace.cloned" if clone_from else "workspace.created",
        "workspace",
        workspace.id,
        "Cloned saved workspace" if clone_from else "Created workspace",
        {"name": name, "clone_from": clone_from, "revision": 1},
    )
    db.commit()
    return workspace_view(db, workspace)


def change_layout(
    db,
    owner,
    workspace_id,
    layout,
    expected_revision,
    reason,
    action="workspace.saved",
    restored_from=None,
):
    validate_layout(layout)
    workspace = get_workspace(db, owner, workspace_id, lock=True)
    if workspace.revision != expected_revision:
        raise HTTPException(
            409, "This workspace changed in another tab. Reload its saved layout before saving."
        )
    workspace.revision += 1
    db.add(
        LayoutVersion(
            workspace_id=workspace.id,
            revision=workspace.revision,
            layout=layout,
            actor=owner.id,
            reason=reason,
        )
    )
    audit(
        db,
        owner,
        action,
        "workspace",
        workspace.id,
        reason,
        {"name": workspace.name, "revision": workspace.revision, "restored_from": restored_from},
    )
    db.commit()
    return workspace_view(db, workspace)


def audit_view(event):
    return AuditView(
        id=event.id,
        actor=event.actor,
        action=event.action,
        resource=ResourceRef(type=event.resource_type, id=event.resource_id),
        reason=event.reason,
        details=event.details,
        created_at=event.created_at,
    )


def audit_page(db, owner: Owner, cursor, limit):
    query = select(AuditEvent).where(AuditEvent.owner_id == owner.id)
    if cursor:
        anchor = db.get(AuditEvent, cursor)
        if not anchor or anchor.owner_id != owner.id:
            raise HTTPException(404, "This history page is no longer available.")
        query = query.where(
            or_(
                AuditEvent.created_at < anchor.created_at,
                (AuditEvent.created_at == anchor.created_at) & (AuditEvent.id < anchor.id),
            )
        )
    events = db.scalars(
        query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(limit + 1)
    ).all()
    return {
        "items": [audit_view(event) for event in events[:limit]],
        "next_cursor": events[limit - 1].id if len(events) > limit else None,
    }
