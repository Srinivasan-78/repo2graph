import { MetricItem } from "../types/dashboard";

export function renderDataTable(rows: MetricItem[]): string {
    const tableRows = rows.map(r => `<tr><td>${r.id}</td><td>${r.label}</td><td>${r.value}</td></tr>`);
    return `<table><thead><tr><th>ID</th><th>Metric</th><th>Value</th></tr></thead><tbody>${tableRows.join("")}</tbody></table>`;
}

