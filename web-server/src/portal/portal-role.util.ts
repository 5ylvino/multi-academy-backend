export function normalizePortalRoles(
  legacyRole: unknown,
  rawRoles: unknown,
): string[] {
  let roles: unknown[] = [];
  if (Array.isArray(rawRoles)) {
    roles = rawRoles;
  } else if (typeof rawRoles === 'string') {
    try {
      const parsed: unknown = JSON.parse(rawRoles);
      roles = Array.isArray(parsed) ? parsed : [rawRoles];
    } catch {
      roles = [rawRoles];
    }
  }

  return [...roles, legacyRole]
    .map((role) =>
      String(role || '')
        .trim()
        .toLowerCase()
        .replace(/[\s-]+/g, '_'),
    )
    .filter(Boolean);
}

export function hasPortalRole(
  legacyRole: unknown,
  rawRoles: unknown,
  role: 'parent' | 'student',
): boolean {
  return normalizePortalRoles(legacyRole, rawRoles).includes(role);
}
