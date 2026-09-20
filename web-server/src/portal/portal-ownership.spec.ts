import { ForbiddenException } from '@nestjs/common';
import { runDbQuery } from '../database/db-driver.util';
import { ParentPortalService } from './parent-portal.service';
import { StudentPortalService } from './student-portal.service';

jest.mock('../database/db-driver.util', () => ({
  runDbQuery: jest.fn(),
}));

const query = runDbQuery as jest.MockedFunction<typeof runDbQuery>;

function parentService() {
  const ds = {};
  const controlPlane = {
    getTenantById: jest
      .fn()
      .mockResolvedValue({ id: 'tenant-1', dbUri: 'db://tenant-1' }),
  };
  const tenantConnections = { getOrCreate: jest.fn().mockResolvedValue(ds) };
  const flags = { assertEnabled: jest.fn().mockResolvedValue(undefined) };
  const balanceFreeze = {
    computeParentBalance: jest
      .fn()
      .mockResolvedValue({ frozen: false, wards: [] }),
    computeStudentBalance: jest.fn().mockResolvedValue({ frozen: false }),
  };
  const studentPortal = {
    getAssignmentsForStudent: jest.fn().mockResolvedValue([]),
    getScheduleForStudent: jest
      .fn()
      .mockResolvedValue({ studentId: 'child-1', schedule: [] }),
  } as unknown as StudentPortalService;

  return new ParentPortalService(
    controlPlane as any,
    tenantConnections as any,
    flags as any,
    {} as any,
    {} as any,
    balanceFreeze as any,
    {} as any,
    {} as any,
    studentPortal,
    { isEnabled: () => false } as any,
  );
}

describe('portal ownership boundaries', () => {
  beforeEach(() => query.mockReset());

  it('returns every child linked to the authenticated parent', async () => {
    query.mockResolvedValueOnce([
      { id: 'parent-1', roles: '["parent"]' },
    ] as any);
    query.mockResolvedValueOnce([
      { id: 'child-1', name: 'Ada' },
      { id: 'child-2', name: 'Bola' },
    ] as any);
    query.mockResolvedValueOnce([{ studentId: 'child-1', classId: 'class-1' }] as any);

    const result = await parentService().listWards('tenant-1', 'parent-1');

    expect(result.wards.map((ward) => ward.id)).toEqual(['child-1', 'child-2']);
    expect(result.wards[0].classIds).toEqual(['class-1']);
    expect(query.mock.calls[0][1]).toContain('SELECT id, roles FROM users');
    expect(query).toHaveBeenLastCalledWith(
      expect.anything(),
      expect.stringContaining('student_class_enrollments'),
      ['child-1', 'child-2'],
    );
  });

  it('denies a parent access to an unlinked child', async () => {
    query.mockResolvedValueOnce([
      { id: 'parent-1', roles: '["parent"]' },
    ] as any);
    query.mockResolvedValueOnce([]);

    await expect(
      parentService().wardResults('tenant-1', 'parent-1', 'child-3'),
    ).rejects.toBeInstanceOf(ForbiddenException);
  });

  it('returns an empty ward list when the parent has no linked children', async () => {
    query.mockResolvedValueOnce([{ id: 'parent-1', roles: '["parent"]' }] as any);
    query.mockResolvedValueOnce([]);

    await expect(parentService().listWards('tenant-1', 'parent-1')).resolves.toMatchObject({
      wards: [],
    });
  });

  it('keeps student access self-scoped even when other results exist', async () => {
    const service = new StudentPortalService(
      {
        getTenantById: jest
          .fn()
          .mockResolvedValue({ id: 'tenant-1', dbUri: 'db://tenant-1' }),
      } as any,
      { getOrCreate: jest.fn().mockResolvedValue({}) } as any,
      { assertEnabled: jest.fn().mockResolvedValue(undefined) } as any,
      {
        listResults: jest.fn().mockResolvedValue([
          { id: 'r1', studentId: 'student-1', status: 'approved' },
          { id: 'r2', studentId: 'student-2', status: 'approved' },
        ]),
      } as any,
      { isEnabled: () => false } as any,
    );
    query.mockResolvedValueOnce([
      { id: 'student-1', role: 'student', roles: '["student"]' },
    ] as any);
    query.mockResolvedValueOnce([
      { id: 'r1', studentId: 'student-1', releaseStatus: 'approved', studentSubjectRecords: '[]' },
    ] as any);

    await expect(service.getResults('tenant-1', 'student-1')).resolves.toEqual([
      { id: 'r1', studentId: 'student-1', releaseStatus: 'approved', studentSubjectRecords: [] },
    ]);
  });
});
