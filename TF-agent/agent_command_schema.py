# -*- coding: utf-8 -*-
"""Agent 系统命令的边界 Schema。

解析器仍保留 legacy 文本兼容，但所有进入 session state 的命令都先经过这里的
结构校验；Schema 不执行任何任务，也不读取文件或环境中的凭据。
"""
from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class MapBounds(BaseModel):
    """Canonical WGS84 rectangle used by map commands."""

    model_config = ConfigDict(extra="forbid")

    west: float = Field(ge=-180.0, le=180.0)
    south: float = Field(ge=-90.0, le=90.0)
    east: float = Field(ge=-180.0, le=180.0)
    north: float = Field(ge=-90.0, le=90.0)

    @model_validator(mode="after")
    def validate_rectangle(self) -> "MapBounds":
        if self.west >= self.east or self.south >= self.north:
            raise ValueError("bounds must satisfy west < east and south < north")
        return self


class MapCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    zoom: int = Field(default=8, ge=1, le=18)
    bounds: Optional[MapBounds] = None
    preset: Optional[str] = None
    label: Optional[str] = None
    height: Optional[float] = Field(default=None, ge=0.0)
    duration: Optional[float] = Field(default=None, ge=0.0)
    pitch: Optional[float] = None
    heading: Optional[float] = None


def _finite_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _clamped_float(value: Any, low: float, high: float) -> Optional[float]:
    number = _finite_float(value)
    if number is None:
        return None
    return min(high, max(low, number))


def _clamped_int(value: Any, low: int, high: int) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return min(high, max(low, number))


class M4Parameters(BaseModel):
    """GEE/M4 参数的强类型边界；不负责读取文件或发起远端调用。"""

    model_config = ConfigDict(extra="allow")

    roi_path: Optional[str] = None
    roi_name: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    cloud_limit: Optional[int] = Field(default=None, ge=0, le=100)
    min_land_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    max_land_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    min_pixel_count: Optional[int] = Field(default=None, ge=100, le=500000)
    scale: Optional[int] = Field(default=None, ge=10, le=30)
    bands: Optional[List[str]] = None
    export_to: Optional[Literal["drive", "local"]] = None

    @field_validator("cloud_limit", mode="before")
    @classmethod
    def normalize_cloud(cls, value: Any) -> Optional[int]:
        return _clamped_int(value, 0, 100)

    @field_validator("min_land_pct", "max_land_pct", mode="before")
    @classmethod
    def normalize_land_pct(cls, value: Any) -> Optional[float]:
        return _clamped_float(value, 0.0, 100.0)

    @field_validator("min_pixel_count", mode="before")
    @classmethod
    def normalize_pixel_count(cls, value: Any) -> Optional[int]:
        return _clamped_int(value, 100, 500000)

    @field_validator("scale", mode="before")
    @classmethod
    def normalize_scale(cls, value: Any) -> Optional[int]:
        return _clamped_int(value, 10, 30)

    @model_validator(mode="after")
    def validate_date_and_land_order(self) -> "M4Parameters":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        if (
            self.min_land_pct is not None
            and self.max_land_pct is not None
            and self.min_land_pct > self.max_land_pct
        ):
            raise ValueError("min_land_pct must not exceed max_land_pct")
        return self


class SidebarDelta(BaseModel):
    """侧栏差量；字段集合由 bridge 的 SIDEBAR_KEY_MAP 继续约束。"""

    model_config = ConfigDict(extra="allow")

    prob_th: Optional[float] = Field(default=None, ge=0.01, le=0.50)
    min_cnt: Optional[int] = Field(default=None, ge=1, le=10)
    m4_cloud: Optional[int] = Field(default=None, ge=0, le=100)
    m4_min_land: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    m4_max_land: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    m4_min_pix: Optional[int] = Field(default=None, ge=100, le=500000)
    m4_scale: Optional[int] = Field(default=None, ge=10, le=30)
    m4_start_date: Optional[date] = None
    m4_end_date: Optional[date] = None

    @field_validator("prob_th", mode="before")
    @classmethod
    def normalize_probability(cls, value: Any) -> Optional[float]:
        return _clamped_float(value, 0.01, 0.50)

    @field_validator("min_cnt", mode="before")
    @classmethod
    def normalize_count(cls, value: Any) -> Optional[int]:
        return _clamped_int(value, 1, 10)

    @field_validator("m4_cloud", mode="before")
    @classmethod
    def normalize_sidebar_cloud(cls, value: Any) -> Optional[int]:
        return _clamped_int(value, 0, 100)

    @field_validator("m4_min_land", "m4_max_land", mode="before")
    @classmethod
    def normalize_sidebar_land(cls, value: Any) -> Optional[float]:
        return _clamped_float(value, 0.0, 100.0)

    @field_validator("m4_min_pix", mode="before")
    @classmethod
    def normalize_sidebar_pixels(cls, value: Any) -> Optional[int]:
        return _clamped_int(value, 100, 500000)

    @field_validator("m4_scale", mode="before")
    @classmethod
    def normalize_sidebar_scale(cls, value: Any) -> Optional[int]:
        return _clamped_int(value, 10, 30)

    @model_validator(mode="after")
    def validate_sidebar_dates_and_land_order(self) -> "SidebarDelta":
        if self.m4_start_date and self.m4_end_date and self.m4_start_date > self.m4_end_date:
            raise ValueError("m4_start_date must not be after m4_end_date")
        if (
            self.m4_min_land is not None
            and self.m4_max_land is not None
            and self.m4_min_land > self.m4_max_land
        ):
            raise ValueError("m4_min_land must not exceed m4_max_land")
        return self


_ACTION_TYPES = {
    "run_autotune", "run_m4", "run_e1", "run_m5", "run_inference",
    "run_gee_download", "run_pipeline", "run", "run_workflow",
    "propose_m5", "plan_m5", "confirm_m5", "propose_e1", "plan_e1",
    "confirm_e1", "propose_inference", "plan_inference", "confirm_inference",
    "propose_gee", "propose_gee_plan", "plan_gee", "confirm_gee",
    "propose_workflow", "plan_workflow", "propose_analysis_workflow",
    "confirm_workflow",
}


class PendingAction(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    confirmed: Optional[bool] = None
    task: Optional[str] = None
    prob_th: Optional[float] = Field(default=None, ge=0.01, le=0.50)
    min_cnt: Optional[int] = Field(default=None, ge=1, le=10)
    m4_params: Optional[M4Parameters] = None

    @field_validator("prob_th", mode="before")
    @classmethod
    def normalize_action_probability(cls, value: Any) -> Optional[float]:
        return _clamped_float(value, 0.01, 0.50)

    @field_validator("min_cnt", mode="before")
    @classmethod
    def normalize_action_count(cls, value: Any) -> Optional[int]:
        return _clamped_int(value, 1, 10)


class SystemCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    map: Optional[MapCommand] = None
    sidebar_states: Optional[SidebarDelta] = None
    pending_action: Optional[PendingAction] = None


class CommandValidationError(ValueError):
    """面向 UI 的安全校验错误，不回声原始命令内容。"""


def validate_system_command(command: Any) -> Dict[str, Any]:
    if not isinstance(command, dict):
        raise CommandValidationError("系统命令必须是 JSON 对象。")
    try:
        parsed = SystemCommand.model_validate(command)
    except ValidationError as exc:
        # 只返回字段定位和规则摘要，不返回输入值/堆栈。
        fields = ", ".join(".".join(str(x) for x in err.get("loc", ())) for err in exc.errors())
        raise CommandValidationError(f"系统命令校验失败（字段：{fields or '未知'}）。") from None

    data = parsed.model_dump(exclude_none=True)
    action = data.get("pending_action")
    if action:
        action_type = str(action.get("type") or "").strip().lower()
        if action_type not in _ACTION_TYPES:
            raise CommandValidationError("不支持的 pending_action.type。")
        action["type"] = action_type
    return data


__all__ = [
    "CommandValidationError", "M4Parameters", "MapBounds", "MapCommand", "PendingAction", "SidebarDelta",
    "SystemCommand", "validate_system_command",
]
