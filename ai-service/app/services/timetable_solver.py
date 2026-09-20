from __future__ import annotations

from dataclasses import dataclass

from ortools.sat.python import cp_model

from app.schemas.timetable import (
    SwapSuggestion,
    TimetableSlotInput,
    TimetableSolveRequest,
    TimetableSolveResponse,
)


@dataclass(frozen=True)
class _Period:
    day: int
    start: str
    end: str

    def label(self) -> str:
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        day_name = days[self.day] if 0 <= self.day < len(days) else f"D{self.day}"
        return f"{day_name} {self.start}–{self.end}"


def _overlap(a: TimetableSlotInput, b: TimetableSlotInput) -> bool:
    if a.day_of_week != b.day_of_week:
        return False
    return a.start_time < b.end_time and b.start_time < a.end_time


def _clash_count(slot: TimetableSlotInput, all_slots: list[TimetableSlotInput]) -> int:
    if not slot.teacher_id:
        return 0
    return sum(
        1
        for other in all_slots
        if other.slot_id != slot.slot_id and other.teacher_id == slot.teacher_id and _overlap(slot, other)
    )


def _collect_periods(*slot_groups: list[TimetableSlotInput]) -> list[_Period]:
    seen: set[tuple[int, str, str]] = set()
    periods: list[_Period] = []
    for group in slot_groups:
        for slot in group:
            key = (slot.day_of_week, slot.start_time, slot.end_time)
            if key in seen:
                continue
            seen.add(key)
            periods.append(_Period(day=slot.day_of_week, start=slot.start_time, end=slot.end_time))
    periods.sort(key=lambda p: (p.day, p.start, p.end))
    return periods


def _period_index(periods: list[_Period], slot: TimetableSlotInput) -> int:
    key = (slot.day_of_week, slot.start_time, slot.end_time)
    for idx, period in enumerate(periods):
        if (period.day, period.start, period.end) == key:
            return idx
    raise ValueError(f"Unknown period for slot {slot.slot_id}")


def solve_with_ortools(request: TimetableSolveRequest) -> TimetableSolveResponse:
    class_slots = request.class_slots
    all_slots = request.all_slots or class_slots
    periods = _collect_periods(class_slots, all_slots)

    if not class_slots:
        return TimetableSolveResponse(
            classId=request.class_id,
            slotCount=0,
            clashCount=0,
            solver="ortools",
            suggestions=[],
        )

    if not periods:
        return TimetableSolveResponse(
            classId=request.class_id,
            slotCount=len(class_slots),
            clashCount=0,
            solver="ortools",
            suggestions=[],
        )

    clash_by_slot = {slot.slot_id: _clash_count(slot, all_slots) for slot in class_slots}
    total_clashes = sum(1 for count in clash_by_slot.values() if count > 0)
    movable = [slot for slot in class_slots if clash_by_slot.get(slot.slot_id, 0) > 0]
    fixed_class = [slot for slot in class_slots if slot not in movable]

    if not movable:
        return TimetableSolveResponse(
            classId=request.class_id,
            slotCount=len(class_slots),
            clashCount=0,
            solver="ortools",
            suggestions=[],
        )

    model = cp_model.CpModel()
    assignment: dict[str, dict[int, cp_model.IntVar]] = {}
    current_idx = {slot.slot_id: _period_index(periods, slot) for slot in class_slots}

    for slot in movable:
        assignment[slot.slot_id] = {}
        for p_idx in range(len(periods)):
            assignment[slot.slot_id][p_idx] = model.new_bool_var(f"x_{slot.slot_id}_{p_idx}")
        model.add_exactly_one(assignment[slot.slot_id][p_idx] for p_idx in range(len(periods)))

    # Class period capacity: one slot per period (fixed + movable).
    for p_idx, period in enumerate(periods):
        fixed_here = sum(
            1
            for slot in fixed_class
            if slot.day_of_week == period.day
            and slot.start_time == period.start
            and slot.end_time == period.end
        )
        if fixed_here > 1:
            continue
        movable_vars = [assignment[s.slot_id][p_idx] for s in movable]
        if movable_vars:
            model.add(sum(movable_vars) + fixed_here <= 1)

    # Teacher capacity per period (include slots outside this class).
    teachers = {slot.teacher_id for slot in class_slots if slot.teacher_id}
    external_by_teacher_period: dict[tuple[str, int], int] = {}
    class_ids = {slot.slot_id for slot in class_slots}
    for slot in all_slots:
        if not slot.teacher_id or slot.slot_id in class_ids:
            continue
        try:
            p_idx = _period_index(periods, slot)
        except ValueError:
            continue
        key = (slot.teacher_id, p_idx)
        external_by_teacher_period[key] = external_by_teacher_period.get(key, 0) + 1

    for teacher_id in teachers:
        for p_idx in range(len(periods)):
            external = external_by_teacher_period.get((teacher_id, p_idx), 0)
            teacher_movable = [
                assignment[s.slot_id][p_idx] for s in movable if s.teacher_id == teacher_id
            ]
            teacher_fixed = sum(
                1
                for slot in fixed_class
                if slot.teacher_id == teacher_id and current_idx[slot.slot_id] == p_idx
            )
            if teacher_movable:
                model.add(sum(teacher_movable) + teacher_fixed + external <= 1)

    # Minimize moves away from the current period.
    move_penalties = []
    for slot in movable:
        cur = current_idx[slot.slot_id]
        for p_idx in range(len(periods)):
            if p_idx != cur:
                move_penalties.append(assignment[slot.slot_id][p_idx])
    if move_penalties:
        model.minimize(sum(move_penalties))
    else:
        model.minimize(0)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 5.0
    status = solver.Solve(model)

    suggestions: list[SwapSuggestion] = []
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for slot in movable:
            clashes = clash_by_slot[slot.slot_id]
            suggestions.append(
                SwapSuggestion(
                    slotId=slot.slot_id,
                    clashCount=clashes,
                    currentDay=slot.day_of_week,
                    currentStart=slot.start_time,
                    currentEnd=slot.end_time,
                    suggestion=(
                        f"No OR-Tools slot found — manually move {slot.start_time}–{slot.end_time} "
                        f"(clashes with {clashes} other slot(s))."
                    ),
                )
            )
        return TimetableSolveResponse(
            classId=request.class_id,
            slotCount=len(class_slots),
            clashCount=total_clashes,
            solver="ortools",
            suggestions=suggestions,
        )

    for slot in movable:
        chosen = current_idx[slot.slot_id]
        for p_idx in range(len(periods)):
            if solver.value(assignment[slot.slot_id][p_idx]) == 1:
                chosen = p_idx
                break
        period = periods[chosen]
        cur = periods[current_idx[slot.slot_id]]
        moved = chosen != current_idx[slot.slot_id]
        clashes = clash_by_slot[slot.slot_id]
        if moved:
            suggestion = (
                f"Move slot from {cur.label()} to {period.label()} "
                f"to resolve {clashes} teacher clash(es)."
            )
        else:
            suggestion = (
                f"Keep at {cur.label()} but reassign teacher — {clashes} clash(es) remain "
                f"with current teacher."
            )
        suggestions.append(
            SwapSuggestion(
                slotId=slot.slot_id,
                clashCount=clashes,
                currentDay=slot.day_of_week,
                currentStart=slot.start_time,
                currentEnd=slot.end_time,
                proposedDay=period.day if moved else None,
                proposedStart=period.start if moved else None,
                proposedEnd=period.end if moved else None,
                suggestion=suggestion,
            )
        )

    return TimetableSolveResponse(
        classId=request.class_id,
        slotCount=len(class_slots),
        clashCount=total_clashes,
        solver="ortools",
        suggestions=suggestions,
    )
