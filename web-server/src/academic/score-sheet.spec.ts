import { calculateScoreSheetGrade } from './academic.service';

describe('score sheets', () => {
  it('calculates a rounded percentage grade', () => {
    expect(calculateScoreSheetGrade(17, 30)).toBe(57);
    expect(calculateScoreSheetGrade(2, 3)).toBe(67);
  });

  it('keeps an unentered score blank', () => {
    expect(calculateScoreSheetGrade(null, 100)).toBeNull();
    expect(calculateScoreSheetGrade(undefined, 100)).toBeNull();
  });

  it('rejects an invalid obtainable score', () => {
    expect(calculateScoreSheetGrade(10, 0)).toBeNull();
    expect(calculateScoreSheetGrade(10, Number.NaN)).toBeNull();
  });
});
