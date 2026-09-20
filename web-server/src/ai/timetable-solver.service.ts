import { BadRequestException, Injectable, Logger, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { TimetableService } from '../timetable/timetable.service';
import { AiServiceClient } from './ai-service.client';

type TimetableSlotRow = {
  id: string;
  classId: string;
  subjectId?: string | null;
  teacherId?: string | null;
  dayOfWeek: number;
  startTime: string;
  endTime: string;
  room?: string | null;
};

@Injectable()
export class TimetableSolverService {
  private readonly logger = new Logger(TimetableSolverService.name);

  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly flags: FeatureFlagService,
    private readonly aiService: AiServiceClient,
    private readonly timetable: TimetableService,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private mapSlot(row: TimetableSlotRow) {
    return {
      slotId: row.id,
      classId: row.classId,
      subjectId: row.subjectId || undefined,
      teacherId: row.teacherId || undefined,
      dayOfWeek: Number(row.dayOfWeek),
      startTime: row.startTime,
      endTime: row.endTime,
      room: row.room || undefined,
    };
  }

  private async loadSlots(ds: any, classId?: string): Promise<TimetableSlotRow[]> {
    if (classId) {
      return runDbQuery(
        ds,
        `SELECT id, class_id as "classId", subject_id as "subjectId", teacher_id as "teacherId",
                day_of_week as "dayOfWeek", start_time as "startTime", end_time as "endTime", room
         FROM timetable_slots WHERE class_id = ? ORDER BY day_of_week, start_time`,
        [classId],
      );
    }
    return runDbQuery(
      ds,
      `SELECT id, class_id as "classId", subject_id as "subjectId", teacher_id as "teacherId",
              day_of_week as "dayOfWeek", start_time as "startTime", end_time as "endTime", room
       FROM timetable_slots ORDER BY day_of_week, start_time LIMIT 2000`,
      [],
    );
  }

  /** Heuristic fallback when AI service is unavailable. */
  private heuristicSwaps(classId: string, classSlots: TimetableSlotRow[], allSlots: TimetableSlotRow[]) {
    const suggestions: Array<{
      slotId: string;
      suggestion: string;
      clashCount: number;
      currentDay: number;
      currentStart: string;
      currentEnd: string;
      proposedDay?: number;
      proposedStart?: string;
      proposedEnd?: string;
      explanation?: string;
    }> = [];

    for (const slot of classSlots) {
      if (!slot.teacherId) continue;
      const clashes = allSlots.filter(
        (other) =>
          other.id !== slot.id &&
          other.teacherId === slot.teacherId &&
          other.dayOfWeek === slot.dayOfWeek &&
          other.startTime < slot.endTime &&
          other.endTime > slot.startTime,
      );
      if (clashes.length) {
        suggestions.push({
          slotId: slot.id,
          clashCount: clashes.length,
          currentDay: slot.dayOfWeek,
          currentStart: slot.startTime,
          currentEnd: slot.endTime,
          suggestion: `Move ${slot.startTime}–${slot.endTime} slot to an adjacent period or reassign teacher (clashes with ${clashes.length} slot(s))`,
        });
      }
    }
    return {
      classId,
      slotCount: classSlots.length,
      clashCount: suggestions.length,
      solver: 'heuristic',
      suggestions,
      disclaimer: 'Heuristic clash detection — enable AI_SERVICE_URL for OR-Tools optimization.',
    };
  }

  async suggestSwaps(tenantId: string, actorId: string, classId: string) {
    await this.flags.assertEnabled(tenantId, 'ai.timetable_solver');
    if (!classId?.trim()) {
      throw new NotFoundException('classId is required');
    }

    const ds = await this.getTenantDs(tenantId);
    const classSlots = await this.loadSlots(ds, classId);
    const allSlots = await this.loadSlots(ds);

    const actor = {
      userId: actorId,
      roles: ['principal'],
    };

    if (this.aiService.isEnabled()) {
      try {
        const result = await this.aiService.timetableSolve(tenantId, actor, {
          classId,
          classSlots: classSlots.map((row) => this.mapSlot(row)),
          allSlots: allSlots.map((row) => this.mapSlot(row)),
          explain: true,
        });
        return result;
      } catch (err) {
        this.logger.warn(`AI timetable solver failed, using heuristic: ${String(err)}`);
      }
    }

    return this.heuristicSwaps(classId, classSlots, allSlots);
  }

  async applySwaps(
    tenantId: string,
    actorId: string,
    classId: string,
    moves: Array<{
      slotId: string;
      proposedDay?: number;
      proposedStart?: string;
      proposedEnd?: string;
    }>,
  ) {
    await this.flags.assertEnabled(tenantId, 'ai.timetable_solver');
    if (!classId?.trim()) {
      throw new BadRequestException('classId is required');
    }
    if (!Array.isArray(moves) || !moves.length) {
      throw new BadRequestException('moves array is required');
    }

    const ds = await this.getTenantDs(tenantId);
    const classSlots = await this.loadSlots(ds, classId);
    const classSlotIds = new Set(classSlots.map((row) => row.id));

    const applied: string[] = [];
    const skipped: Array<{ slotId: string; reason: string }> = [];
    const failed: Array<{ slotId: string; reason: string }> = [];

    for (const move of moves) {
      if (!move.slotId || !classSlotIds.has(move.slotId)) {
        skipped.push({ slotId: move.slotId || 'unknown', reason: 'slot_not_in_class' });
        continue;
      }
      if (
        move.proposedDay == null ||
        move.proposedDay < 0 ||
        move.proposedDay > 6 ||
        !move.proposedStart ||
        !move.proposedEnd
      ) {
        skipped.push({ slotId: move.slotId, reason: 'no_proposed_period' });
        continue;
      }
      try {
        await this.timetable.reschedule(tenantId, actorId, move.slotId, {
          dayOfWeek: move.proposedDay,
          startTime: move.proposedStart,
          endTime: move.proposedEnd,
        });
        applied.push(move.slotId);
      } catch (err) {
        failed.push({
          slotId: move.slotId,
          reason: err instanceof Error ? err.message : 'apply_failed',
        });
      }
    }

    return {
      classId,
      appliedCount: applied.length,
      applied,
      skipped,
      failed,
      disclaimer: 'Applied moves are live — re-run the solver to verify clashes are cleared.',
    };
  }
}
