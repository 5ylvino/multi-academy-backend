import PDFDocument = require('pdfkit');
import {
  Document,
  Packer,
  Paragraph,
  Table,
  TableCell,
  TableRow,
  TextRun,
  HeadingLevel,
  WidthType,
  BorderStyle,
} from 'docx';

type ReportMeta = {
  title?: string;
  type?: string;
  category?: string;
  scope?: string;
  scopeLabel?: string;
  period?: string;
  generatedDate?: string | null;
  status?: string;
};

type ReportRow = Record<string, string | number | null | undefined>;
type ReportOrganization = {
  name?: string | null;
  address?: string | null;
  city?: string | null;
  state?: string | null;
  country?: string | null;
  phone?: string | null;
  brandColor?: string | null;
};

function cellText(value: unknown): string {
  if (value == null) return '';
  return String(value);
}

export async function buildReportPdf(params: {
  report: ReportMeta;
  columns: string[];
  rows: ReportRow[];
  summary?: Record<string, string | number> | null;
  organization?: ReportOrganization | null;
  groupBy?: string[];
  footerText?: string;
}): Promise<Buffer> {
  const { report, columns, rows, summary, organization, groupBy = [], footerText } = params;

  return new Promise((resolve, reject) => {
    const doc = new PDFDocument({
      margin: 40,
      size: 'A4',
      layout: rows.length > 0 && columns.length > 6 ? 'landscape' : 'portrait',
    });
    const chunks: any[] = [];
    doc.on('data', (c: any) => chunks.push(c));
    doc.on('end', () => resolve(Buffer.concat(chunks)));
    doc.on('error', reject);

    const schoolAddress = [
      organization?.address,
      organization?.city,
      organization?.state,
      organization?.country,
    ].filter(Boolean).join(', ');
    if (organization?.name) {
      doc
        .fontSize(16)
        .fillColor(organization.brandColor || '#111827')
        .font('Helvetica-Bold')
        .text(organization.name, { align: 'center' });
      if (schoolAddress) doc.fontSize(9).fillColor('#4B5563').font('Helvetica').text(schoolAddress, { align: 'center' });
      if (organization.phone) doc.fontSize(9).text(`Tel: ${organization.phone}`, { align: 'center' });
      doc.moveDown(0.5);
    }
    doc.fontSize(16).fillColor('#111827').font('Helvetica-Bold').text(report.title || 'Report', { underline: false });
    doc.moveDown(0.4);
    doc.fontSize(9).fillColor('#4B5563');
    doc.text(`Type: ${report.type || '—'}   |   Category: ${report.category || '—'}`);
    doc.text(`${report.scopeLabel || 'Scope'}: ${report.scope || '—'}   |   Period: ${report.period || '—'}`);
    doc.text(
      `Generated: ${report.generatedDate || '—'}   |   Status: ${report.status || '—'}   |   Rows: ${rows.length}`,
    );
    if (summary && Object.keys(summary).length) {
      doc.moveDown(0.3);
      doc.text(
        Object.entries(summary)
          .map(([k, v]) => `${k}: ${v}`)
          .join('   |   '),
      );
    }
    doc.moveDown(0.8);

    const pageWidth = doc.page.width - doc.page.margins.left - doc.page.margins.right;
    const colCount = Math.max(columns.length, 1);
    const colWidth = pageWidth / colCount;
    const startX = doc.page.margins.left;
    let y = doc.y;

    const drawHeader = () => {
      doc.fontSize(8).fillColor('#111827').font('Helvetica-Bold');
      columns.forEach((col, i) => {
        doc.text(col, startX + i * colWidth, y, {
          width: colWidth - 4,
          ellipsis: true,
        });
      });
      y += 14;
      doc
        .moveTo(startX, y)
        .lineTo(startX + pageWidth, y)
        .strokeColor('#D1D5DB')
        .stroke();
      y += 6;
      doc.font('Helvetica').fillColor('#1F2937');
    };

    drawHeader();

    for (let rowIndex = 0; rowIndex < rows.length; rowIndex += 1) {
      const row = rows[rowIndex];
      if (y > doc.page.height - 50) {
        doc.addPage();
        y = doc.page.margins.top;
        drawHeader();
      }
      let rowHeight = 12;
      columns.forEach((col, i) => {
        const text = cellText(row[col]);
        const h = doc.heightOfString(text, { width: colWidth - 4 });
        rowHeight = Math.max(rowHeight, Math.min(h, 36));
        doc.text(text, startX + i * colWidth, y, {
          width: colWidth - 4,
          height: 36,
          ellipsis: true,
        });
      });
      y += rowHeight + 4;
      const nextRow = rows[rowIndex + 1];
      const groupEnded = groupBy.length > 0 && (
        !nextRow || groupBy.some((column) => cellText(row[column]) !== cellText(nextRow[column]))
      );
      if (groupEnded) {
        doc
          .moveTo(startX, y - 2)
          .lineTo(startX + pageWidth, y - 2)
          .strokeColor('#9CA3AF')
          .stroke();
        y += 4;
      }
    }

    if (rows.length === 0) {
      doc.fontSize(10).fillColor('#6B7280').text('No data rows for this report type.', startX, y);
    }
    if (footerText) {
      doc.moveDown(6);
      doc.fontSize(9).fillColor('#1F2937').font('Helvetica');
      doc.text(footerText, startX, doc.y, { align: 'right', width: pageWidth });
    }

    doc.end();
  });
}

export async function buildReportDocx(params: {
  report: ReportMeta;
  columns: string[];
  rows: ReportRow[];
  summary?: Record<string, string | number> | null;
}): Promise<Buffer> {
  const { report, columns, rows, summary } = params;

  const metaLines = [
    `Type: ${report.type || '—'}`,
    `Category: ${report.category || '—'}`,
    `${report.scopeLabel || 'Scope'}: ${report.scope || '—'}`,
    `Period: ${report.period || '—'}`,
    `Generated: ${report.generatedDate || '—'}`,
    `Status: ${report.status || '—'}`,
    `Rows: ${rows.length}`,
  ];
  if (summary) {
    for (const [k, v] of Object.entries(summary)) {
      metaLines.push(`${k}: ${v}`);
    }
  }

  const headerRow = new TableRow({
    children: columns.map(
      (col) =>
        new TableCell({
          children: [
            new Paragraph({
              children: [new TextRun({ text: col, bold: true, size: 18 })],
            }),
          ],
          width: { size: Math.floor(9000 / Math.max(columns.length, 1)), type: WidthType.DXA },
        }),
    ),
  });

  const dataRows =
    rows.length > 0
      ? rows.map(
          (row) =>
            new TableRow({
              children: columns.map(
                (col) =>
                  new TableCell({
                    children: [
                      new Paragraph({
                        children: [new TextRun({ text: cellText(row[col]), size: 18 })],
                      }),
                    ],
                    width: {
                      size: Math.floor(9000 / Math.max(columns.length, 1)),
                      type: WidthType.DXA,
                    },
                    borders: {
                      top: { style: BorderStyle.SINGLE, size: 4, color: 'E5E7EB' },
                      bottom: { style: BorderStyle.SINGLE, size: 4, color: 'E5E7EB' },
                      left: { style: BorderStyle.SINGLE, size: 4, color: 'E5E7EB' },
                      right: { style: BorderStyle.SINGLE, size: 4, color: 'E5E7EB' },
                    },
                  }),
              ),
            }),
        )
      : [
          new TableRow({
            children: [
              new TableCell({
                children: [
                  new Paragraph({
                    children: [
                      new TextRun({
                        text: 'No data rows for this report type.',
                        italics: true,
                        size: 18,
                      }),
                    ],
                  }),
                ],
                columnSpan: Math.max(columns.length, 1),
              }),
            ],
          }),
        ];

  const doc = new Document({
    sections: [
      {
        properties: {},
        children: [
          new Paragraph({
            text: report.title || 'Report',
            heading: HeadingLevel.HEADING_1,
          }),
          ...metaLines.map(
            (line) =>
              new Paragraph({
                children: [new TextRun({ text: line, size: 20, color: '4B5563' })],
                spacing: { after: 60 },
              }),
          ),
          new Paragraph({ text: '' }),
          new Table({
            width: { size: 9000, type: WidthType.DXA },
            rows: [headerRow, ...dataRows],
          }),
        ],
      },
    ],
  });

  return Packer.toBuffer(doc);
}
