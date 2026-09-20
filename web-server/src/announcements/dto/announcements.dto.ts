import {
  IsBoolean,
  IsIn,
  IsOptional,
  IsString,
  MaxLength,
  MinLength,
} from 'class-validator';

export const ANNOUNCEMENT_AUDIENCES = ['all', 'staff', 'students', 'parents'] as const;
export const ANNOUNCEMENT_SCHOOL_LEVELS = ['primary', 'secondary', 'nursery'] as const;

export class CreateAnnouncementDto {
  @IsString()
  @MinLength(1)
  @MaxLength(255)
  title!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(10000)
  body!: string;

  @IsOptional()
  @IsIn(ANNOUNCEMENT_AUDIENCES)
  audience?: (typeof ANNOUNCEMENT_AUDIENCES)[number];

  @IsOptional()
  @IsIn(ANNOUNCEMENT_SCHOOL_LEVELS)
  schoolLevel?: (typeof ANNOUNCEMENT_SCHOOL_LEVELS)[number];

  @IsOptional()
  @IsBoolean()
  isPublished?: boolean;

  @IsOptional()
  @IsBoolean()
  isCritical?: boolean;
}

export class UpdateAnnouncementDto {
  @IsOptional()
  @IsString()
  @MinLength(1)
  @MaxLength(255)
  title?: string;

  @IsOptional()
  @IsString()
  @MinLength(1)
  @MaxLength(10000)
  body?: string;

  @IsOptional()
  @IsIn(ANNOUNCEMENT_AUDIENCES)
  audience?: (typeof ANNOUNCEMENT_AUDIENCES)[number];

  @IsOptional()
  @IsIn(ANNOUNCEMENT_SCHOOL_LEVELS)
  schoolLevel?: (typeof ANNOUNCEMENT_SCHOOL_LEVELS)[number] | null;

  @IsOptional()
  @IsBoolean()
  isPublished?: boolean;

  @IsOptional()
  @IsBoolean()
  isCritical?: boolean;
}
