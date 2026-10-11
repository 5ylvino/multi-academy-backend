import {
  actorHasPermission,
  actorHasWildcard,
  arraysEqualAsSets,
  derivePermissionsForRoles,
  findInvalidRoles,
  findRoleOnlyCapabilities,
  findUnknownCapabilities,
  isValidRole,
  ROLE_PERMISSIONS,
} from './role-permissions';

describe('role-permissions catalog', () => {
  it('includes all required school roles', () => {
    const required = [
      'director',
      'school_admin',
      'it_admin',
      'head_teacher',
      'principal',
      'assistant_head_teacher',
      'bursar',
      'class_teacher',
      'subject_teacher',
      'administrative_staff',
      'student',
      'parent',
    ];
    for (const role of required) {
      expect(isValidRole(role)).toBe(true);
      expect(
        ROLE_PERMISSIONS[role as keyof typeof ROLE_PERMISSIONS]?.length,
      ).toBeGreaterThan(0);
    }
  });

  it('lets it_admin create users and read org context for that flow', () => {
    const perms = new Set(ROLE_PERMISSIONS.it_admin);
    expect(perms.has('users:create')).toBe(true);
    expect(perms.has('users:manage')).toBe(true);
    expect(perms.has('organization:view')).toBe(true);
    expect(perms.has('roles:assign')).toBe(true);
  });

  it('lets it_admin load academic catalogs for Additional Details user provisioning', () => {
    const perms = new Set(ROLE_PERMISSIONS.it_admin);
    for (const required of [
      'classes:read',
      'classes:create',
      'classes:update',
      'subjects:read',
      'subjects:create',
      'students:read',
      'students:update',
    ]) {
      expect(perms.has(required)).toBe(true);
    }
    // Keep destructive academic structure ops with school_admin.
    expect(perms.has('classes:delete')).toBe(false);
    expect(perms.has('subjects:delete')).toBe(false);
    expect(perms.has('results:approve')).toBe(false);
    expect(perms.has('payments:process')).toBe(false);
  });

  it('gives bursar and administrative_staff class catalog reads for their workflows', () => {
    expect(ROLE_PERMISSIONS.bursar).toContain('classes:read');
    expect(ROLE_PERMISSIONS.administrative_staff).toContain('classes:read');
  });

  it('keeps finance writes off school_admin and director', () => {
    for (const role of ['school_admin', 'director'] as const) {
      const perms = new Set(ROLE_PERMISSIONS[role]);
      expect(perms.has('payments:process')).toBe(false);
      expect(perms.has('fee_structures:create')).toBe(false);
      expect(perms.has('refunds:process')).toBe(false);
      expect(perms.has('results:approve')).toBe(false);
    }
  });

  it('keeps teacher entry separate from principal approval', () => {
    expect(ROLE_PERMISSIONS.class_teacher).toContain('results:create');
    expect(ROLE_PERMISSIONS.subject_teacher).toContain('results:update');
    expect(ROLE_PERMISSIONS.class_teacher).not.toContain('results:approve');
    expect(ROLE_PERMISSIONS.subject_teacher).not.toContain('results:approve');
    expect(ROLE_PERMISSIONS.principal).toContain('results:approve');
  });

  it('keeps nursery permissions with primary leadership only', () => {
    expect(ROLE_PERMISSIONS.head_teacher).toContain('nursery:read');
    expect(ROLE_PERMISSIONS.assistant_head_teacher).toContain('nursery:read');
    expect(ROLE_PERMISSIONS.principal).not.toContain('nursery:read');
    expect(ROLE_PERMISSIONS.vice_principal).not.toContain('nursery:read');
  });

  it('gives parents only linked-ward-facing capabilities', () => {
    expect(ROLE_PERMISSIONS.parent).toContain('results:read');
    expect(ROLE_PERMISSIONS.parent).toContain('payments:process');
    expect(ROLE_PERMISSIONS.parent).not.toContain('results:approve');
    expect(ROLE_PERMISSIONS.parent).not.toContain('users:manage');
  });

  it('rejects unknown and wildcard custom capabilities', () => {
    expect(findUnknownCapabilities(['fees:view'])).toEqual([]);
    expect(findUnknownCapabilities(['*', 'not:real', 'fees:view'])).toEqual([
      '*',
      'not:real',
    ]);
  });

  it('blocks role-only capabilities from custom overrides', () => {
    expect(
      findRoleOnlyCapabilities([
        'fees:view',
        'payments:process',
        'roles:assign',
      ]),
    ).toEqual(['payments:process', 'roles:assign']);
  });

  it('derives bursar finance permissions from role only', () => {
    const perms = derivePermissionsForRoles(['bursar']);
    expect(perms).toContain('payments:process');
    expect(perms).toContain('fee_structures:create');
  });

  it('compares role sets order-independently', () => {
    expect(arraysEqualAsSets(['a', 'b'], ['b', 'a'])).toBe(true);
    expect(arraysEqualAsSets(['a'], ['a', 'b'])).toBe(false);
  });

  it('detects wildcard and roles:assign via actor helpers', () => {
    expect(actorHasWildcard({ permissions: ['*'], capabilities: [] })).toBe(
      true,
    );
    expect(
      actorHasPermission(
        { permissions: ['roles:assign'], capabilities: [] },
        'roles:assign',
      ),
    ).toBe(true);
    expect(
      actorHasPermission(
        { permissions: ['users:read'], capabilities: [] },
        'roles:assign',
      ),
    ).toBe(false);
    expect(findInvalidRoles(['director', 'nope'])).toEqual(['nope']);
  });
});
