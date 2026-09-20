import { validateGradingBands } from './grading-matrix.service';
import { DEFAULT_GRADE_BANDS } from './grading-matrix.defaults';
import { competitionPosition } from './term-ranking.service';
import { validate } from 'class-validator';
import {
  CreateSchemeOfWorkDto,
  LinkTeacherScopeDto,
  SchemeTopicDto,
  UpdateSchemeTopicProgressDto,
  UpsertClassDto,
} from './dto/academic.dto';
import { hasUnscopedClassAccess } from './academic.service';

describe('academic grading and ranking workflow', () => {
  it('provides six complete default grading bands', () => {
    expect(DEFAULT_GRADE_BANDS).toHaveLength(6);
    expect(DEFAULT_GRADE_BANDS[0]).toEqual({
      grade_title: 'A',
      grade_name: 'Distinction',
      min: 70,
      max: 100,
    });
  });

  it('rejects invalid or duplicate grading thresholds', () => {
    expect(() =>
      validateGradingBands([
        { grade_title: 'A', grade_name: 'Distinction', min: 70, max: 100 },
        { grade_title: 'B', grade_name: 'Very Good', min: 70, max: 100 },
      ]),
    ).toThrow('unique');
    expect(() =>
      validateGradingBands([
        { grade_title: 'A', grade_name: 'Distinction', min: 101, max: 100 },
      ]),
    ).toThrow('between 0 and 100');
    expect(() =>
      validateGradingBands([
        { grade_title: 'A', grade_name: 'Distinction', min: 70, max: 100 },
        { grade_title: 'B', grade_name: 'Very Good', min: 60, max: 80 },
      ]),
    ).toThrow('must not overlap');
    expect(() => validateGradingBands([null as unknown as never])).toThrow(
      'between 0 and 100',
    );
  });

  it('uses competition ranking for tied averages', () => {
    const rows = [{ avg: 90 }, { avg: 90 }, { avg: 80 }];
    expect(rows.map((_, index) => competitionPosition(rows, index))).toEqual([
      1, 1, 3,
    ]);
  });

  it('accepts provisioned JSS and SSS levels at the API boundary', async () => {
    for (const schoolLevel of ['jss', 'sss']) {
      const dto = Object.assign(new UpsertClassDto(), {
        name: `${schoolLevel.toUpperCase()} 1`,
        code: schoolLevel.toUpperCase() + '1',
        schoolLevel,
      });
      expect(await validate(dto)).toEqual([]);
    }
  });

  it('allows a single teacher-scope request to carry both assignments', async () => {
    const dto = Object.assign(new LinkTeacherScopeDto(), {
      teacherId: 'teacher-1',
      classIds: ['class-1'],
      subjectIds: ['subject-1'],
      schoolLevels: ['jss'],
    });
    expect(await validate(dto)).toEqual([]);
  });

  it('does not hide the tenant class catalog from structure administrators', () => {
    expect(hasUnscopedClassAccess(['classes:manage'], [])).toBe(true);
    expect(hasUnscopedClassAccess(['organization:view'], [])).toBe(true);
    expect(hasUnscopedClassAccess(['classes:read'], [])).toBe(false);
    expect(hasUnscopedClassAccess(['classes:read'], ['*'])).toBe(true);
  });

  it('validates the required scheme-of-work relationships', async () => {
    const dto = Object.assign(new CreateSchemeOfWorkDto(), {
      classId: 'class-1',
      subjectId: 'subject-1',
      sessionId: 'session-1',
      termId: 'term-1',
      topics: [
        Object.assign(new SchemeTopicDto(), {
          title: 'Fractions',
          timeframe: 'Weeks 1-2',
          objective: 'Add and subtract fractions.',
        }),
      ],
    });
    expect(await validate(dto)).toEqual([]);

    const invalid = Object.assign(new CreateSchemeOfWorkDto(), {
      classId: '',
      subjectId: 'subject-1',
      sessionId: 'session-1',
      termId: 'term-1',
    });
    expect(
      (await validate(invalid)).some((error) => error.property === 'classId'),
    ).toBe(true);
  });

  it('requires a valid progress status and accepts carry-over metadata', async () => {
    const dto = Object.assign(new UpdateSchemeTopicProgressDto(), {
      status: 'carried_over',
      carryOverReason: 'The examination timetable reduced teaching time.',
    });
    expect(await validate(dto)).toEqual([]);

    const invalid = Object.assign(new UpdateSchemeTopicProgressDto(), {
      status: 'unknown',
    });
    expect(
      (await validate(invalid)).some((error) => error.property === 'status'),
    ).toBe(true);
  });
});
