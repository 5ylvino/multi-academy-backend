import { IsOptional, IsString, MinLength, MaxLength } from 'class-validator';

export class AdminResetPasswordDto {
  /** If omitted, a random 10-character temporary password is generated and returned once. */
  @IsOptional()
  @IsString()
  @MinLength(8)
  @MaxLength(128)
  password?: string;
}
