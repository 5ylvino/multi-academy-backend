export type GradeBand = {
  grade_title: string;
  grade_name: string;
  min: number;
  max: number;
};

export const DEFAULT_GRADE_BANDS: GradeBand[] = [
  { grade_title: 'A', grade_name: 'Distinction', min: 70, max: 100 },
  { grade_title: 'B', grade_name: 'Very Good', min: 60, max: 69 },
  { grade_title: 'C', grade_name: 'Credit', min: 50, max: 59 },
  { grade_title: 'D', grade_name: 'Poor', min: 40, max: 49 },
  { grade_title: 'E', grade_name: 'Very Poor', min: 30, max: 39 },
  { grade_title: 'F', grade_name: 'Failed', min: 20, max: 29 },
];

export const DEFAULT_GRADING_MATRIX = {
  caWeight: 40,
  examWeight: 60,
  gradeBands: DEFAULT_GRADE_BANDS,
};
