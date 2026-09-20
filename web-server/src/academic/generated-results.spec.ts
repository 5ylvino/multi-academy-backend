import { calculateGeneratedSubjectRecord } from './academic.service';

describe('generated result algorithm', () => {
  const bands = [
    { min: 70, max: 100, grade_title: 'A', grade_name: 'Distinction' },
    { min: 0, max: 69.99, grade_title: 'F', grade_name: 'Failed' },
  ];

  it('aggregates CA and examination subcategories', () => {
    expect(calculateGeneratedSubjectRecord(
      { subjectId: 'math', subjectName: 'Math', internal_assessment: 15, examination: 60 },
      [],
      0,
      bands,
    )).toMatchObject({ caScore: 15, examScore: 60, totalScore: 75, grade_title: 'A' });
  });

  it('averages current and historical terms', () => {
    const result = calculateGeneratedSubjectRecord(
      { subjectId: 'math', subjectName: 'Math', continuous: 40, examination: 40 },
      [{ totalScore: 60 }, { totalScore: 70 }],
      2,
      bands,
    );
    expect(result).toMatchObject({
      totalScore: 80,
      firstTermTotal: 60,
      secondTermTotal: 70,
      totalAverage: 70,
      grade_title: 'A',
    });
  });
});
