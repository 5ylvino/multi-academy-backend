The two endpoints serve different purposes:


| Area              | AI Assistant                                                     | AI Support                                               |
| ----------------- | ---------------------------------------------------------------- | -------------------------------------------------------- |
| Endpoint          | `/api/v1/ai/assistant/chat`                                      | `/api/v1/ai/support/chat`                                |
| Main UI           | Full AI Assistant page at `/dashboard/ai`                        | Floating Support Chat widget                             |
| Intended users    | Staff, administrators, and authorized school users               | Parents, staff, and general portal users                 |
| Main purpose      | Answer school-operation and data questions                       | Explain how to use the system and resolve support issues |
| Typical questions | “What are this term’s fees for JSS1?” “Summarise attendance.”    | “How do I activate my account?” “How do I pay fees?”     |
| Retrieval         | Authorized school data plus relevant documents                   | Support articles plus authorized school context          |
| Restrictions      | School operations only; no invented facts or destructive actions | No academic grading advice or student comparisons        |
| Feature flag      | `ai.assistant`                                                   | `ai.support_chatbot`                                     |


The Assistant is the data-oriented school intelligence tool. It is intended to answer questions using academic, attendance, timetable, calendar, announcement, and authorized financial records.

The Support chatbot is the helpdesk-style tool. It explains navigation, activation, payments, attendance alerts, and general system usage.

Both are read-only and tenant-scoped. They do not execute SQL or modify school records.

