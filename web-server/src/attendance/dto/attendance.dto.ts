import {
  IsIn,
  IsISO8601,
  IsNumber,
  IsOptional,
  IsString,
  Length,
  MinLength,
} from 'class-validator';
import { Type } from 'class-transformer';

export class MarkStudentAttendanceDto {
  @IsString()
  @MinLength(1)
  studentId!: string;

  @IsOptional()
  @IsString()
  classId?: string;

  @IsISO8601()
  date!: string;

  @IsIn(['present', 'absent', 'late'])
  status!: 'present' | 'absent' | 'late';
}

export class MarkStaffAttendanceDto {
  @IsOptional()
  @IsString()
  staffId?: string;

  @IsISO8601()
  date!: string;

  @IsIn(['present', 'absent', 'late'])
  status!: 'present' | 'absent' | 'late';

  @IsString()
  @MinLength(1)
  biometricAssertionId!: string;

  @IsOptional()
  @IsString()
  location?: string;

  @IsOptional()
  @Type(() => Number)
  @IsNumber()
  latitude?: number;

  @IsOptional()
  @Type(() => Number)
  @IsNumber()
  longitude?: number;
}

export class MarkStaffManualAttendanceDto {
  @IsOptional()
  @IsString()
  staffId?: string;

  @IsISO8601()
  date!: string;

  @IsIn(['present', 'absent', 'late'])
  status!: 'present' | 'absent' | 'late';

  @IsString()
  @Length(20, 20)
  verificationCode!: string;

  @IsOptional()
  @IsString()
  location?: string;

  @IsOptional()
  @Type(() => Number)
  @IsNumber()
  latitude?: number;

  @IsOptional()
  @Type(() => Number)
  @IsNumber()
  longitude?: number;
}
