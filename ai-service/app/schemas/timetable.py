from pydantic import BaseModel, Field


class TimetableSlotInput(BaseModel):
    slot_id: str = Field(alias="slotId")
    class_id: str = Field(alias="classId")
    subject_id: str | None = Field(default=None, alias="subjectId")
    teacher_id: str | None = Field(default=None, alias="teacherId")
    day_of_week: int = Field(alias="dayOfWeek")
    start_time: str = Field(alias="startTime")
    end_time: str = Field(alias="endTime")
    room: str | None = None

    model_config = {"populate_by_name": True}


class TimetableSolveRequest(BaseModel):
    class_id: str = Field(alias="classId")
    class_slots: list[TimetableSlotInput] = Field(alias="classSlots")
    all_slots: list[TimetableSlotInput] = Field(default_factory=list, alias="allSlots")
    explain: bool = True

    model_config = {"populate_by_name": True}


class SwapSuggestion(BaseModel):
    slot_id: str = Field(alias="slotId")
    clash_count: int = Field(alias="clashCount")
    current_day: int = Field(alias="currentDay")
    current_start: str = Field(alias="currentStart")
    current_end: str = Field(alias="currentEnd")
    proposed_day: int | None = Field(default=None, alias="proposedDay")
    proposed_start: str | None = Field(default=None, alias="proposedStart")
    proposed_end: str | None = Field(default=None, alias="proposedEnd")
    suggestion: str
    explanation: str | None = None

    model_config = {"populate_by_name": True}


class TimetableSolveResponse(BaseModel):
    class_id: str = Field(alias="classId")
    slot_count: int = Field(alias="slotCount")
    clash_count: int = Field(alias="clashCount")
    solver: str = "ortools"
    suggestions: list[SwapSuggestion] = Field(default_factory=list)
    disclaimer: str = "Review suggestions, then apply via Nest or use auto-apply on AI Tools."

    model_config = {"populate_by_name": True}
