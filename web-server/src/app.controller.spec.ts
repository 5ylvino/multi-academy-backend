import { Test, TestingModule } from '@nestjs/testing';
import { AppController } from './app.controller';
import { AppService } from './app.service';
import { ControlDbService } from './database/control-db.service';
import { RuntimeConfigRedisStore } from './platform-config/runtime-config-redis.store';

describe('AppController', () => {
  let appController: AppController;

  beforeEach(async () => {
    const app: TestingModule = await Test.createTestingModule({
      controllers: [AppController],
      providers: [
        AppService,
        {
          provide: ControlDbService,
          useValue: { ping: jest.fn(async () => true) },
        },
        {
          provide: RuntimeConfigRedisStore,
          useValue: { isEnabled: () => false, ping: jest.fn(async () => true) },
        },
      ],
    }).compile();

    appController = app.get<AppController>(AppController);
  });

  describe('root', () => {
    it('should return "Hello World!"', () => {
      expect(appController.getHello()).toBe('Hello World!');
    });
  });
});
