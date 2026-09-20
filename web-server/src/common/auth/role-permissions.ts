/**
 * Server-side role -> permission catalog.
 *
 * This is the single source of truth for what each role can do.
 * The frontend must never derive authority on its own; it only renders
 * what the backend grants here.
 *
 * Principle: least privilege. Finance writes stay with bursar;
 * result approval stays with head_teacher / principal;
 * custom capability overrides require roles:assign and are audited.
 */

export const ROLE_IDS = [
  'director',
  'school_admin',
  'it_admin',
  'head_teacher',
  'principal',
  'vice_principal',
  'assistant_head_teacher',
  'bursar',
  'class_teacher',
  'subject_teacher',
  'administrative_staff',
  'student',
  'parent',
  // Phase 4 ops roles (`roles.ops_staff`)
  'driver',
  'guard',
  'nurse',
] as const;

export type RoleId = (typeof ROLE_IDS)[number];

/** Roles that may authorize custom capability grants (HIPAA / audit trail). */
export const PRIVILEGE_GRANTOR_ROLES: RoleId[] = ['director', 'it_admin', 'school_admin'];

const SCHOOL_LEADER_PERMISSIONS = [
  'ai:use',
  'results:read',
  'results:view_all',
  'results:approve',
  'students:update',
  'staff:read',
  'staff_records:view',
  'dashboards:view',
  'dashboards:view_strategic',
  'reports:view',
  'reports:generate',
  'analytics:view',
  'classes:read',
  'subjects:read',
  'student_attendance:read',
  'student_attendance:view_all',
  'staff_attendance:read',
  'staff_attendance:view_all',
  'staff_attendance:create',
  'biometric:verify',
  'notifications:view',
  'assignments:read',
  'curriculum:view',
  'curriculum:manage',
  'cbt:read',
  'cbt:manage',
  'cbt:publish',
  'inventory:read',
  'inventory:manage',
  'budgets:read',
  'budgets:manage',
  'payroll:read',
  'payroll:manage',
  'analytics:view',
  'ai:use',
  'consent:read',
  'consent:manage',
  'emergency:send',
  'hostel:read',
  'hostel:manage',
  'push:register',
  'push:send',
  'sync:write',
  'timetable:read',
  'timetable:manage',
  'lesson_notes:read',
  'lesson_notes:manage',
];

export const ROLE_PERMISSIONS: Record<RoleId, string[]> = {
  /**
   * Executive oversight. Can authorize privilege exceptions (roles:assign)
   * and view sensitive audit/finance/academic data, but cannot process
   * payments, edit fee structures, or alter/approve results.
   */
  director: [
    'ai:use',
    'organization:view',
    'organization:update',
    'organization_settings:configure',
    'dashboards:view',
    'dashboards:view_strategic',
    'reports:view',
    'reports:generate',
    'reports:export',
    'analytics:view',
    'audit_logs:view',
    'staff:read',
    'staff_records:view',
    'staff_attendance:view_all',
    'students:read',
    'students:update',
    'student_records:view',
    'student_attendance:view_all',
    'results:read',
    'results:view_all',
    'classes:read',
    'subjects:read',
    'curriculum:view',
    'fees:view',
    'fee_structures:read',
    'payments:view',
    'invoices:read',
    'users:read',
    'roles:assign',
    'announcements:read',
    'announcements:create',
    'notifications:view',
    'correspondence:manage',
    'calendar:manage',
    'cbt:read',
    'cbt:manage',
    'cbt:publish',
    'transport:read',
    'transport:manage',
    'clinic:read',
    'clinic:manage',
    'inventory:read',
    'inventory:manage',
    'budgets:read',
    'budgets:manage',
  ],

  it_admin: [
    'ai:use',
    'users:create',
    'users:read',
    'users:update',
    'users:delete',
    'users:manage',
    'accounts:create',
    'accounts:manage',
    'accounts:reset',
    'roles:assign',
    'biometric:configure',
    'biometric:verify',
    'audit_logs:view',
    'system_config:view',
    'system_config:manage',
    'notifications:manage',
    'dashboards:view',
  ],

  /**
   * Org / academic structure admin. No finance writes, no result approve/alter.
   * May assign catalog roles and (with audit) custom capability overrides.
   */
  school_admin: [
    'ai:use',
    'organization:view',
    'organization:update',
    'organization_settings:configure',
    'academic_sessions:manage',
    'academic_terms:manage',
    'departments:manage',
    'school_levels:manage',
    'staff:read',
    'staff:create',
    'staff:update',
    'users:read',
    'users:create',
    'users:update',
    'roles:assign',
    'classes:manage',
    'classes:read',
    'classes:create',
    'classes:update',
    'classes:delete',
    'subjects:create',
    'subjects:read',
    'subjects:update',
    'subjects:delete',
    'results:read',
    'assignments:read',
    'curriculum:view',
    'reports:view',
    'reports:generate',
    'dashboards:view',
    'announcements:create',
    'announcements:read',
    'announcements:update',
    'announcements:delete',
    'correspondence:manage',
    'calendar:manage',
    'cbt:read',
    'cbt:manage',
    'cbt:publish',
    'transport:read',
    'transport:manage',
    'clinic:read',
    'clinic:manage',
    'inventory:read',
    'inventory:manage',
    'budgets:read',
    'budgets:manage',
    'student_attendance:read',
    'student_attendance:view_all',
    'student_attendance:create',
    'student_attendance:update',
    'staff_attendance:read',
    'staff_attendance:view_all',
    'staff_attendance:create',
    'biometric:configure',
    'biometric:verify',
    'notifications:view',
    'notifications:manage',
    // Read-only finance visibility (writes belong to bursar)
    'fees:view',
    'fee_structures:read',
    'payments:view',
    'invoices:read',
    'students:read',
    'students:create',
    'students:update',
    'student_records:view',
    'system_config:view',
    'nursery:read',
    'nursery:manage',
  ],

  administrative_staff: [
    'ai:use',
    'students:create',
    'students:read',
    'students:update',
    'student_records:view',
    'student_records:update',
    'correspondence:manage',
    'calendar:manage',
    'cbt:read',
    'cbt:manage',
    'transport:read',
    'transport:manage',
    'clinic:read',
    'clinic:manage',
    'inventory:read',
    'inventory:manage',
    'budgets:read',
    'budgets:manage',
    'announcements:read',
    'reports:view',
    'dashboards:view',
  ],

  head_teacher: [...SCHOOL_LEADER_PERMISSIONS, 'nursery:read', 'nursery:manage'],
  principal: [...SCHOOL_LEADER_PERMISSIONS],
  vice_principal: [...SCHOOL_LEADER_PERMISSIONS],

  assistant_head_teacher: [
    'ai:use',
    'results:read',
    'results:view_all',
    'students:update',
    'reports:view',
    'reports:generate',
    'analytics:view',
    'curriculum:view',
    'classes:read',
    'subjects:read',
    'staff:read',
    'staff_records:view',
    'staff_attendance:read',
    'staff_attendance:view_all',
    'staff_attendance:create',
    'dashboards:view',
    'nursery:read',
    'nursery:manage',
  ],

  class_teacher: [
    'ai:use',
    'sync:write',
    'timetable:read',
    'lesson_notes:read',
    'lesson_notes:manage',
    'cbt:read',
    'cbt:manage',
    'transport:read',
    'clinic:read',
    'student_attendance:create',
    'student_attendance:read',
    'student_attendance:update',
    'staff_attendance:create',
    'staff_attendance:read',
    'biometric:configure',
    'biometric:verify',
    'results:create',
    'results:read',
    'results:update',
    'assignments:create',
    'assignments:read',
    'assignments:update',
    'assignments:delete',
    'correspondence:manage',
    'announcements:create',
    'announcements:read',
    'students:read',
    'student_records:view',
    'classes:read',
    'subjects:read',
    'notifications:view',
    'dashboards:view',
    'nursery:read',
    'nursery:manage',
  ],

  subject_teacher: [
    'ai:use',
    'sync:write',
    'timetable:read',
    'lesson_notes:read',
    'lesson_notes:manage',
    'cbt:read',
    'cbt:manage',
    'transport:read',
    'clinic:read',
    'results:create',
    'results:read',
    'results:update',
    'staff_attendance:create',
    'staff_attendance:read',
    'biometric:configure',
    'biometric:verify',
    'assignments:create',
    'assignments:read',
    'assignments:update',
    'assignments:delete',
    'students:read',
    'student_records:view',
    'classes:read',
    'subjects:read',
    'announcements:read',
    'notifications:view',
    'dashboards:view',
  ],

  bursar: [
    'inventory:read',
    'inventory:manage',
    'budgets:read',
    'budgets:manage',
    'payroll:read',
    'payroll:manage',
    'analytics:view',
    'fee_structures:create',
    'fee_structures:read',
    'fee_structures:update',
    'fee_structures:delete',
    'fees:view',
    'fees:manage',
    'payments:view',
    'payments:process',
    'biometric:verify',
    'staff_attendance:create',
    'staff_attendance:read',
    'invoices:create',
    'invoices:read',
    'scholarships:manage',
    'refunds:process',
    'reports:view',
    'reports:generate',
    'reports:export',
    'notifications:view',
    'dashboards:view',
    'students:read',
  ],

  student: [
    'ai:use',
    'consent:read',
    'hostel:read',
    'push:register',
    'timetable:read',
    'lesson_notes:read',
    'cbt:read',
    'cbt:take',
    'transport:read',
    'clinic:read',
    'results:read',
    'student_attendance:read',
    'assignments:read',
    'announcements:read',
    'fees:view',
    'notifications:view',
    'dashboards:view',
  ],

  parent: [
    'ai:use',
    'consent:read',
    'consent:respond',
    'hostel:read',
    'push:register',
    'timetable:read',
    'lesson_notes:read',
    'cbt:read',
    'transport:read',
    'clinic:read',
    'results:read',
    'student_attendance:read',
    'assignments:read',
    'announcements:read',
    'fees:view',
    'payments:process',
    'biometric:verify',
    'notifications:view',
    'correspondence:manage',
    'dashboards:view',
  ],

  driver: [
    'transport:read',
    'dashboards:view',
    'notifications:view',
    'students:read',
    'announcements:read',
  ],

  guard: [
    'dashboards:view',
    'notifications:view',
    'biometric:verify',
    'students:read',
    'announcements:read',
  ],

  nurse: [
    'clinic:read',
    'clinic:manage',
    'dashboards:view',
    'notifications:view',
    'students:read',
    'results:read',
    'announcements:read',
  ],
};

/** Flat set of every permission string used by any role (for override allowlisting). */
export const KNOWN_PERMISSIONS: ReadonlySet<string> = new Set(
  Object.values(ROLE_PERMISSIONS).flat(),
);

export function isValidRole(role: string): role is RoleId {
  return (ROLE_IDS as readonly string[]).includes(role);
}

/** Returns the subset of provided roles that are not recognised. */
export function findInvalidRoles(roles: string[]): string[] {
  return roles.filter((role) => !isValidRole(role));
}

/** Derives the effective permission set for a list of roles (union, deduplicated). */
export function derivePermissionsForRoles(roles: string[]): string[] {
  const permissions = new Set<string>();
  for (const role of roles) {
    if (isValidRole(role)) {
      for (const permission of ROLE_PERMISSIONS[role]) {
        permissions.add(permission);
      }
    }
  }
  return [...permissions];
}

/** True if every capability is a known catalog permission (or '*" is rejected). */
export function findUnknownCapabilities(capabilities: string[]): string[] {
  return capabilities.filter((cap) => {
    if (!cap || typeof cap !== 'string') return true;
    const trimmed = cap.trim();
    if (!trimmed || trimmed === '*') return true;
    return !KNOWN_PERMISSIONS.has(trimmed);
  });
}

/**
 * Capabilities that must never be granted as custom overrides.
 * These powers are only available through the matching role (bursar, it_admin, etc.).
 * Prevents roles:assign holders from silently reconstituting privileged roles via capabilities[].
 */
export const ROLE_ONLY_CAPABILITIES: ReadonlySet<string> = new Set([
  'payments:process',
  'refunds:process',
  'fees:manage',
  'fee_structures:create',
  'fee_structures:update',
  'fee_structures:delete',
  'scholarships:manage',
  'invoices:create',
  'results:approve',
  'results:update',
  'results:create',
  'users:manage',
  'users:delete',
  'users:create',
  'accounts:create',
  'accounts:manage',
  'accounts:reset',
  'roles:assign',
  'roles:manage',
  'system_config:manage',
  'audit_logs:view',
  'ai_features:configure',
]);

export function findRoleOnlyCapabilities(capabilities: string[]): string[] {
  return capabilities.filter((cap) => ROLE_ONLY_CAPABILITIES.has(cap.trim()));
}

export function arraysEqualAsSets(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const setB = new Set(b);
  return a.every((x) => setB.has(x));
}

/** True when the actor holds unrestricted authority (primary owner). */
export function actorHasWildcard(actor: {
  permissions?: string[];
  capabilities?: string[];
}): boolean {
  const granted = [...(actor.permissions || []), ...(actor.capabilities || [])];
  return granted.includes('*');
}

export function actorHasPermission(
  actor: { permissions?: string[]; capabilities?: string[] },
  permission: string,
): boolean {
  const granted = [...(actor.permissions || []), ...(actor.capabilities || [])];
  if (granted.includes('*')) return true;
  if (granted.includes(permission)) return true;
  const [resource] = permission.split(':');
  return granted.includes(`${resource}:manage`) || granted.includes(`${resource}:*`);
}
