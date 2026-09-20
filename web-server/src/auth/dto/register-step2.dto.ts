import { IsEmail, IsString, MinLength } from 'class-validator';

export class RegisterStep2Dto {
  @IsString()
  schoolBusinessOrganisationId!: string;

  @IsEmail()
  email!: string;

  @IsString()
  @MinLength(8)
  password!: string;
}
