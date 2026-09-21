import { BadRequestException, ForbiddenException } from '@nestjs/common';
import { runDbQuery } from '../../database/db-driver.util';

export type TeacherScope = {
  isTeacher: boolean;
  classIds: string[];
  subjectIds: string[];
  subjectClassIds: string[];
};

export const ACADEMIC_MANAGEMENT_ROLES = [
  'director',
  'school_admin',
  'head_teacher',
  'principal',
  'vice_principal',
  'assistant_head_teacher',
] as const;

export function hasAcademicManagementRole(roles: string[]): boolean {
  return roles.some((role) =>
    ACADEMIC_MANAGEMENT_ROLES.includes(role as (typeof ACADEMIC_MANAGEMENT_ROLES)[number]),
  );
}

function hasTenantWideRole(roles: string[]): boolean {
  return roles.some((role) => ['director', 'school_admin', 'it_admin'].includes(role));
}

/** Returns null for tenant-wide managers, otherwise the student IDs visible to the actor. */
export async function getVisibleStudentIds(
  ds: any,
  userId: string,
): Promise<string[] | null> {
  const rows = await runDbQuery(ds, `SELECT roles FROM users WHERE id = ? LIMIT 1`, [userId]);
  const roles = parseRoleList(rows?.[0]?.roles);
  if (hasTenantWideRole(roles) || hasAcademicManagementRole(roles)) return null;
  if (roles.includes('student')) return [userId];
  if (roles.includes('parent')) {
    const links = await runDbQuery(
      ds,
      `SELECT student_id as "studentId" FROM parent_student_links WHERE parent_id = ?`,
      [userId],
    );
    return links.map((row: any) => String(row.studentId ?? row.studentid));
  }
  if (
    roles.includes('teacher') ||
    roles.includes('class_teacher') ||
    roles.includes('subject_teacher')
  ) {
    const scope = await getTeacherScope(ds, userId);
    if (!scope.classIds.length && !scope.subjectClassIds.length) return [];
    const classIds = [...new Set([...scope.classIds, ...scope.subjectClassIds])];
    const students = await runDbQuery(
      ds,
      `SELECT DISTINCT student_id as "studentId"
       FROM student_class_enrollments
       WHERE class_id IN (${classIds.map(() => '?').join(',')})`,
      classIds,
    );
    return students.map((row: any) => String(row.studentId ?? row.studentid));
  }
  return [];
}

/** Load normalized role slugs for a tenant user (users.roles JSON only — no legacy role column). */
export async function loadUserRoles(ds: any, userId: string): Promise<string[]> {
  const rows = await runDbQuery(ds, `SELECT roles FROM users WHERE id = ? LIMIT 1`, [userId]);
  return parseRoleList(rows?.[0]?.roles);
}

export function parseRoleList(roles: unknown): string[] {
  if (Array.isArray(roles)) return roles.map((role) => String(role).toLowerCase());
  if (typeof roles === 'string') {
    try {
      const parsed = JSON.parse(roles);
      if (Array.isArray(parsed)) {
        return parsed.map((role) => String(role).toLowerCase());
      }
    } catch {
      return roles
        .split(',')
        .map((role) => role.trim().toLowerCase())
        .filter(Boolean);
    }
  }
  return [];
}

export async function getTeacherScope(
  ds: any,
  userId: string,
): Promise<TeacherScope> {
  const rows = await runDbQuery(
    ds,
    `SELECT roles FROM users WHERE id = ? LIMIT 1`,
    [userId],
  );
  const roles = parseRoleList(rows?.[0]?.roles);
  const isTeacher =
    roles.includes('teacher') ||
    roles.includes('class_teacher') ||
    roles.includes('subject_teacher');
  if (!isTeacher) {
    return { isTeacher: false, classIds: [], subjectIds: [], subjectClassIds: [] };
  }

  const classes: any[] = await runDbQuery(
    ds,
    `SELECT class_id as "classId" FROM academic_class_teacher_links WHERE teacher_id = ?
     UNION SELECT id as "classId" FROM academic_classes WHERE class_teacher_id = ?`,
    [userId, userId],
  );
  const ownedClasses: any[] = await runDbQuery(
    ds,
    `SELECT id as "classId" FROM academic_classes WHERE class_teacher_id = ?`,
    [userId],
  );
  const ownedClassIds = new Set(
    ownedClasses.map((row) => String(row.classId ?? row.classid)),
  );
  const subjects: any[] = await runDbQuery(
    ds,
    `SELECT st.subject_id as "subjectId", s.class_ids as "classIds"
     FROM academic_subject_teacher_links st
     JOIN academic_subjects s ON s.id = st.subject_id
     WHERE st.teacher_id = ?`,
    [userId],
  );
  const subjectClassIds = subjects.flatMap((row) => {
    const raw = row.classIds ?? row.classids;
    if (Array.isArray(raw)) return raw.map(String);
    if (typeof raw !== 'string') return [];
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed.map(String) : [];
    } catch {
      return raw
        .split(',')
        .map((value) => value.trim())
        .filter(Boolean);
    }
  });

  const subjectClassIdSet = new Set(subjectClassIds.map(String));
  return {
    isTeacher: true,
    classIds: classes
      .map((row) => row.classId ?? row.classid)
      .filter((id) => id != null)
      .map(String)
      // A subject assignment must not turn a class into a homeroom
      // responsibility for a dual-role teacher. The class_teacher_id is the
      // authoritative owner when both roles are present.
      .filter((id) => ownedClassIds.has(id) || !subjectClassIdSet.has(id)),
    subjectIds: subjects
      .map((row) => row.subjectId ?? row.subjectid)
      .filter((id) => id != null)
      .map(String),
    subjectClassIds,
  };
}

export async function assertTeacherMayAccess(
  ds: any,
  userId: string,
  classId?: string,
  subjectId?: string,
) {
  const roleRows = await runDbQuery(ds, `SELECT roles FROM users WHERE id = ? LIMIT 1`, [userId]);
  if (hasTenantWideRole(parseRoleList(roleRows?.[0]?.roles))) return;
  const scope = await getTeacherScope(ds, userId);
  if (!scope.isTeacher) return;
  const classAllowed = classId && scope.classIds.includes(String(classId));
  const subjectAllowed =
    subjectId && scope.subjectIds.includes(String(subjectId));
  const subjectClassAllowed =
    subjectAllowed &&
    (!classId ||
      scope.subjectClassIds.length === 0 ||
      scope.subjectClassIds.includes(String(classId)));
  if (!classAllowed && !subjectClassAllowed) {
    throw new ForbiddenException(
      'Teacher is not assigned to this class or subject',
    );
  }
}

export async function assertTeacherMayMarkClass(
  ds: any,
  userId: string,
  classId?: string,
) {
  const roleRows = await runDbQuery(ds, `SELECT roles FROM users WHERE id = ? LIMIT 1`, [userId]);
  if (hasTenantWideRole(parseRoleList(roleRows?.[0]?.roles))) return;
  const scope = await getTeacherScope(ds, userId);
  if (!scope.isTeacher) return;
  if (!classId) {
    throw new BadRequestException('Class is required for teacher attendance');
  }
  const allowed =
    scope.classIds.includes(String(classId)) ||
    scope.subjectClassIds.includes(String(classId));
  if (!allowed) {
    throw new ForbiddenException('Teacher is not assigned to this class');
  }
}

/**
 * Student attendance is homeroom ownership, not a general subject-teacher
 * permission. Class teachers may only access their explicitly assigned class.
 */
export async function assertClassTeacherMayAccess(
  ds: any,
  userId: string,
  classId?: string,
) {
  const rows = await runDbQuery(ds, `SELECT roles FROM users WHERE id = ? LIMIT 1`, [userId]);
  const roles = parseRoleList(rows?.[0]?.roles);
  if (hasTenantWideRole(roles)) return;
  if (!roles.includes('class_teacher')) return;

  if (!classId) {
    throw new BadRequestException('Class is required for class teacher attendance');
  }
  const scope = await getTeacherScope(ds, userId);
  if (!scope.classIds.includes(String(classId))) {
    throw new ForbiddenException('Class teacher is not assigned to this class');
  }
}
