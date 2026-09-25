import { MetricItem } from "../types/dashboard";

export function renderMetricCard(metric: MetricItem): string {
    return `<div class="card"><h3>${metric.label}</h3><p>${metric.value} (${metric.trend})</p></div>`;
}

export class MetricCardComponent {
    constructor(private metric: MetricItem) {}

    public render(): string {
        return renderMetricCard(this.metric);
    }
}

