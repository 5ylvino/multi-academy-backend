import { validateExamBody } from './cbt.service';

describe('CBT exam validation', () => {
  it('rejects invalid questions and durations', () => {
    expect(() =>
      validateExamBody({
        title: 'Test',
        durationMinutes: 0,
        questions: [],
      }),
    ).toThrow('between 1 and 480');
    expect(() =>
      validateExamBody({
        title: 'Test',
        questions: [{ prompt: 'Q', options: ['A', 'B'], correctIndex: 2 }],
      }),
    ).toThrow('valid correct answer');
  });

  it('accepts a valid multiple-choice assessment', () => {
    expect(() =>
      validateExamBody({
        title: 'Mathematics',
        durationMinutes: 30,
        questions: [{ prompt: '2+2?', options: ['3', '4'], correctIndex: 1 }],
      }),
    ).not.toThrow();
  });
});
