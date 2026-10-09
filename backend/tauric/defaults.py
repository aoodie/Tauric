from .schemas import SettingDefinitionView

SETTING_DEFINITIONS = [
    SettingDefinitionView(
        key="ui.density",
        label="Interface density",
        description="Spacing and type size throughout the workstation.",
        kind="enum",
        default="compact",
        scopes=["global", "user"],
        options=["comfortable", "compact", "terminal"],
    ),
    SettingDefinitionView(
        key="ui.timezone",
        label="Display timezone",
        description="Times are stored in UTC and displayed in this timezone.",
        kind="timezone",
        default="Europe/London",
        scopes=["global", "user"],
    ),
    SettingDefinitionView(
        key="workspace.confirm_unsaved",
        label="Protect unsaved layouts",
        description="Confirm before switching or resetting a workspace with unsaved changes.",
        kind="boolean",
        default=True,
        scopes=["global", "user"],
    ),
    SettingDefinitionView(
        key="system.worker_concurrency",
        label="Analysis concurrency",
        description="Maximum simultaneous expensive jobs when workers are added. No workers are running in this milestone.",
        kind="integer",
        default=1,
        scopes=["global"],
        minimum=1,
        maximum=8,
        restart_required=True,
    ),
]

PANELS = {
    "chart": "Market chart",
    "scanner": "Scanner",
    "tauric": "Tauric analysis",
    "openclaw": "OpenClaw governance",
    "openclaw_activity": "OpenClaw activity",
    "positions": "Positions",
    "orders": "Orders",
    "journal": "Journal",
}


def default_layout() -> dict:
    def tab(component):
        return {
            "type": "tab",
            "id": "panel-" + component,
            "name": PANELS[component],
            "component": component,
        }

    return {
        "global": {
            "tabEnableClose": True,
            "tabSetEnableMaximize": True,
            "tabEnableRename": False,
            "tabEnablePopout": False,
            "splitterSize": 5,
        },
        "borders": [],
        "layout": {
            "type": "row",
            "id": "root",
            "children": [
                {
                    "type": "row",
                    "id": "main-column",
                    "weight": 72,
                    "children": [
                        {
                            "type": "tabset",
                            "id": "market",
                            "weight": 72,
                            "selected": 0,
                            "children": [tab("chart"), tab("scanner")],
                        },
                        {
                            "type": "tabset",
                            "id": "execution",
                            "weight": 28,
                            "selected": 0,
                            "children": [tab("positions"), tab("orders"), tab("journal")],
                        },
                    ],
                },
                {
                    "type": "tabset",
                    "id": "intelligence",
                    "weight": 28,
                    "selected": 0,
                    "children": [tab("tauric"), tab("openclaw")],
                },
            ],
        },
    }
