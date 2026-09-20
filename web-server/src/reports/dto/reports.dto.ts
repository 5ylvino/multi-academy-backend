import {
  IsIn,
  IsISO8601,
  IsNumber,
  IsOptional,
  IsString,
  MaxLength,
  Min,
  MinLength,
} from 'class-validator';

export const REPORT_CATEGORIES = ['financial', 'academic', 'attendance', 'organization'] as const;
export const REPORT_STATUSES = ['draft', 'final'] as const;

export class CreateReportDto {
  @IsString()
  @MinLength(1)
  @MaxLength(255)
  title!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(128)
  type!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(128)
  scope!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(128)
  period!: string;

  @IsOptional()
  @IsISO8601()
  generatedDate?: string;

  @IsOptional()
  @IsNumber()
  @Min(0)
  totalAmount?: number;

  @IsOptional()
  @IsIn(REPORT_STATUSES)
  status?: (typeof REPORT_STATUSES)[number];

  @IsIn(REPORT_CATEGORIES)
  category!: (typeof REPORT_CATEGORIES)[number];
}
