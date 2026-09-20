import {
  calculatePerformancePercentage,
  isLowPerformance,
} from './academic.service';

describe('performance alerts', () => {
  it('calculates score percentage from the obtainable mark', () => {
    expect(calculatePerformancePercentage(8, 20)).toBe(40);
    expect(calculatePerformancePercentage(10, 20)).toBe(50);
    expect(calculatePerformancePercentage('4.00', 10)).toBe(40);
    expect(calculatePerformancePercentage('', 10)).toBeNull();
  });

  it('alerts only when a recorded score is below 50 percent', () => {
    expect(isLowPerformance(8, 20)).toBe(true);
    expect(isLowPerformance(10, 20)).toBe(false);
    expect(isLowPerformance(null, 20)).toBe(false);
    expect(isLowPerformance(1, 0)).toBe(false);
  });
});
