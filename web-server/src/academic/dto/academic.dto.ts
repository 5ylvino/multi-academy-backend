import {
  IsArray,
  ArrayMinSize,
  IsBoolean,
  IsIn,
  IsInt,
  IsISO8601,
  IsNumber,
  IsOptional,
  IsString,
  Max,
  MaxLength,
  Min,
  MinLength,
  ValidateNested,
} from 'class-validator';
import { Type } from 'class-transformer';

// `jss` and `sss` are persisted by tenant provisioning. Keep `secondary` as
// the aggregate value used by older clients and accept all three spellings at
// the API boundary.
export const SCHOOL_LEVELS = [
  'primary',
  'secondary',
  'nursery',
  'jss',
  'sss',
] as const;
export const RESULT_STATUSES = ['draft', 'submitted', 'approved'] as const;
export const RESULT_UPSERT_STATUSES = ['draft', 'submitted'] as const;
export const TERM_CODES = ['first', 'second', 'third'] as const;

export class SubjectCategoryDto {
  @IsString()
  @MinLength(1)
  @MaxLength(128)
  name!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(128)
  slug!: string;
}

export const SCORE_SHEET_STATUSES = ['draft', 'published'] as const;

export class ScoreSheetEntryDto {
  @IsOptional()
  @IsString()
  id?: string;

  @IsString()
  @MinLength(1)
  studentId!: string;

  @IsOptional()
  @IsNumber()
  @Min(0)
  studentScore?: number | null;

  @IsNumber()
  @Min(0.01)
  obtainableScore!: number;
}

export class ScoreSheetBatchDto {
  @IsString()
  @MinLength(1)
  sessionId!: string;

  @IsString()
  @MinLength(1)
  termId!: string;

  @IsString()
  @MinLength(1)
  classId!: string;

  @IsString()
  @MinLength(1)
  subjectId!: string;

  @IsString()
  @MinLength(1)
  categoryId!: string;

  @IsOptional()
  @IsString()
  subcategoryId?: string | null;

  @IsOptional()
  @IsIn(SCORE_SHEET_STATUSES)
  status?: (typeof SCORE_SHEET_STATUSES)[number];

  @IsArray()
  @ValidateNested({ each: true })
  @Type(() => ScoreSheetEntryDto)
  entries!: ScoreSheetEntryDto[];
}

export class ScoreSheetSubcategoryDto {
  @IsString()
  @MinLength(1)
  @MaxLength(128)
  name!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(128)
  slug!: string;
}

export class ScoreSheetCategoryDto {
  @IsString()
  @MinLength(1)
  @MaxLength(128)
  name!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(128)
  slug!: string;

  @IsOptional()
  @IsArray()
  @ValidateNested({ each: true })
  @Type(() => ScoreSheetSubcategoryDto)
  subcategories?: ScoreSheetSubcategoryDto[];
}

export class UpsertClassDto {
  @IsString()
  @MinLength(1)
  @MaxLength(255)
  name!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(64)
  code!: string;

  @IsIn(SCHOOL_LEVELS)
  schoolLevel!: (typeof SCHOOL_LEVELS)[number];

  @IsOptional()
  @IsString()
  classTeacherId?: string;

  @IsOptional()
  @IsInt()
  @Min(1)
  @Max(1000)
  capacity?: number | null;

  @IsOptional()
  @IsBoolean()
  isActive?: boolean;
}

export class UpsertSubjectDto {
  @IsString()
  @MinLength(1)
  @MaxLength(255)
  name!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(64)
  code!: string;

  @IsIn(SCHOOL_LEVELS)
  schoolLevel!: (typeof SCHOOL_LEVELS)[number];

  @IsOptional()
  @IsString()
  @MaxLength(2000)
  description?: string;

  @IsOptional()
  @IsString()
  categoryId?: string;

  @IsOptional()
  @IsArray()
  @IsString({ each: true })
  classIds?: string[];

  @IsOptional()
  @IsBoolean()
  isActive?: boolean;
}

export class UpsertAssignmentDto {
  @IsString()
  @MinLength(1)
  @MaxLength(255)
  title!: string;

  @IsOptional()
  @IsString()
  @MaxLength(5000)
  description?: string;

  @IsOptional()
  @IsString()
  classId?: string;

  @IsOptional()
  @IsString()
  subjectId?: string;

  @IsISO8601()
  dueDate!: string;

  @IsOptional()
  @IsBoolean()
  isActive?: boolean;
}

export class UpsertResultDto {
  @IsOptional()
  @IsString()
  studentId?: string;

  @IsString()
  @MinLength(1)
  classId!: string;

  @IsString()
  @MinLength(1)
  subjectId!: string;

  @IsString()
  @MinLength(1)
  termId!: string;

  @IsOptional()
  @IsNumber()
  @Min(0)
  @Max(100)
  caScore?: number;

  @IsOptional()
  @IsNumber()
  @Min(0)
  @Max(100)
  examScore?: number;

  @IsOptional()
  @IsNumber()
  @Min(0)
  @Max(100)
  totalScore?: number;

  @IsOptional()
  @IsString()
  @MaxLength(8)
  grade?: string;

  @IsOptional()
  @IsIn(RESULT_UPSERT_STATUSES)
  status?: (typeof RESULT_UPSERT_STATUSES)[number];
}

export class UpdateClassDto {
  @IsOptional()
  @IsString()
  @MinLength(1)
  @MaxLength(255)
  name?: string;

  @IsOptional()
  @IsString()
  @MinLength(1)
  @MaxLength(64)
  code?: string;

  @IsOptional()
  @IsIn(SCHOOL_LEVELS)
  schoolLevel?: (typeof SCHOOL_LEVELS)[number];

  @IsOptional()
  @IsString()
  classTeacherId?: string;

  @IsOptional()
  @IsInt()
  @Min(1)
  @Max(1000)
  capacity?: number | null;

  @IsOptional()
  @IsBoolean()
  isActive?: boolean;
}

export class UpdateSubjectDto {
  @IsOptional()
  @IsString()
  @MinLength(1)
  @MaxLength(255)
  name?: string;

  @IsOptional()
  @IsString()
  @MinLength(1)
  @MaxLength(64)
  code?: string;

  @IsOptional()
  @IsIn(SCHOOL_LEVELS)
  schoolLevel?: (typeof SCHOOL_LEVELS)[number];

  @IsOptional()
  @IsString()
  @MaxLength(2000)
  description?: string;

  @IsOptional()
  @IsString()
  categoryId?: string | null;

  @IsOptional()
  @IsArray()
  @IsString({ each: true })
  classIds?: string[];

  @IsOptional()
  @IsBoolean()
  isActive?: boolean;
}

export class UpdateAssignmentDto {
  @IsOptional()
  @IsString()
  @MinLength(1)
  @MaxLength(255)
  title?: string;

  @IsOptional()
  @IsString()
  @MaxLength(5000)
  description?: string;

  @IsOptional()
  @IsString()
  classId?: string;

  @IsOptional()
  @IsString()
  subjectId?: string;

  @IsOptional()
  @IsISO8601()
  dueDate?: string;

  @IsOptional()
  @IsBoolean()
  isActive?: boolean;
}

export class UpsertAssignmentSubmissionDto {
  @IsString()
  @MinLength(1)
  studentId!: string;

  @IsOptional()
  @IsIn(['submitted', 'pending', 'late', 'graded'])
  status?: 'submitted' | 'pending' | 'late' | 'graded';

  @IsOptional()
  @IsNumber()
  @Min(0)
  @Max(100)
  score?: number;

  @IsOptional()
  @IsString()
  @MaxLength(5000)
  note?: string;
}

export class UpdateResultDto {
  @IsOptional()
  @IsString()
  studentId?: string;

  @IsOptional()
  @IsString()
  @MinLength(1)
  classId?: string;

  @IsOptional()
  @IsString()
  @MinLength(1)
  subjectId?: string;

  @IsOptional()
  @IsString()
  @MinLength(1)
  termId?: string;

  @IsOptional()
  @IsNumber()
  @Min(0)
  @Max(100)
  caScore?: number;

  @IsOptional()
  @IsNumber()
  @Min(0)
  @Max(100)
  examScore?: number;

  @IsOptional()
  @IsNumber()
  @Min(0)
  @Max(100)
  totalScore?: number;

  @IsOptional()
  @IsString()
  @MaxLength(8)
  grade?: string;

  @IsOptional()
  @IsIn(RESULT_UPSERT_STATUSES)
  status?: (typeof RESULT_UPSERT_STATUSES)[number];
}

export class CreateSessionDto {
  @IsString()
  @MinLength(1)
  @MaxLength(128)
  name!: string;

  @IsOptional()
  @IsString()
  @MaxLength(32)
  startDate?: string;

  @IsOptional()
  @IsString()
  @MaxLength(32)
  endDate?: string;

  @IsOptional()
  @IsBoolean()
  isCurrent?: boolean;
}

export class UpdateSessionDto {
  @IsOptional()
  @IsString()
  @MinLength(1)
  @MaxLength(128)
  name?: string;

  @IsOptional()
  @IsString()
  @MaxLength(32)
  startDate?: string | null;

  @IsOptional()
  @IsString()
  @MaxLength(32)
  endDate?: string | null;

  @IsOptional()
  @IsBoolean()
  isCurrent?: boolean;
}

export class CreateTermDto {
  @IsString()
  @MinLength(1)
  sessionId!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(64)
  name!: string;

  @IsIn(TERM_CODES)
  code!: (typeof TERM_CODES)[number];

  @IsOptional()
  @IsString()
  @MaxLength(32)
  startDate?: string;

  @IsOptional()
  @IsString()
  @MaxLength(32)
  endDate?: string;

  @IsOptional()
  @IsBoolean()
  isCurrent?: boolean;
}

export class UpdateTermDto {
  @IsOptional()
  @IsString()
  @MinLength(1)
  sessionId?: string;

  @IsOptional()
  @IsString()
  @MinLength(1)
  @MaxLength(64)
  name?: string;

  @IsOptional()
  @IsIn(TERM_CODES)
  code?: (typeof TERM_CODES)[number];

  @IsOptional()
  @IsString()
  @MaxLength(32)
  startDate?: string | null;

  @IsOptional()
  @IsString()
  @MaxLength(32)
  endDate?: string | null;

  @IsOptional()
  @IsBoolean()
  isCurrent?: boolean;
}

export class ApproveResultsBatchDto {
  @IsOptional()
  @IsString()
  studentId?: string;

  @IsOptional()
  @IsString()
  classId?: string;

  @IsOptional()
  @IsString()
  termId?: string;
}

export class LinkTeacherClassesDto {
  @IsString()
  @MinLength(1)
  teacherId!: string;

  @IsArray()
  @IsString({ each: true })
  classIds!: string[];
}

export class LinkTeacherSubjectsDto {
  @IsString()
  @MinLength(1)
  teacherId!: string;

  @IsArray()
  @IsString({ each: true })
  subjectIds!: string[];
}

export class LinkTeacherScopeDto {
  @IsString()
  @MinLength(1)
  teacherId!: string;

  @IsOptional()
  @IsArray()
  @IsString({ each: true })
  classIds?: string[];

  @IsOptional()
  @IsArray()
  @IsString({ each: true })
  subjectIds?: string[];

  @IsOptional()
  @IsArray()
  @IsIn(SCHOOL_LEVELS, { each: true })
  schoolLevels?: Array<(typeof SCHOOL_LEVELS)[number]>;
}

export class LinkParentStudentsDto {
  @IsString()
  @MinLength(1)
  parentId!: string;

  @IsArray()
  @IsString({ each: true })
  studentIds!: string[];
}

export class EnrollStudentsDto {
  @IsArray()
  @IsString({ each: true })
  studentIds!: string[];
}

export const SCHEME_TOPIC_STATUSES = [
  'planned',
  'in_progress',
  'covered',
  'carried_over',
] as const;

export class SchemeTopicDto {
  @IsString()
  @MinLength(1)
  @MaxLength(255)
  title!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(128)
  timeframe!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(5000)
  objective!: string;

  @IsOptional()
  @IsString()
  @MaxLength(5000)
  activities?: string;

  @IsOptional()
  @IsString()
  @MaxLength(5000)
  resources?: string;

  @IsOptional()
  @IsString()
  @MaxLength(5000)
  assessment?: string;

  @IsOptional()
  @IsInt()
  @Min(0)
  orderIndex?: number;
}

export class CreateSchemeOfWorkDto {
  @IsString()
  @MinLength(1)
  classId!: string;

  @IsString()
  @MinLength(1)
  subjectId!: string;

  @IsString()
  @MinLength(1)
  sessionId!: string;

  @IsString()
  @MinLength(1)
  termId!: string;

  @IsOptional()
  @IsString()
  @MaxLength(255)
  title?: string;

  @IsArray()
  @ArrayMinSize(1)
  @ValidateNested({ each: true })
  @Type(() => SchemeTopicDto)
  topics!: SchemeTopicDto[];
}

export class UpdateSchemeOfWorkDto {
  @IsOptional()
  @IsString()
  @MinLength(1)
  classId?: string;

  @IsOptional()
  @IsString()
  @MinLength(1)
  subjectId?: string;

  @IsOptional()
  @IsString()
  @MinLength(1)
  sessionId?: string;

  @IsOptional()
  @IsString()
  @MinLength(1)
  termId?: string;

  @IsOptional()
  @IsString()
  @MaxLength(255)
  title?: string;
}

export class UpdateSchemeTopicDto {
  @IsOptional()
  @IsString()
  @MinLength(1)
  title?: string;

  @IsOptional()
  @IsString()
  @MaxLength(128)
  timeframe?: string;

  @IsOptional()
  @IsString()
  @MaxLength(5000)
  objective?: string;

  @IsOptional()
  @IsString()
  @MaxLength(5000)
  activities?: string;

  @IsOptional()
  @IsString()
  @MaxLength(5000)
  resources?: string;

  @IsOptional()
  @IsString()
  @MaxLength(5000)
  assessment?: string;

  @IsOptional()
  @IsInt()
  @Min(0)
  orderIndex?: number;
}

export class UpdateSchemeTopicProgressDto {
  @IsIn(SCHEME_TOPIC_STATUSES)
  status!: (typeof SCHEME_TOPIC_STATUSES)[number];

  @IsOptional()
  @IsBoolean()
  coveredBeforeAssessment?: boolean;

  @IsOptional()
  @IsBoolean()
  coveredBeforeExamination?: boolean;

  @IsOptional()
  @IsBoolean()
  isNext?: boolean;

  @IsOptional()
  @IsString()
  @MaxLength(2000)
  carryOverReason?: string;
}
