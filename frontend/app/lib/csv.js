export function downloadCsv(filename, columns, rows) {
  const csv = [
    columns.join(","),
    ...rows.map(row => columns.map(col => `"${String(row[col] ?? "").replaceAll('"', '""')}"`).join(",")),
  ].join("\n");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}
