export function mapDataToUpdateKeys<T>(data: Array<T> | T, fieldObject: Record<string, string>): Array<T> | T {

    if (Array.isArray(data)) {
      return data.map((item) => mapDataToUpdateKeys<T>(item, fieldObject)) as Array<T>;
    } else {
      return Object.entries(data).reduce((acc, [key, value]) => {
        const updatedKey = fieldObject?.[key];

          if (updatedKey) {
            return { ...acc, [updatedKey]:value };
          }
        
        return { ...acc, [key]:value };
      }, {}) as T;
    }
  }
