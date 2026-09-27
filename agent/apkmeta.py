"""Static APK analysis: build the map the crawler explores.

Parses AndroidManifest.xml (via androguard, pure Python — no Java/apktool
needed) and returns every activity, its exported flag, intent filters, deep
links, and permissions. This is the coverage denominator: "your app has N
screens, we visited M".
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _silence_androguard() -> None:
    try:
        from loguru import logger
        logger.remove()
    except Exception:
        pass


@dataclass
class ActivityInfo:
    name: str                 # fully qualified class name
    exported: bool
    is_main: bool = False
    deep_links: list[str] = field(default_factory=list)
    has_intent_filter: bool = False


@dataclass
class ApkMeta:
    package: str
    version: str
    main_activity: str | None
    activities: list[ActivityInfo]
    permissions: list[str]
    app_name: str = ""


def _resolve(name: str, package: str) -> str:
    if name.startswith("."):
        return package + name
    if "." not in name:
        return package + "." + name
    return name


def analyze(apk_path: str) -> ApkMeta:
    _silence_androguard()
    from androguard.core.apk import APK

    a = APK(apk_path)
    package = a.get_package()
    main = a.get_main_activity()
    if main:
        main = _resolve(main, package)

    activities: list[ActivityInfo] = []
    for raw in a.get_activities():
        name = _resolve(raw, package)
        try:
            exported = a.get_activity_exported(raw)
        except Exception:
            exported = False
        links: list[str] = []
        has_filter = False
        try:
            filters = a.get_intent_filters("activity", raw)
            has_filter = bool(filters)
            for f in filters.get("data", []):
                # data dicts carry scheme/host/path keys
                scheme = f.get("scheme", "")
                host = f.get("host", "")
                path = f.get("path", "") or f.get("pathPrefix", "") or ""
                if scheme and host:
                    links.append(f"{scheme}://{host}{path}")
                elif scheme:
                    links.append(f"{scheme}:")
        except Exception:
            pass
        # Pre-Android-12 APKs often omit android:exported; an activity with
        # intent filters was implicitly exported — treat it as launchable.
        exported = bool(exported) or has_filter
        activities.append(ActivityInfo(
            name=name,
            exported=exported,
            is_main=(name == main),
            deep_links=links,
            has_intent_filter=has_filter,
        ))

    try:
        version = a.get_androidversion_name() or ""
    except Exception:
        version = ""
    try:
        app_name = a.get_app_name() or ""
    except Exception:
        app_name = ""

    return ApkMeta(
        package=package,
        version=version,
        main_activity=main,
        activities=activities,
        permissions=a.get_permissions() or [],
        app_name=app_name,
    )


def component(package: str, activity: str) -> str:
    """'pkg/ActivityClass' component string for `am start -n`."""
    short = activity[len(package):] if activity.startswith(package) else activity
    if not short.startswith("."):
        short = "." + short.lstrip(".")
    return f"{package}/{short}"
