import {
  IsArray,
  IsBoolean,
  IsEmail,
  IsOptional,
  IsString,
  MinLength,
  ValidateIf,
} from 'class-validator';

export class UpdateUserDto {
  @IsOptional()
  @IsString()
  @MinLength(2)
  name?: string;

  @IsOptional()
  @IsEmail()
  email?: string;

  @IsOptional()
  @IsArray()
  @IsString({ each: true })
  roles?: string[];

  /** Extra capability overrides beyond those derived from roles. */
  @IsOptional()
  @IsArray()
  @IsString({ each: true })
  capabilities?: string[];

  /**
   * Required when roles or custom capabilities change.
   * Empty string is treated as absent (does not fail MinLength).
   */
  @ValidateIf((_, v) => v !== undefined && v !== null && String(v).trim() !== '')
  @IsString()
  @MinLength(3)
  privilegeChangeReason?: string;

  @IsOptional()
  @IsBoolean()
  isActive?: boolean;

  @IsOptional()
  @IsString()
  @MinLength(8)
  password?: string;

  @IsOptional()
  @IsString()
  phone?: string;

  /** Subject category/major for SSS students (for example, science). */
  @IsOptional()
  @IsString()
  major?: string | null;

  @IsOptional()
  @IsString()
  schoolLevel?: string | null;

  @IsOptional()
  @IsString()
  gender?: string | null;

  /** Passport-size profile photo as a data-URL (image/jpeg or image/png base64). */
  @IsOptional()
  @IsString()
  passportPhoto?: string | null;
}
