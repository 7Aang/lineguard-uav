from __future__ import annotations

import re

from lineguard.audit import audited_tool
from lineguard.models import InspectionTaskSpec


def _extract_tower_range(text: str) -> tuple[int | None, int | None]:
    patterns = (
        r"(\d+)\s*号?\s*(?:到|至|-|—|~)\s*(\d+)\s*号?塔",
        r"(\d+)\s*号塔\s*(?:到|至|-|—|~)\s*(\d+)\s*号塔",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None, None


@audited_tool("parse_inspection_task")
def parse_inspection_task(task_id: str, text: str) -> InspectionTaskSpec:
    voltage_match = re.search(r"(\d{2,4})\s*kV", text, flags=re.IGNORECASE)
    tower_start, tower_end = _extract_tower_range(text)

    weather_map = {
        "strong_wind": ("强风", "大风", "wind"),
        "night": ("夜间", "夜晚", "night"),
        "low_temperature": ("低温", "严寒"),
        "icing": ("覆冰", "结冰", "icing"),
        "rain": ("雨", "降雨"),
    }
    target_map = {
        "conductor": ("导线", "导地线", "地线", "conductor"),
        "spacer": ("间隔棒", "spacer"),
        "insulator": ("绝缘子", "insulator"),
        "tower": ("杆塔", "铁塔", "tower"),
    }
    lowered = text.lower()
    weather = [
        key
        for key, aliases in weather_map.items()
        if any(alias.lower() in lowered for alias in aliases)
    ]
    targets = [
        key
        for key, aliases in target_map.items()
        if any(alias.lower() in lowered for alias in aliases)
    ]
    if not targets:
        targets = ["conductor"]

    requested_outputs = ["risk_report"]
    if "轨迹" in text:
        requested_outputs.append("trajectory")
    if "频率" in text:
        requested_outputs.append("frequency")
    if "幅值" in text:
        requested_outputs.append("amplitude")

    return InspectionTaskSpec(
        raw_text=text,
        voltage_kv=int(voltage_match.group(1)) if voltage_match else None,
        tower_start=tower_start,
        tower_end=tower_end,
        weather_conditions=weather,
        targets=targets,
        requested_outputs=list(dict.fromkeys(requested_outputs)),
    )
